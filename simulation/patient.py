import random
import salabim as sim
import config as cfg
from simulation.diagnostic_test import DiagnosticTest


class Patient(sim.Component):
    def setup(self, nurses, providers, beds, testing, metrics, esi=None, data=None):
        self.nurses = nurses
        self.providers = providers
        self.beds = beds
        self.testing = testing
        self.metrics = metrics

        # If esi is provided explicitly, use it. Otherwise, assign ESI now.
        self.esi = esi
        self.data = data
        self.tests = []
        self.admitted = None
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
        """Assign acuity at patient creation, preserving an explicit ESI."""
        if self.esi is None:
            if self.data is not None:
                esi_levels, esi_weights, _hourly_arrival_rates, _wait_times_by_esi, _lengths_of_visit_by_esi = self.data
                self.esi = random.choices(esi_levels, weights=esi_weights, k=1)[0]
            else:
                # Fallback
                self.esi = random.choices([1, 2, 3, 4, 5], weights=cfg.ESI_WEIGHTS_SYNTHETIC, k=1)[0]

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
            return str(self.esi)

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

    def _priority(self, ranks, task, by_esi=True):
        # Lower value = served first: task rank first, then ESI (ESI 1 most critical) within a task.
        return ranks[task] * 10 + (self.esi if by_esi else 0)

    def _acquire(self, stage, wait_queue, resource, priority):
        # Request a resource and record how long this stage's queue took.
        yield from self._enter_stage(wait_queue)
        requested_at = self.env.now()
        yield self.request(resource, request_priority=priority)
        self.metrics.record_stage_wait(stage, self.esi, self.env.now() - requested_at)

    def _order_tests(self):
        # Each ordered test is its own component, linked to this patient, that starts immediately.
        for test_type, spec in cfg.TEST_TYPES.items():
            if random.random() < spec["probability"][self.esi]:
                self.tests.append(
                    DiagnosticTest(
                        patient=self,
                        test_type=test_type,
                        resource=self.testing[test_type],
                        env=self.env,
                    )
                )

    def process(self):
        # Measure wait from the moment the patient enters the ER.
        arrival_time = self.env.now()
        self._anim_queue = None

        # TRIAGE (nurse). FIFO among triage requests: acuity is not known until triage is done.
        yield from self._acquire(
            "triage", self.env.q_wait_triage, self.nurses,
            self._priority(cfg.NURSE_TASK_RANK, "triage", by_esi=False),
        )
        yield from self._enter_stage(self.env.q_in_triage)
        yield self.hold(random.expovariate(1.0 / cfg.MEAN_TRIAGE_TIME))
        self.release(self.nurses)

        # BED. Held from here until discharge/transfer; every later stage happens in the bed.
        # Lower priority value = served first, so ESI 1 (most critical) wins.
        yield from self._acquire("bed", self.env.q_wait_bed, self.beds, self.esi)

        # EXAMINATION (provider)
        yield from self._acquire(
            "exam", self.env.q_wait_exam, self.providers,
            self._priority(cfg.PROVIDER_TASK_RANK, "exam"),
        )
        yield from self._enter_stage(self.env.q_in_exam)
        # Door-to-provider time.
        self.metrics.record_wait_time(self.esi, self.env.now() - arrival_time)
        yield self.hold(random.expovariate(1.0 / cfg.MEAN_EXAM_TIME))
        self.release(self.providers)

        # TESTING. Tests are ordered as the exam finishes; the patient waits in bed for all results.
        self._order_tests()
        if self.tests:
            yield from self._enter_stage(self.env.q_testing)
            ordered_at = self.env.now()
            yield self.wait(*[test.done for test in self.tests], all=True)
            self.metrics.record_stage_wait("test results", self.esi, self.env.now() - ordered_at)

        # DIAGNOSIS (provider). Requires the bed and all test results.
        yield from self._acquire(
            "diagnosis", self.env.q_wait_diagnosis, self.providers,
            self._priority(cfg.PROVIDER_TASK_RANK, "diagnosis"),
        )
        yield from self._enter_stage(self.env.q_in_diagnosis)
        yield self.hold(random.expovariate(1.0 / cfg.MEAN_DIAGNOSIS_TIME))
        self.release(self.providers)

        # TREATMENT (nurse), if needed.
        if random.random() < cfg.TREATMENT_PROBABILITY[self.esi]:
            yield from self._acquire(
                "treatment", self.env.q_wait_treatment, self.nurses,
                self._priority(cfg.NURSE_TASK_RANK, "treatment"),
            )
            yield from self._enter_stage(self.env.q_in_treatment)
            yield self.hold(random.expovariate(1.0 / cfg.MEAN_TREATMENT_TIME[self.esi]))
            self.release(self.nurses)

        # DISPOSITION. Admitted/transferred patients board in their ED bed first.
        self.admitted = random.random() < cfg.ADMIT_PROBABILITY[self.esi]
        if self.admitted:
            yield from self._enter_stage(self.env.q_boarding)
            yield self.hold(random.expovariate(1.0 / cfg.MEAN_BOARDING_TIME))
        yield from self._acquire(
            "disposition", self.env.q_wait_disposition, self.nurses,
            self._priority(cfg.NURSE_TASK_RANK, "disposition"),
        )
        yield from self._enter_stage(self.env.q_in_disposition)
        mean_time = cfg.MEAN_TRANSFER_TIME if self.admitted else cfg.MEAN_DISCHARGE_TIME
        yield self.hold(random.expovariate(1.0 / mean_time))
        self.release(self.nurses)
        self.release(self.beds)

        # Leave the last queue so the box disappears properly
        self._safe_move(None)
        length_of_visit = self.env.now() - arrival_time
        self.metrics.record_length_of_visit(self.esi, length_of_visit)
        self.metrics.record_disposition(self.esi, self.admitted)
