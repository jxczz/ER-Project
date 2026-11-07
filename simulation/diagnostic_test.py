import random
import salabim as sim
import config as cfg


class DiagnosticTest(sim.Component):
    """
    A single test (e.g. lab or CT) ordered for one patient.

    Each test queues for its own testing resource independently of the patient, so a patient can
    have several tests running in parallel while they keep holding their bed. When the test
    finishes it sets `done`, which the linked patient waits on. Follow-up tests (ordered after a
    failed diagnosis) use the same pools and are only reported separately.

    A test type's spec gives either "mean_time" (an exponential hold of the pool) or lognormal
    "room_time" and "report_delay" medians and IQRs: the pool is held for the room time, then the
    result is ready after the report delay, which holds no resource.
    """

    def setup(self, patient, test_type, resource, follow_up=False):
        self.patient = patient
        self.test_type = test_type
        self.resource = resource
        self.follow_up = follow_up
        self.done = sim.State(f"{test_type}_done", value=False, env=self.env)

    def process(self):
        requested_at = self.env.now()
        # Sicker patients' tests are run first.
        yield self.request(self.resource, request_priority=self.patient.esi)
        stage = f"{'follow-up ' if self.follow_up else ''}{self.test_type} queue"
        self.patient.metrics.record_stage_wait(stage, self.patient.esi, self.env.now() - requested_at)
        spec = cfg.TEST_TYPES[self.test_type]
        if "mean_time" in spec:
            yield self.hold(random.expovariate(1.0 / spec["mean_time"]))
            self.release(self.resource)
        else:
            # Imported here: simulation.patient imports this module.
            from simulation.patient import lognormal_params_from_median_iqr

            yield self.hold(random.lognormvariate(*lognormal_params_from_median_iqr(
                spec["room_time_median"], *spec["room_time_iqr"])))
            self.release(self.resource)
            yield self.hold(random.lognormvariate(*lognormal_params_from_median_iqr(
                spec["report_delay_median"], *spec["report_delay_iqr"])))
        self.done.set(True)
