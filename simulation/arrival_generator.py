import salabim as sim
import random
from config import MEAN_ARRIVAL_TIME, MEAN_SERVICE_TIME
from simulation.patient import Patient


class ArrivalGenerator(sim.Component):
    def setup(self, triage, providers, beds, metrics, data=None):
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics
        self.data = data

    def process(self):
        while True:
            if self.data is not None:
                esi_levels, esi_weights, interarrival_times, service_times_by_esi = self.data

                esi_level = random.choices(
                    population=esi_levels,
                    weights=esi_weights,
                    k=1
                )[0]

                if esi_level in service_times_by_esi and service_times_by_esi[esi_level]:
                    sampled_service_time = random.choice(service_times_by_esi[esi_level])
                else:
                    sampled_service_time = MEAN_SERVICE_TIME

                if interarrival_times:
                    sampled_interarrival = random.choice(interarrival_times)
                else:
                    sampled_interarrival = MEAN_ARRIVAL_TIME

            else:
                esi_level = random.choices(
                    [1, 2, 3, 4, 5],
                    weights=[1, 2, 3, 4, 5],
                    k=1
                )[0]

                sampled_service_time = random.expovariate(1.0 / MEAN_SERVICE_TIME)
                sampled_interarrival = random.expovariate(1.0 / MEAN_ARRIVAL_TIME)

            Patient(
                triage=self.triage,
                providers=self.providers,
                beds=self.beds,
                metrics=self.metrics,
                esi=esi_level,
                service_time=sampled_service_time
            )

            yield self.hold(sampled_interarrival)
