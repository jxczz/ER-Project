import math
import random
import salabim as sim
import config as cfg
from simulation.diagnostic_test import DiagnosticTest
from simulation.hospital import InpatientStay

Z75 = 0.6744897501960817  # 75th percentile of the standard normal


def lognormal_params_from_median_iqr(median, q1, q3):
    """Return (mu, sigma) of the lognormal with this median whose log-scale IQR width matches q1..q3."""
    if not all(math.isfinite(value) for value in (median, q1, q3)) or not 0 < q1 < median < q3:
        raise ValueError(f"need finite 0 < q1 < median < q3, got q1={q1}, median={median}, q3={q3}")
    return math.log(median), (math.log(q3) - math.log(q1)) / (2 * Z75)


INITIAL_TEST_COMBINATIONS = {"none": [], "lab": ["lab"], "imaging": ["imaging"], "both": ["lab", "imaging"]}


def draw_initial_test_types(esi):
    """Draw this ESI level's initial tests as one combination from INITIAL_TEST_WEIGHTS."""
    weights = cfg.INITIAL_TEST_WEIGHTS[esi]
    combination = random.choices(list(weights), weights=list(weights.values()), k=1)[0]
    return list(INITIAL_TEST_COMBINATIONS[combination])


class Patient(sim.Component):
    def setup(self, staff, beds, testing, hospital_beds, metrics, esi=None, data=None, ambulance=False):
        # One staff pool per stage, keyed by stage name.
        self.staff = staff
        self.beds = beds
        self.testing = testing
        self.hospital_beds = hospital_beds
        self.metrics = metrics

        # If esi is provided explicitly, use it. Otherwise, assign ESI now.
        self.esi = esi
        self.data = data
        # Ambulance arrivals skip triage and go straight to the ED bed queue.
        self.ambulance = ambulance
        self.door_to_provider = None
        self.length_of_visit = None
        self.tests = []
        self.diagnosis_attempts = 0
        self.forced_admission = False
        self.admitted = None
        # "discharged", "admitted" (got a hospital bed) or "blocked" (no bed in time; discharged).
        self.outcome = None
        self.inpatient_stay = None
        self._assign_esi()

    def _safe_move(self, new_queue):
        # Safely transition this patient to a new animation queue.
        old_queue = self._anim_queue
        if new_queue is old_queue:
            return

        # Attempt to enter the new queue first.
        entered_new = False
        if new_queue is None:
            entered_new = True
        else:
            if new_queue in self._qmembers:
                entered_new = True
            else:
                try:
                    self.enter(new_queue)
                except Exception:
                    pass
                entered_new = new_queue in self._qmembers

        # Only once we've entered the new queue, leave the old one.
        if entered_new and old_queue is not None and old_queue in self._qmembers:
            try:
                self.leave(old_queue)
            except Exception:
                pass

        if entered_new:
            self._anim_queue = new_queue

    def _assign_esi(self):
        """Assign acuity at patient creation from this arrival mode's mix, preserving an explicit ESI."""
        if self.esi is None:
            if self.data is not None:
                esi_levels, esi_weights, _hourly_arrival_rates, _wait_times_by_esi, _lengths_of_visit_by_esi, arrival_modes = self.data
                if cfg.AMBULANCE_ARRIVALS and arrival_modes is not None:
                    esi_weights = arrival_modes["esi_weights"]["ambulance" if self.ambulance else "walk_in"]
                self.esi = random.choices(esi_levels, weights=esi_weights, k=1)[0]
            else:
                # Fallback
                weights = cfg.ESI_WEIGHTS_AMBULANCE_SYNTHETIC if self.ambulance else cfg.ESI_WEIGHTS_SYNTHETIC
                self.esi = random.choices([1, 2, 3, 4, 5], weights=weights, k=1)[0]

    def animation_objects(self, *args, **kwargs):
        # Salabim passes `screen_coordinates` from AnimateQueue.
        screen_coordinates = kwargs.get("screen_coordinates", True)

        def fill(_t=None):
            if getattr(self, "esi", None) is None:
                return "#d0d0d0"
            return {
                1: "#e53935",  # red
                2: "#fb8c00",  # orange
                3: "#fdd835",  # yellow/gold
                4: "#81d4fa",  # light blue
                5: "#a5d6a7",  # light green
            }.get(self.esi, "#d0d0d0")

        def label(_t=None):
            if getattr(self, "esi", None) is None:
                return "?"
            # "A" marks an ambulance arrival.
            return f"A{self.esi}" if self.ambulance else str(self.esi)

        rect_kwargs = dict(
            spec=(-13, -8, 13, 8),
            fillcolor=fill,
            linecolor="black",
            linewidth=1,
            text=label,
            textcolor="black",
            # Let Salabim decide whether these are screen/world coordinates for this queue.
            screen_coordinates=screen_coordinates,
        )
        try:
            ao0 = sim.AnimateRectangle(**rect_kwargs)
        except TypeError:
            # Fallback for versions that don't accept screen_coordinates because Salabim is weird.
            rect_kwargs.pop("screen_coordinates", None)
            ao0 = sim.AnimateRectangle(**rect_kwargs)
        return (28, 18, ao0)

    def _enter_stage(self, queue):
        # Move to the stage's animation queue, with an optional pause so the move is visible.
        self._safe_move(queue)
        if getattr(cfg, "ANIMATE", False):
            yield self.hold(getattr(cfg, "ANIMATION_STAGE_PAUSE", 0))

    def _acquire(self, stage, wait_queue, resource, priority=0):
        # Request a resource and record how long this stage's queue took.
        # Lower priority value = served first, so ESI 1 (most critical) wins.
        yield from self._enter_stage(wait_queue)
        requested_at = self.env.now()
        yield self.request(resource, request_priority=priority)
        self.metrics.record_stage_wait(stage, self.esi, self.env.now() - requested_at)

    def _order_tests(self, follow_up=False):
        """Order tests and return them. Each is its own component, linked to this patient, that starts immediately."""
        if not follow_up:
            ordered = draw_initial_test_types(self.esi)
        else:
            chances = {test_type: spec["follow_up_probability"][self.esi] for test_type, spec in cfg.TEST_TYPES.items()}
            ordered = [test_type for test_type, chance in chances.items() if random.random() < chance]
            if not ordered:
                # A failed diagnosis always gets at least one follow-up test, picked by the follow-up chances.
                weights = list(chances.values()) if sum(chances.values()) > 0 else None
                ordered = random.choices(list(chances), weights=weights, k=1)

        new_tests = [
            DiagnosticTest(
                patient=self,
                test_type=test_type,
                resource=self.testing[test_type],
                follow_up=follow_up,
                env=self.env,
            )
            for test_type in ordered
        ]
        self.tests.extend(new_tests)
        return new_tests

    def process(self):
        # Measure wait from the moment the patient enters the ER.
        arrival_time = self.env.now()
        self.metrics.record_arrival(self)
        self._anim_queue = None

        # TRIAGE (triage staff). FIFO: acuity is not known until triage is done. Ambulance arrivals
        # skip triage and go straight to the bed queue.
        if not self.ambulance:
            yield from self._acquire("triage", self.env.q_wait_triage, self.staff["triage"])
            yield from self._enter_stage(self.env.q_in_triage)
            mu, sigma = lognormal_params_from_median_iqr(cfg.TRIAGE_TIME_MEDIAN, *cfg.TRIAGE_TIME_IQR)
            yield self.hold(random.lognormvariate(mu, sigma))
            self.release(self.staff["triage"])

        # BED. Held from here until discharge/transfer; every later stage happens in the bed.
        yield from self._acquire("bed", self.env.q_wait_bed, self.beds, self.esi)

        # EXAMINATION (examining provider)
        yield from self._acquire("exam", self.env.q_wait_exam, self.staff["exam"], self.esi)
        yield from self._enter_stage(self.env.q_in_exam)
        # Door-to-provider time.
        self.door_to_provider = self.env.now() - arrival_time
        self.metrics.record_wait_time(self.esi, self.door_to_provider)
        yield self.hold(random.expovariate(1.0 / cfg.MEAN_EXAM_TIME))
        self.release(self.staff["exam"])

        # TESTING AND DIAGNOSIS. Initial tests are ordered as the exam finishes. A failed diagnosis
        # orders follow-up tests and diagnoses again, up to MAX_DIAGNOSIS_ATTEMPTS; if the last
        # attempt also fails, the patient will be admitted.
        follow_up = False
        while True:
            # TESTING. The patient waits in bed for all results of this round.
            tests = self._order_tests(follow_up)
            if tests:
                yield from self._enter_stage(self.env.q_testing)
                ordered_at = self.env.now()
                yield self.wait(*[test.done for test in tests], all=True)
                stage = "follow-up results" if follow_up else "test results"
                self.metrics.record_stage_wait(stage, self.esi, self.env.now() - ordered_at)

            # DIAGNOSIS (diagnosing provider). Requires the bed and all test results.
            yield from self._acquire("diagnosis", self.env.q_wait_diagnosis, self.staff["diagnosis"], self.esi)
            yield from self._enter_stage(self.env.q_in_diagnosis)
            yield self.hold(random.expovariate(1.0 / cfg.MEAN_DIAGNOSIS_TIME))
            self.release(self.staff["diagnosis"])
            self.diagnosis_attempts += 1

            if random.random() >= cfg.DIAGNOSIS_FAILURE_PROBABILITY[self.esi]:
                break
            self.metrics.record_failed_diagnosis(self.esi)
            if self.diagnosis_attempts >= cfg.MAX_DIAGNOSIS_ATTEMPTS:
                self.forced_admission = True
                break
            follow_up = True

        # TREATMENT (treatment staff), if needed.
        if random.random() < cfg.TREATMENT_PROBABILITY[self.esi]:
            yield from self._acquire("treatment", self.env.q_wait_treatment, self.staff["treatment"], self.esi)
            yield from self._enter_stage(self.env.q_in_treatment)
            yield self.hold(random.expovariate(1.0 / cfg.MEAN_TREATMENT_TIME[self.esi]))
            self.release(self.staff["treatment"])

        # DISPOSITION. Admitted patients board in their ED bed, waiting up to MAX_BOARDING_WAIT for a
        # main-hospital bed (sicker patients first). The InpatientStay holds that bed from the moment
        # it is found, so it stays reserved through the handoff and for the hospital stay.
        self.admitted = self.forced_admission or random.random() < cfg.ADMIT_PROBABILITY[self.esi]
        self.outcome = "discharged"
        if self.admitted:
            yield from self._enter_stage(self.env.q_boarding)
            boarding_start = self.env.now()
            self.inpatient_stay = InpatientStay(
                hospital_beds=self.hospital_beds,
                metrics=self.metrics,
                max_wait=cfg.MAX_BOARDING_WAIT,
                request_priority=self.esi,
                from_ed=True,
                env=self.env,
            )
            yield self.wait(self.inpatient_stay.decided)
            self.metrics.record_stage_wait("boarding", self.esi, self.env.now() - boarding_start)
            self.outcome = "admitted" if self.inpatient_stay.got_bed else "blocked"

        if self.outcome == "blocked":
            # EXTRA TREATMENT (own staff pool): no hospital bed came free, so treat further and discharge.
            yield from self._acquire("extra treatment", self.env.q_wait_extended, self.staff["extra_treatment"], self.esi)
            yield from self._enter_stage(self.env.q_in_extended)
            yield self.hold(random.expovariate(1.0 / cfg.MEAN_EXTENDED_TREATMENT_TIME[self.esi]))
            self.release(self.staff["extra_treatment"])

        # Discharge/transfer staff: a transfer handoff if admitted, otherwise a discharge.
        yield from self._acquire("disposition", self.env.q_wait_disposition, self.staff["disposition"], self.esi)
        yield from self._enter_stage(self.env.q_in_disposition)
        mean_time = cfg.MEAN_TRANSFER_TIME if self.outcome == "admitted" else cfg.MEAN_DISCHARGE_TIME
        yield self.hold(random.expovariate(1.0 / mean_time))
        self.release(self.staff["disposition"])
        self.release(self.beds)

        # Leave the last queue so the box disappears properly
        self._safe_move(None)
        self.length_of_visit = self.env.now() - arrival_time
        self.metrics.record_length_of_visit(self.esi, self.length_of_visit)
        self.metrics.record_disposition(self.esi, self.outcome, self.forced_admission)
        self.metrics.record_departure(self)
