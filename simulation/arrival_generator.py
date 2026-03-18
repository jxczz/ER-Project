import salabim as sim
import random
from config import MEAN_ARRIVAL_TIME
from simulation.patient import Patient

class ArrivalGenerator(sim.Component):
    def setup(self, triage, providers, beds, metrics):
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics

    def process(self):
        while True: # infinite loop to keep generating patients
            esi_level = random.randint(1, 5) # randomly assigns an ESI level between 1 and 5 to the arriving patient

            Patient(
                triage = self.triage,
                providers = self.providers,
                beds = self.beds,
                metrics = self.metrics,
                esi = esi_level
            )

            yield self.hold(random.expovariate(1.0 / MEAN_ARRIVAL_TIME)) # wait until next patient arrival using exponential distribution