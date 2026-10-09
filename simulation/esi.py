import statistics
import config as cfg

class ESI:
    def __init__(self):
        self.lengths_of_visit = {1: [], 2: [], 3: [], 4: [], 5: []}
        self.wait_times = {1: [], 2: [], 3: [], 4: [], 5: []} # creates a dictionary of lists to store wait times for each ESI level
        # Per-stage queue waits, e.g. {"bed": {3: [12.0, ...]}}.
        self.stage_waits = {}
        # "admitted" includes "forced" (admitted after the last diagnosis attempt failed).
        self.dispositions = {level: {"discharged": 0, "admitted": 0, "forced": 0} for level in range(1, 6)}
        self.failed_diagnoses = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    def record_wait_time(self, esi_level, wait_time):
        self.wait_times[esi_level].append(wait_time) # just taking wait time measured and storing in list for the patient's ESI level

    def record_stage_wait(self, stage, esi_level, wait_time):
        self.stage_waits.setdefault(stage, {}).setdefault(esi_level, []).append(wait_time)

    def record_disposition(self, esi_level, admitted, forced=False):
        self.dispositions[esi_level]["admitted" if admitted else "discharged"] += 1
        if forced:
            self.dispositions[esi_level]["forced"] += 1

    def record_failed_diagnosis(self, esi_level):
        self.failed_diagnoses[esi_level] += 1

    def record_length_of_visit(self, esi_level, length_of_visit):
        self.lengths_of_visit[esi_level].append(length_of_visit)

    def report(self):
        for level in self.wait_times:
            if self.wait_times[level]: # if there are wait times recorded for this ESI level
                waits = self.wait_times[level]

                count = len(waits) # counts how many patients are in this ESI level
                average_wait = sum(waits) / count # calculate average wait time
                median_wait = statistics.median(waits)

                print(
                    "ESI Level: ",
                    level,
                    "Door-to-provider patients: ",
                    count,
                    "Avg: ",
                    round(average_wait, 2),
                    "min",
                    "Median: ",
                    round(median_wait, 2),
                    "min",
                )

        for level, visits in self.lengths_of_visit.items():
            if visits:
                print(
                    "ESI Level: ", level,
                    "Completed visits: ", len(visits),
                    "Mean simulated LOV: ", round(statistics.mean(visits), 2), "min",
                    "Median simulated LOV: ", round(statistics.median(visits), 2), "min",
                )

        print("\nMean queue wait by stage (min):")
        # Report in patient-flow order; any unknown stage goes last.
        flow = (
            ["triage", "bed", "exam"]
            + [f"{t} queue" for t in cfg.TEST_TYPES]
            + ["test results", "diagnosis"]
            + [f"follow-up {t} queue" for t in cfg.TEST_TYPES]
            + ["follow-up results", "treatment", "disposition"]
        )
        order = {stage: i for i, stage in enumerate(flow)}
        for stage in sorted(self.stage_waits, key=lambda st: order.get(st, len(flow))):
            by_esi = self.stage_waits[stage]
            cells = [
                f"ESI {level}: {statistics.mean(by_esi[level]):6.1f} (n={len(by_esi[level])})"
                for level in sorted(by_esi)
            ]
            print(f"  {stage:<23}", " | ".join(cells))

        print("\nDispositions:")
        for level, counts in self.dispositions.items():
            if counts["discharged"] or counts["admitted"]:
                print(
                    "ESI Level: ", level,
                    "Discharged: ", counts["discharged"],
                    "Admitted/transferred: ", counts["admitted"],
                    "(forced after failed diagnoses: ", counts["forced"], ")",
                    "Failed diagnoses: ", self.failed_diagnoses[level],
                )
