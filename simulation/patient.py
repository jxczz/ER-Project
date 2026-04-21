import salabim as sim
import random
from config import MEAN_TRIAGE_TIME, MEAN_SERVICE_TIME, MEAN_BED_TIME, ESI_WEIGHTS_SYNTHETIC
 
 
class Patient(sim.Component):
    def setup(self, triage, providers, beds, metrics, esi=None, service_time=None, data=None):
        self.triage = triage
        self.providers = providers
        self.beds = beds
        self.metrics = metrics
 
        # If esi/service_time are provided explicitly, use them.
        # Otherwise, determine ESI during triage and sample service time after ESI is known.
        self.esi = esi
        self.service_time = service_time
        self.data = data
 
    def _safe_move(self, new_queue):
        """
        Safely transition this patient to a new animation queue.
 
        WHY THIS EXISTS:
        - self.enter(q) raises ValueError if the component is already a member of q.
        - self.leave(q) raises ValueError if the component is NOT a member of q.
        - Both exceptions silently kill the process() coroutine mid-execution, which
          freezes the patient box on screen at whatever queue it was last in.
 
        FIX:
        - self._qmembers is an internal dict keyed by queue object.
          Checking 'q in self._qmembers' is a safe, no-exception membership test.
        """
        # Leave current queue only if we are actually still in it
        if self._anim_queue is not None and self._anim_queue in self._qmembers:
            self.leave(self._anim_queue)
 
        self._anim_queue = new_queue
 
        # Enter new queue only if it exists and we aren't already in it
        if new_queue is not None and new_queue not in self._qmembers:
            self.enter(new_queue)
 
    def _assign_esi_and_service_time(self):
        """
        Assign triage category (ESI) and sample a service time.
 
        Called after triage completes, matching real-world flow:
        patient arrives -> triage assessment -> ESI assigned -> waits for provider by acuity.
        """
        if self.esi is None:
            if self.data is not None:
                esi_levels, esi_weights, _interarrival_times, service_times_by_esi = self.data
                self.esi = random.choices(esi_levels, weights=esi_weights, k=1)[0]
                if self.service_time is None:
                    times = service_times_by_esi.get(self.esi, [])
                    self.service_time = random.choice(times) if times else MEAN_SERVICE_TIME
            else:
                # Synthetic fallback
                self.esi = random.choices([1, 2, 3, 4, 5], weights=ESI_WEIGHTS_SYNTHETIC, k=1)[0]
                if self.service_time is None:
                    self.service_time = random.expovariate(1.0 / MEAN_SERVICE_TIME)
 
        if self.service_time is None:
            self.service_time = MEAN_SERVICE_TIME
 
    def process(self):
        # Door time: measure wait from the moment the patient enters the ER.
        arrival_time = self.env.now()
 
        self._anim_queue = None
 
        # --- TRIAGE ---
        self._safe_move(self.env.q_wait_triage)
        yield self.request(self.triage)
        self._safe_move(self.env.q_in_triage)
        yield self.hold(random.expovariate(1.0 / MEAN_TRIAGE_TIME))
        self.release(self.triage)
 
        # After triage completes, assign ESI (real-world: assess -> categorise -> queue)
        self._assign_esi_and_service_time()
 
        # --- PROVIDER ---
        # Lower priority value = served first, so ESI 1 (most critical) wins.
        self._safe_move(self.env.q_wait_provider)
        yield self.request(self.providers, priority=self.esi)
        self._safe_move(self.env.q_in_treatment)
        wait_time = self.env.now() - arrival_time
        self.metrics.record_wait_time(self.esi, wait_time)
 
        yield self.hold(self.service_time)
        self.release(self.providers)
 
        # --- BED ---
        self._safe_move(self.env.q_wait_bed)
        yield self.request(self.beds)
        self._safe_move(self.env.q_in_bed)
        yield self.hold(random.expovariate(1.0 / MEAN_BED_TIME))
        self.release(self.beds)
 
        # Leave the last queue so the box disappears properly
        self._safe_move(None)
 
    def animation_objects(self, *args, **kwargs):
        """
        Controls how this Patient looks when shown in an animated queue.
        """
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
            screen_coordinates=False,
        )
        try:
            ao0 = sim.AnimateRectangle(**rect_kwargs)
        except TypeError:
            rect_kwargs.pop("screen_coordinates", None)
            ao0 = sim.AnimateRectangle(**rect_kwargs)
 
        return (28, 18, ao0)