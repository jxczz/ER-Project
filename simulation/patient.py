import salabim as sim
import random
from config import MEAN_TRIAGE_TIME, MEAN_SERVICE_TIME

class Patient(sim.Component):
    def setup(self, triage, providers, beds, metrics, esi):
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics
        self.esi = esi

    def process(self):
        arrival_time = self.env.now() # records the time when patient arrives

        yield self.request(self.triage) # patient requests triage nurse (if busy -> patient waits in queue)
        yield self.hold(random.expovariate(1.0 / MEAN_TRIAGE_TIME)) # simulates triage time using exponential distribution
        self.release(self.triage) # patient releases triage nurse

        yield self.request(self.providers, priority=self.esi) # patient requests provider with priority based on ESI level
        wait_time = self.env.now() - arrival_time # calculates wait time from arrival to being seen by provider
        self.metrics.record_wait_time(self.esi, wait_time) # records wait time for this patient

        yield self.hold(random.expovariate(1.0 / MEAN_SERVICE_TIME)) # simulates service time using exponential distribution
        self.release(self.providers) # patient releases provider

        yield self.request(self.beds) # patient requests a bed
        yield self.hold(10) # placeholder value for time spent in bed, can be adjusted based on actual data
        self.release(self.beds) # patient releases bed
        