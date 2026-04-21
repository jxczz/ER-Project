import salabim as sim
import random
from config import MEAN_ARRIVAL_TIME
from simulation.patient import Patient


class ArrivalGenerator(sim.Component):
    def setup(self, triage, providers, beds, metrics, data=None):
        # Shared resources and metrics sink that each Patient will use.
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics
        self.data = data

    def process(self):
        # Keep generating patients over time until the environment stops.
        while True:
            if self.data is not None:
                # ArrivalGenerator only controls *when* patients arrive.
                # ESI assignment happens during triage inside Patient.
                _esi_levels, _esi_weights, interarrival_times, _service_times_by_esi = self.data
                sampled_interarrival = random.choice(interarrival_times) if interarrival_times else MEAN_ARRIVAL_TIME

            else:
                # Synthetic mode: exponential interarrival times around the configured mean.
                sampled_interarrival = random.expovariate(1.0 / MEAN_ARRIVAL_TIME)

            # Create a Patient component.
            Patient(
                triage=self.triage,
                providers=self.providers,
                beds=self.beds,
                metrics=self.metrics,
                data=self.data
            )

            # Wait until the next patient arrives (simulated time).
            yield self.hold(sampled_interarrival)
