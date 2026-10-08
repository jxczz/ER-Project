import unittest
from unittest.mock import patch

import salabim as sim

import config as cfg
import simulation.environment as environment
from simulation.esi import ESI
from simulation.patient import Patient

# Deterministic durations: random.expovariate(1 / mean) is patched to return the mean exactly.
# Powers of two keep the timeline arithmetic exact.
MEANS = dict(
    MEAN_TRIAGE_TIME=1,
    MEAN_EXAM_TIME=2,
    MEAN_DIAGNOSIS_TIME=1,
    MEAN_TREATMENT_TIME={level: 2 for level in range(1, 6)},
    MEAN_BOARDING_TIME=16,
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
            "lab": {"capacity": 1, "mean_time": LAB_TIME, "probability": dict.fromkeys(range(1, 6), 0)},
            "imaging": {"capacity": 1, "mean_time": IMAGING_TIME, "probability": dict.fromkeys(range(1, 6), 0)},
        }
        self.patch(patch.multiple(
            cfg,
            ANIMATE=False,
            TEST_TYPES=self.test_types,
            TREATMENT_PROBABILITY=dict.fromkeys(range(1, 6), 0),
            ADMIT_PROBABILITY=dict.fromkeys(range(1, 6), 0),
            **MEANS,
        ))
        self.patch(patch("simulation.patient.random.expovariate", side_effect=lambda rate: 1 / rate))

        with patch.multiple(
            environment, NUM_NURSES=1, NUM_PROVIDERS=1, NUM_BEDS=1, TEST_TYPES=self.test_types
        ):
            self.env, self.nurses, self.providers, self.beds, self.testing = environment.create_environment()
        self.metrics = ESI()
        self.animation_queues = [getattr(self.env, name) for name, _label in environment.STAGE_QUEUES]

    def patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, tests=(), treatment=False, admit=False):
        for test_type in tests:
            self.test_types[test_type]["probability"] = dict.fromkeys(range(1, 6), 1)
        cfg.TREATMENT_PROBABILITY = dict.fromkeys(range(1, 6), 1 if treatment else 0)
        cfg.ADMIT_PROBABILITY = dict.fromkeys(range(1, 6), 1 if admit else 0)

    def patient(self, esi=3, **kwargs):
        return Patient(
            nurses=self.nurses,
            providers=self.providers,
            beds=self.beds,
            testing=self.testing,
            metrics=self.metrics,
            esi=esi,
            env=self.env,
            **kwargs,
        )

    def block(self, resource, duration, **kwargs):
        return ResourceHolder(
            held_resource=resource, hold_duration=duration, env=self.env, **kwargs
        )

    def assert_claims(self, patient, *, nurse=False, provider=False, bed=False):
        self.assertEqual(patient in self.nurses.claimers(), nurse)
        self.assertEqual(patient in self.providers.claimers(), provider)
        self.assertEqual(patient in self.beds.claimers(), bed)

    def assert_departed(self, *patients):
        for patient in patients:
            self.assertIsNone(patient._anim_queue)
            self.assertEqual(patient.queues(), set())
        resources = [self.nurses, self.providers, self.beds, *self.testing.values()]
        for resource in resources:
            self.assertEqual(resource.claimed_quantity(), 0)
            self.assertEqual(len(resource.requesters()), 0)
            self.assertEqual(len(resource.claimers()), 0)
        for queue in self.animation_queues:
            self.assertEqual(len(queue), 0)

    def test_full_path_with_parallel_tests_treatment_and_admission(self):
        self.route(tests=("lab", "imaging"), treatment=True, admit=True)
        patient = self.patient()

        # Triage 0-1 (nurse), bed at 1, exam 1-3 (provider).
        self.env.run(till=0.5)
        self.assert_claims(patient, nurse=True)
        self.env.run(till=2)
        self.assert_claims(patient, provider=True, bed=True)
        self.assertEqual(self.metrics.wait_times[3], [1.0])

        # Tests ordered at 3 run in parallel on their own pools: lab 3-7, imaging 3-11.
        self.env.run(till=5)
        self.assertEqual([t.test_type for t in patient.tests], ["lab", "imaging"])
        self.assertTrue(all(t.patient is patient for t in patient.tests))
        self.assertIn(patient.tests[0], self.testing["lab"].claimers())
        self.assertIn(patient.tests[1], self.testing["imaging"].claimers())
        # Bed held during testing; the free provider is not requested until results are back.
        self.assert_claims(patient, bed=True)
        self.assertNotIn(patient, self.providers.requesters())
        self.assertIn(patient, self.env.q_testing)
        self.env.run(till=8)
        self.assertTrue(patient.tests[0].done())
        self.assertFalse(patient.tests[1].done())
        self.assert_claims(patient, bed=True)

        # Diagnosis 11-12 (provider), treatment 12-14 (nurse).
        self.env.run(till=11.5)
        self.assert_claims(patient, provider=True, bed=True)
        self.env.run(till=13)
        self.assert_claims(patient, nurse=True, bed=True)
        self.assertIn(patient, self.env.q_in_treatment)

        # Boarding 14-30 holds only the bed; transfer 30-32 (nurse).
        self.env.run(till=20)
        self.assert_claims(patient, bed=True)
        self.assertIn(patient, self.env.q_boarding)
        self.env.run(till=31)
        self.assert_claims(patient, nurse=True, bed=True)
        self.assertEqual(self.metrics.lengths_of_visit[3], [])

        self.env.run(till=32.1)
        self.assertEqual(self.metrics.lengths_of_visit[3], [32.0])
        self.assertEqual(self.metrics.stage_waits["test results"][3], [8.0])
        self.assertEqual(self.metrics.dispositions[3], {"discharged": 0, "admitted": 1})
        self.assert_departed(patient)

    def test_minimal_path_skips_optional_steps(self):
        patient = self.patient(esi=4)

        # Triage 0-1, exam 1-3, diagnosis 3-4, discharge 4-5.
        self.env.run(till=3.5)
        self.assert_claims(patient, provider=True, bed=True)
        self.assertIn(patient, self.env.q_in_diagnosis)
        self.env.run(till=4.5)
        self.assert_claims(patient, nurse=True, bed=True)
        self.assertIn(patient, self.env.q_in_disposition)

        self.env.run(till=6)
        self.assertEqual(patient.tests, [])
        self.assertEqual(self.metrics.lengths_of_visit[4], [5.0])
        self.assertNotIn("test results", self.metrics.stage_waits)
        self.assertNotIn("treatment", self.metrics.stage_waits)
        self.assertEqual(self.metrics.dispositions[4], {"discharged": 1, "admitted": 0})
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
        self.assert_claims(later, provider=True, bed=True)
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
        self.assert_claims(earlier, provider=True, bed=True)
        self.assert_claims(later)

        self.env.run(till=30)
        self.assertEqual(self.metrics.wait_times[2], [8.0, 11.5])
        self.assert_departed(earlier, later)

    def test_existing_lower_acuity_bed_occupant_is_not_preempted(self):
        occupant = self.patient(esi=5)
        waiting = self.patient(esi=1, at=0.5)

        # Occupant: triage 0-1, bed 1-5. Waiting: triage 1-2, then waits for the bed.
        self.env.run(till=3)
        self.assert_claims(occupant, provider=True, bed=True)
        self.assertIn(waiting, self.beds.requesters())
        self.env.run(till=5.1)
        self.assert_claims(waiting, provider=True, bed=True)

        self.env.run(till=15)
        self.assertEqual(self.metrics.wait_times[1], [4.5])
        self.assert_departed(occupant, waiting)

    def test_nurse_serves_disposition_before_earlier_triage_request(self):
        self.beds.set_capacity(2)
        finishing = self.patient(esi=5)
        # Finishing patient: triage 0-1, exam 1-3, diagnosis 3-4, then needs a nurse at 4.
        self.block(self.nurses, 2.5, at=3.5)  # nurse busy 3.5-6
        arriving = self.patient(esi=1, at=3.6)  # requests triage at 3.6, before the disposition request

        self.env.run(till=5)
        self.assertIn(arriving, self.nurses.requesters())
        self.assertIn(finishing, self.nurses.requesters())
        self.env.run(till=6.1)
        self.assert_claims(finishing, nurse=True, bed=True)
        self.assertIn(arriving, self.nurses.requesters())

        self.env.run(till=30)
        self.assert_departed(finishing, arriving)

    def test_provider_serves_diagnosis_before_earlier_exam_request(self):
        self.beds.set_capacity(2)
        diagnosing = self.patient(esi=5)
        # Exam request at 2 by a sicker patient, while the first is still in their exam until 3.
        examining = self.patient(esi=1, at=0.5)
        self.block(self.providers, 7, at=2.9)  # takes the provider at 3, until 10

        self.env.run(till=5)
        self.assertIn(examining, self.providers.requesters())
        self.assertIn(diagnosing, self.providers.requesters())
        self.env.run(till=10.1)
        self.assert_claims(diagnosing, provider=True, bed=True)
        self.assertIn(examining, self.providers.requesters())

        self.env.run(till=30)
        self.assert_departed(diagnosing, examining)

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
