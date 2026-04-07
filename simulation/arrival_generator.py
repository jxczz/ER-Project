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
            esi_level = random.choices(
                [1, 2, 3, 4, 5],
                weights=[1, 2, 3, 4, 5], # example distribution of ESI levels, can be adjusted based on actual data
            )[0]

            Patient(
                triage = self.triage,
                providers = self.providers,
                beds = self.beds,
                metrics = self.metrics,
                esi = esi_level
            )

            yield self.hold(random.expovariate(1.0 / MEAN_ARRIVAL_TIME)) # wait until next patient arrival using exponential distribution