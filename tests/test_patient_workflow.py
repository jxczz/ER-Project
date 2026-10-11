import unittest
from unittest.mock import patch

import salabim as sim

import config as cfg
import simulation.environment as environment
from simulation.esi import ESI
from simulation.hospital import HospitalCensus, InpatientStay, next_discharge_time
from simulation.patient import Patient

# Deterministic durations: random.expovariate(1 / mean) is patched to return the mean exactly.
# Powers of two keep the timeline arithmetic exact.
MEANS = dict(
    MEAN_TRIAGE_TIME=1,
    MEAN_EXAM_TIME=2,
    MEAN_DIAGNOSIS_TIME=1,
    MEAN_TREATMENT_TIME={level: 2 for level in range(1, 6)},
    MEAN_EXTENDED_TREATMENT_TIME={level: 4 for level in range(1, 6)},
    MEAN_HOSPITAL_STAY=64,
    MEAN_DISCHARGE_TIME=1,
    MEAN_TRANSFER_TIME=2,
)
LAB_TIME = 4
IMAGING_TIME = 8


class ResourceHolder(sim.Component):
    def setup(self, held_resource, hold_duration, request_priority=-1):
        self.held_resource = held_resource
        self.hold_duration = hold_duration
        self.request_priority = request_priority

    def process(self):
        yield self.request(self.held_resource, request_priority=self.request_priority)
        yield self.hold(self.hold_duration)
        self.release(self.held_resource)


class PatientWorkflowTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

        # By default every optional step is skipped; tests opt in with route().
        self.test_types = {
            name: {
                "capacity": 1,
                "mean_time": mean_time,
                "probability": dict.fromkeys(range(1, 6), 0),
                "follow_up_probability": dict.fromkeys(range(1, 6), 0),
            }
            for name, mean_time in (("lab", LAB_TIME), ("imaging", IMAGING_TIME))
        }
        self.patch(patch.multiple(
            cfg,
            ANIMATE=False,
            TEST_TYPES=self.test_types,
            TREATMENT_PROBABILITY=dict.fromkeys(range(1, 6), 0),
            ADMIT_PROBABILITY=dict.fromkeys(range(1, 6), 0),
            DIAGNOSIS_FAILURE_PROBABILITY=dict.fromkeys(range(1, 6), 0),
            MAX_DIAGNOSIS_ATTEMPTS=2,
            MAX_BOARDING_WAIT=8,
            HOSPITAL_DISCHARGE_HOURS=(0, 24),  # hospital patients can leave at any time of day
            **MEANS,
        ))
        self.patch(patch("simulation.patient.random.expovariate", side_effect=lambda rate: 1 / rate))

        staff = dict.fromkeys(("triage", "exam", "diagnosis", "treatment", "extra_treatment", "disposition"), 1)
        with patch.multiple(
            environment, STAFF=staff, NUM_BEDS=1, TEST_TYPES=self.test_types, NUM_HOSPITAL_BEDS=1
        ):
            self.env, self.staff, self.beds, self.testing, self.hospital_beds = environment.create_environment()
        self.metrics = ESI()
        self.animation_queues = [getattr(self.env, name) for name, _label in environment.STAGE_QUEUES]

    def patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, tests=(), treatment=False, admit=False, follow_ups=(), diagnosis_fails=False):
        for test_type in tests:
            self.test_types[test_type]["probability"] = dict.fromkeys(range(1, 6), 1)
        for test_type in follow_ups:
            self.test_types[test_type]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1)
        cfg.TREATMENT_PROBABILITY = dict.fromkeys(range(1, 6), 1 if treatment else 0)
        cfg.ADMIT_PROBABILITY = dict.fromkeys(range(1, 6), 1 if admit else 0)
        self.set_diagnosis_failure(diagnosis_fails)

    def set_diagnosis_failure(self, fails):
        # Read at each diagnosis, so tests can change it mid-run to fail only some attempts.
        cfg.DIAGNOSIS_FAILURE_PROBABILITY = dict.fromkeys(range(1, 6), 1 if fails else 0)

    def patient(self, esi=3, **kwargs):
        return Patient(
            staff=self.staff,
            beds=self.beds,
            testing=self.testing,
            hospital_beds=self.hospital_beds,
            metrics=self.metrics,
            esi=esi,
            env=self.env,
            **kwargs,
        )

    def block(self, resource, duration, **kwargs):
        return ResourceHolder(
            held_resource=resource, hold_duration=duration, env=self.env, **kwargs
        )

    def assert_claims(self, patient, *, staff=None, bed=False):
        # `staff` names the one stage pool the patient should be holding, if any.
        for stage, pool in self.staff.items():
            self.assertEqual(patient in pool.claimers(), stage == staff, stage)
        self.assertEqual(patient in self.beds.claimers(), bed)

    def assert_departed(self, *patients):
        for patient in patients:
            self.assertIsNone(patient._anim_queue)
            self.assertEqual(patient.queues(), set())
        resources = [*self.staff.values(), self.beds, *self.testing.values()]
        for resource in resources:
            self.assertEqual(resource.claimed_quantity(), 0)
            self.assertEqual(len(resource.requesters()), 0)
            self.assertEqual(len(resource.claimers()), 0)
        for queue in self.animation_queues:
            self.assertEqual(len(queue), 0)

    def test_full_path_with_parallel_tests_treatment_and_admission(self):
        self.route(tests=("lab", "imaging"), treatment=True, admit=True)
        self.block(self.hospital_beds, 20)  # the only hospital bed frees up at 20
        patient = self.patient()

        # Triage 0-1 (nurse), bed at 1, exam 1-3 (provider).
        self.env.run(till=0.5)
        self.assert_claims(patient, staff="triage")
        self.env.run(till=2)
        self.assert_claims(patient, staff="exam", bed=True)
        self.assertEqual(self.metrics.wait_times[3], [1.0])

        # Tests ordered at 3 run in parallel on their own pools: lab 3-7, imaging 3-11.
        self.env.run(till=5)
        self.assertEqual([t.test_type for t in patient.tests], ["lab", "imaging"])
        self.assertTrue(all(t.patient is patient for t in patient.tests))
        self.assertIn(patient.tests[0], self.testing["lab"].claimers())
        self.assertIn(patient.tests[1], self.testing["imaging"].claimers())
        # Bed held during testing; the free provider is not requested until results are back.
        self.assert_claims(patient, bed=True)
        self.assertNotIn(patient, self.staff["diagnosis"].requesters())
        self.assertIn(patient, self.env.q_testing)
        self.env.run(till=8)
        self.assertTrue(patient.tests[0].done())
        self.assertFalse(patient.tests[1].done())
        self.assert_claims(patient, bed=True)

        # Diagnosis 11-12 (provider), treatment 12-14 (nurse).
        self.env.run(till=11.5)
        self.assert_claims(patient, staff="diagnosis", bed=True)
        self.env.run(till=13)
        self.assert_claims(patient, staff="treatment", bed=True)
        self.assertIn(patient, self.env.q_in_treatment)

        # Boarding 14-20 holds only the ED bed until the hospital bed frees; transfer 20-22.
        self.env.run(till=17)
        self.assert_claims(patient, bed=True)
        self.assertIn(patient, self.env.q_boarding)
        self.assertIn(patient.inpatient_stay, self.hospital_beds.requesters())
        self.env.run(till=21)
        self.assert_claims(patient, staff="disposition", bed=True)
        self.assertIn(patient.inpatient_stay, self.hospital_beds.claimers())
        self.assertEqual(self.metrics.lengths_of_visit[3], [])

        self.env.run(till=22.1)
        self.assertEqual(self.metrics.lengths_of_visit[3], [22.0])
        self.assertEqual(self.metrics.stage_waits["test results"][3], [8.0])
        self.assertEqual(self.metrics.stage_waits["boarding"][3], [6.0])
        self.assertEqual(patient.outcome, "admitted")
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 0, "admitted": 1, "blocked": 0, "forced": 0})
        self.assert_departed(patient)

        # The hospital stay keeps the bed after the patient leaves the ED, until 20 + 64.
        self.env.run(till=83.9)
        self.assertIn(patient.inpatient_stay, self.hospital_beds.claimers())
        self.env.run(till=84.1)
        self.assertEqual(self.hospital_beds.claimed_quantity(), 0)

    def test_minimal_path_skips_optional_steps(self):
        patient = self.patient(esi=4)

        # Triage 0-1, exam 1-3, diagnosis 3-4, discharge 4-5.
        self.env.run(till=3.5)
        self.assert_claims(patient, staff="diagnosis", bed=True)
        self.assertIn(patient, self.env.q_in_diagnosis)
        self.env.run(till=4.5)
        self.assert_claims(patient, staff="disposition", bed=True)
        self.assertIn(patient, self.env.q_in_disposition)

        self.env.run(till=6)
        self.assertEqual(patient.tests, [])
        self.assertEqual(self.metrics.lengths_of_visit[4], [5.0])
        self.assertNotIn("test results", self.metrics.stage_waits)
        self.assertNotIn("treatment", self.metrics.stage_waits)
        self.assertEqual(self.metrics.dispositions[4], {"discharged": 1, "admitted": 0, "blocked": 0, "forced": 0})
        self.assert_departed(patient)

    def test_later_higher_acuity_bed_request_wins(self):
        occupant = self.block(self.beds, 8)
        earlier = self.patient(esi=5)
        later = self.patient(esi=1, at=0.5)

        # Both are triaged by 2 and waiting for the occupied bed.
        self.env.run(till=3)
        self.assertIn(earlier, self.beds.requesters())
        self.assertIn(later, self.beds.requesters())
        self.assertIn(occupant, self.beds.claimers())

        self.env.run(till=8.1)
        self.assert_claims(later, staff="exam", bed=True)
        self.assertIn(earlier, self.beds.requesters())

        self.env.run(till=30)
        self.assertEqual(self.metrics.wait_times[1], [7.5])
        self.assert_departed(earlier, later)

    def test_equal_acuity_bed_requests_remain_fifo(self):
        self.block(self.beds, 8)
        earlier = self.patient(esi=2)
        later = self.patient(esi=2, at=0.5)

        self.env.run(till=3)
        self.assertIn(earlier, self.beds.requesters())
        self.assertIn(later, self.beds.requesters())
        self.env.run(till=8.1)
        self.assert_claims(earlier, staff="exam", bed=True)
        self.assert_claims(later)

        self.env.run(till=30)
        self.assertEqual(self.metrics.wait_times[2], [8.0, 11.5])
        self.assert_departed(earlier, later)

    def test_existing_lower_acuity_bed_occupant_is_not_preempted(self):
        occupant = self.patient(esi=5)
        waiting = self.patient(esi=1, at=0.5)

        # Occupant: triage 0-1, bed 1-5. Waiting: triage 1-2, then waits for the bed.
        self.env.run(till=2.5)
        self.assert_claims(occupant, staff="exam", bed=True)
        self.assertIn(waiting, self.beds.requesters())
        self.env.run(till=5.1)
        self.assert_claims(waiting, staff="exam", bed=True)

        self.env.run(till=15)
        self.assertEqual(self.metrics.wait_times[1], [4.5])
        self.assert_departed(occupant, waiting)

    def test_busy_later_stage_pools_do_not_hold_up_earlier_stages(self):
        self.beds.set_capacity(2)
        self.block(self.staff["diagnosis"], 20)
        self.block(self.staff["disposition"], 20)
        first = self.patient()
        second = self.patient(at=4)

        # Triage 0-1 and exam 1-3 run on their own pools; diagnosis is busy until 20.
        self.env.run(till=2)
        self.assert_claims(first, staff="exam", bed=True)
        self.env.run(till=4.5)
        self.assertIn(first, self.staff["diagnosis"].requesters())
        self.assert_claims(first, bed=True)
        self.assert_claims(second, staff="triage")
        self.env.run(till=6)
        self.assert_claims(second, staff="exam", bed=True)

        self.env.run(till=20.5)
        self.assert_claims(first, staff="diagnosis", bed=True)
        self.env.run(till=40)
        self.assert_departed(first, second)

    def test_stage_pool_serves_higher_acuity_first(self):
        self.beds.set_capacity(2)
        self.block(self.staff["exam"], 10)
        earlier = self.patient(esi=5)
        later = self.patient(esi=1, at=0.5)

        # Both are triaged and in a bed by 2, then wait for the busy exam pool.
        self.env.run(till=3)
        self.assertIn(earlier, self.staff["exam"].requesters())
        self.assertIn(later, self.staff["exam"].requesters())
        self.env.run(till=10.1)
        self.assert_claims(later, staff="exam", bed=True)
        self.assertIn(earlier, self.staff["exam"].requesters())

        self.env.run(till=30)
        self.assertEqual(self.metrics.wait_times[1], [9.5])
        self.assertEqual(self.metrics.wait_times[5], [12.0])
        self.assert_departed(earlier, later)

    def test_failed_diagnosis_orders_follow_up_tests_then_rediagnoses(self):
        self.route(follow_ups=("imaging",), diagnosis_fails=True)
        patient = self.patient()

        # No initial tests: triage 0-1, exam 1-3, diagnosis 3-4 fails.
        # Follow-up imaging 4-12 (bed held), second diagnosis 12-13 succeeds, discharge 13-14.
        self.env.run(till=5)
        self.set_diagnosis_failure(False)
        self.assertEqual(patient.diagnosis_attempts, 1)
        self.assertEqual([(t.test_type, t.follow_up) for t in patient.tests], [("imaging", True)])
        self.assertIn(patient.tests[0], self.testing["imaging"].claimers())
        self.assert_claims(patient, bed=True)
        self.assertNotIn(patient, self.staff["diagnosis"].requesters())
        self.assertIn(patient, self.env.q_testing)

        self.env.run(till=12.5)
        self.assert_claims(patient, staff="diagnosis", bed=True)
        self.env.run(till=13.5)
        self.assert_claims(patient, staff="disposition", bed=True)

        self.env.run(till=15)
        self.assertEqual(patient.diagnosis_attempts, 2)
        self.assertFalse(patient.forced_admission)
        self.assertEqual(self.metrics.failed_diagnoses[3], 1)
        self.assertEqual(self.metrics.stage_waits["follow-up results"][3], [8.0])
        self.assertIn("follow-up imaging queue", self.metrics.stage_waits)
        self.assertNotIn("test results", self.metrics.stage_waits)
        self.assertEqual(self.metrics.lengths_of_visit[3], [14.0])
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 1, "admitted": 0, "blocked": 0, "forced": 0})
        self.assert_departed(patient)

    def test_failing_last_diagnosis_attempt_forces_admission(self):
        self.route(tests=("lab",), follow_ups=("imaging",), treatment=True, diagnosis_fails=True)
        patient = self.patient()

        # Initial lab 3-7, diagnosis 7-8 fails; follow-up imaging 8-16, diagnosis 16-17 fails.
        # Admitted despite ADMIT_PROBABILITY 0: treatment 17-19, hospital bed free at 19, transfer 19-21.
        self.env.run(till=18)
        self.assertEqual(patient.diagnosis_attempts, 2)
        self.assertTrue(patient.forced_admission)
        self.assert_claims(patient, staff="treatment", bed=True)
        self.env.run(till=20)
        self.assert_claims(patient, staff="disposition", bed=True)

        self.env.run(till=22)
        self.assertEqual([(t.test_type, t.follow_up) for t in patient.tests], [("lab", False), ("imaging", True)])
        self.assertEqual(self.metrics.failed_diagnoses[3], 2)
        self.assertEqual(self.metrics.lengths_of_visit[3], [21.0])
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 0, "admitted": 1, "blocked": 0, "forced": 1})
        self.assert_departed(patient)

    def test_failed_diagnosis_always_orders_a_follow_up(self):
        self.route(diagnosis_fails=True)
        # Imaging is almost never drawn on its own and lab never is, so the guaranteed pick is imaging.
        self.test_types["imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1e-12)
        patient = self.patient()

        self.env.run(till=5)
        self.set_diagnosis_failure(False)
        self.assertEqual([(t.test_type, t.follow_up) for t in patient.tests], [("imaging", True)])

        self.env.run(till=20)
        self.assertEqual(self.metrics.failed_diagnoses[3], 1)
        self.assert_departed(patient)

    def test_boarding_times_out_then_extra_treatment_and_discharge(self):
        self.route(admit=True)
        self.block(self.hospital_beds, 100)
        patient = self.patient()

        # Diagnosis ends at 4; boarding 4-12 gives up after MAX_BOARDING_WAIT (8).
        # Extra treatment 12-16 (its own staff pool), discharge 16-17.
        self.env.run(till=10)
        self.assertIn(patient, self.env.q_boarding)
        self.assertIn(patient.inpatient_stay, self.hospital_beds.requesters())
        self.env.run(till=13)
        self.assert_claims(patient, staff="extra_treatment", bed=True)
        self.assertIn(patient, self.env.q_in_extended)
        self.assertEqual(len(self.hospital_beds.requesters()), 0)
        self.assertFalse(patient.inpatient_stay.got_bed)

        self.env.run(till=18)
        self.assertEqual(patient.outcome, "blocked")
        self.assertEqual(self.metrics.stage_waits["boarding"][3], [8.0])
        self.assertEqual(self.metrics.lengths_of_visit[3], [17.0])
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 0, "admitted": 0, "blocked": 1, "forced": 0})
        self.assert_departed(patient)

    def test_forced_admission_without_hospital_bed_follows_same_rule(self):
        self.route(follow_ups=("imaging",), diagnosis_fails=True)
        self.block(self.hospital_beds, 100)
        patient = self.patient()

        # Diagnosis 3-4 fails, follow-up imaging 4-12, diagnosis 12-13 fails (forced).
        # Boarding 13-21 times out, extra treatment 21-25, discharge 25-26.
        self.env.run(till=27)
        self.assertTrue(patient.forced_admission)
        self.assertEqual(patient.outcome, "blocked")
        self.assertEqual(self.metrics.lengths_of_visit[3], [26.0])
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 0, "admitted": 0, "blocked": 1, "forced": 1})
        self.assert_departed(patient)

    def test_hospital_stays_end_in_discharge_window(self):
        cfg.HOSPITAL_DISCHARGE_HOURS = (11, 17)
        day = 24 * 60
        self.assertEqual(next_discharge_time(12 * 60), 12 * 60)
        self.assertTrue(11 * 60 <= next_discharge_time(3 * 60) < 17 * 60)
        self.assertTrue(day + 11 * 60 <= next_discharge_time(18 * 60) < day + 17 * 60)

        # A stay starting at 0 ends at 64 (01:04), so the bed is released at 11:00-17:00 that day.
        stay = InpatientStay(hospital_beds=self.hospital_beds, metrics=None, env=self.env)
        self.env.run(till=11 * 60 - 1)
        self.assertIn(stay, self.hospital_beds.claimers())
        self.env.run(till=17 * 60)
        self.assertEqual(self.hospital_beds.claimed_quantity(), 0)

    def test_non_ed_patients_are_turned_away_when_hospital_full(self):
        first = InpatientStay(hospital_beds=self.hospital_beds, metrics=self.metrics, env=self.env)
        second = InpatientStay(hospital_beds=self.hospital_beds, metrics=self.metrics, env=self.env)

        self.env.run(till=1)
        self.assertTrue(first.got_bed)
        self.assertFalse(second.got_bed)
        self.assertEqual(len(self.hospital_beds.requesters()), 0)
        self.assertEqual(self.metrics.non_ed_admissions, {"admitted": 1, "turned_away": 1})

    def test_census_fills_initial_occupancy(self):
        self.hospital_beds.set_capacity(10)
        self.patch(patch.multiple(cfg, HOSPITAL_INITIAL_OCCUPANCY=0.5, NON_ED_ADMISSIONS_PER_DAY=0))
        HospitalCensus(hospital_beds=self.hospital_beds, metrics=self.metrics, env=self.env)

        self.env.run(till=1)
        self.assertEqual(self.hospital_beds.claimed_quantity(), 5)
        # Patients already in the hospital at the start are not counted as new admissions.
        self.assertEqual(self.metrics.non_ed_admissions, {"admitted": 0, "turned_away": 0})

    def test_acuity_is_sampled_at_creation_from_data(self):
        data = ([2], [1.0], {}, {2: [999.0]}, {2: [9999.0]})
        with patch("simulation.patient.random.choices", return_value=[2]) as acuity:
            patient = self.patient(esi=None, data=data)
            self.assertEqual(patient.esi, 2)
            acuity.assert_called_once_with([2], weights=[1.0], k=1)

        # Observed NHAMCS times do not supply process durations.
        self.env.run(till=6)
        self.assertEqual(self.metrics.lengths_of_visit[2], [5.0])
        self.assert_departed(patient)


if __name__ == "__main__":
    unittest.main()
