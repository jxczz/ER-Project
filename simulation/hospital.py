import random
import salabim as sim
import config as cfg

MINUTES_PER_DAY = 24 * 60


def next_discharge_time(t):
    """
    Earliest time at or after `t` that falls in the daily discharge window (HOSPITAL_DISCHARGE_HOURS).
    Stays that end outside the window leave at a random time in the next window.
    """
    start_hour, end_hour = cfg.HOSPITAL_DISCHARGE_HOURS
    day_start = (t // MINUTES_PER_DAY) * MINUTES_PER_DAY
    minute_of_day = t - day_start
    if start_hour * 60 <= minute_of_day < end_hour * 60:
        return t
    if minute_of_day >= end_hour * 60:
        day_start += MINUTES_PER_DAY
    return day_start + random.uniform(start_hour * 60, end_hour * 60)


class InpatientStay(sim.Component):
    """
    One patient's stay in a main-hospital bed, for ED admissions and non-ED patients alike.

    The stay requests a hospital bed, waiting at most `max_wait` minutes (0 = only if one is free
    now). Once it has a bed it holds it for the length of stay and releases it at the next
    discharge window. `decided` is set once the request succeeds or gives up, and `got_bed` says
    which; an ED patient boarding in the ED waits on `decided`.
    """

    def setup(self, hospital_beds, metrics, max_wait=0, request_priority=0, from_ed=False):
        self.hospital_beds = hospital_beds
        self.metrics = metrics
        self.max_wait = max_wait
        self.request_priority = request_priority
        self.from_ed = from_ed
        self.got_bed = None
        self.decided = sim.State("hospital_bed_decided", value=False, env=self.env)

    def process(self):
        yield self.request(
            self.hospital_beds, request_priority=self.request_priority, fail_delay=self.max_wait
        )
        self.got_bed = not self.failed()
        self.decided.set(True)
        # Count non-ED arrivals; patients already in the hospital at time zero have metrics=None.
        if not self.from_ed and self.metrics is not None:
            self.metrics.record_non_ed_admission(turned_away=not self.got_bed)
        if not self.got_bed:
            return

        # Exponential stays are memoryless, so starting patients' remaining stays use the same distribution.
        stay_end = self.env.now() + random.expovariate(1.0 / cfg.MEAN_HOSPITAL_STAY)
        yield self.hold(till=next_discharge_time(stay_end))
        self.release(self.hospital_beds)


class HospitalCensus(sim.Component):
    """
    The main hospital's own (non-ED) patients: fills HOSPITAL_INITIAL_OCCUPANCY of the beds at the
    start, then admits non-ED patients through the day. A non-ED patient who finds the hospital full
    is turned away rather than queuing, so occupancy fluctuates without competing with ED boarders.
    """

    def setup(self, hospital_beds, metrics):
        self.hospital_beds = hospital_beds
        self.metrics = metrics

    def _stay(self, metrics):
        InpatientStay(hospital_beds=self.hospital_beds, metrics=metrics, env=self.env)

    def process(self):
        for _ in range(round(self.hospital_beds.capacity() * cfg.HOSPITAL_INITIAL_OCCUPANCY)):
            self._stay(metrics=None)

        weights = cfg.NON_ED_ADMISSION_HOURLY_WEIGHTS
        hourly_rates = [cfg.NON_ED_ADMISSIONS_PER_DAY * w / sum(weights) for w in weights]
        while True:
            # Same hour-by-hour arrival process as ED arrivals (time zero is midnight).
            now = self.env.now()
            minutes_to_boundary = 60 - now % 60
            rate = hourly_rates[int(now // 60) % 24]
            interarrival = random.expovariate(rate / 60) if rate > 0 else float("inf")
            if interarrival >= minutes_to_boundary:
                yield self.hold(minutes_to_boundary)
                continue
            yield self.hold(interarrival)
            self._stay(metrics=self.metrics)
