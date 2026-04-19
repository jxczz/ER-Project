import salabim as sim
import random
from config import MEAN_TRIAGE_TIME


class Patient(sim.Component):
    def setup(self, triage, providers, beds, metrics, esi, service_time):
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics
        self.esi = esi
        self.service_time = service_time

    def process(self):
        arrival_time = self.env.now()

        yield self.request(self.triage)
        yield self.hold(random.expovariate(1.0 / MEAN_TRIAGE_TIME))
        self.release(self.triage)

        yield self.request(self.providers, priority=self.esi)
        wait_time = self.env.now() - arrival_time
        self.metrics.record_wait_time(self.esi, wait_time)

        yield self.hold(self.service_time)
        self.release(self.providers)

        yield self.request(self.beds)
        yield self.hold(10)
        self.release(self.beds)
