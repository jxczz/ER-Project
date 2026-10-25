import salabim as sim
import random
import config as cfg
from config import MEAN_ARRIVAL_TIME
from simulation.patient import Patient


class ArrivalGenerator(sim.Component):
    def setup(self, staff, beds, testing, hospital_beds, metrics, data=None):
        # Shared resources and metrics sink that each Patient will use.
        self.staff = staff
        self.beds = beds
        self.testing = testing
        self.hospital_beds = hospital_beds
        self.metrics = metrics
        self.data = data

    def process(self):
        # Keep generating patients over time until the environment stops.
        while True:
            if self.data is not None:
                # ArrivalGenerator only controls *when* patients arrive.
                # ESI assignment happens when the Patient is created.
                _esi_levels, _esi_weights, hourly_arrival_rates, _wait_times_by_esi, _lengths_of_visit_by_esi, _arrival_modes = self.data
                # Simulation time is minutes; time zero is midnight.
                now = self.env.now()
                hour = int(now // 60) % 24
                minutes_to_boundary = 60 - now % 60
                rate = hourly_arrival_rates[hour]
                if rate <= 0:
                    yield self.hold(minutes_to_boundary)
                    continue
                sampled_interarrival = random.expovariate(rate / 60)
                if sampled_interarrival >= minutes_to_boundary:
                    yield self.hold(minutes_to_boundary)
                    continue
                yield self.hold(sampled_interarrival)

            else:
                # Synthetic mode: exponential interarrival times around the configured mean.
                sampled_interarrival = random.expovariate(1.0 / MEAN_ARRIVAL_TIME)

            # Create a Patient component. Skipping the draw when nobody comes by ambulance keeps the random
            # stream, and so the run, identical to before ambulance arrivals were added.
            ambulance_share = self._ambulance_share()
            Patient(
                staff=self.staff,
                beds=self.beds,
                testing=self.testing,
                hospital_beds=self.hospital_beds,
                metrics=self.metrics,
                data=self.data,
                ambulance=ambulance_share > 0 and random.random() < ambulance_share,
            )

            # Preserve the existing timing in synthetic mode.
            if self.data is None:
                yield self.hold(sampled_interarrival)

    def _ambulance_share(self):
        """Chance that the patient arriving now comes by ambulance."""
        if not cfg.AMBULANCE_ARRIVALS:
            return 0
        if self.data is None:
            return cfg.AMBULANCE_SHARE_SYNTHETIC
        arrival_modes = self.data[5]
        if arrival_modes is None:
            return 0
        hour = int(self.env.now() // 60) % 24
        return arrival_modes["ambulance_share_by_hour"][hour]
