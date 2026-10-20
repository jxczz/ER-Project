"""
Validation harness: compares simulated door-to-provider time and length of visit with survey-weighted
NHAMCS 2022 targets, by acuity. This is a measurement tool only; it does not change the model.

    python validation.py --reps 10 --days 14 --warmup-days 2

Mappings:
- simulated door-to-provider (arrival to exam start) vs NHAMCS WAITTIME (arrival to first
  physician/APRN/PA contact)
- simulated length of visit (arrival to ED departure) vs NHAMCS LOV (arrival to ED discharge)
- simulated ESI 1-5 vs NHAMCS IMMEDR 1-5 ("IMMEDR (used as ESI)")
WAITTIME and LOV are comparison targets only; nothing here feeds them into the simulation.
"""
import argparse
import math
import os
import statistics

import pandas as pd
import salabim as sim

import config
from simulation.arrival_generator import ArrivalGenerator
from simulation.data_reader import ERDataReader
from simulation.environment import create_environment
from simulation.esi import ESI
from simulation.hospital import HospitalCensus

# Just using generator-based simulation, as in main.py.
sim.yieldless(False)

MINUTES_PER_DAY = 24 * 60
LEVELS = (1, 2, 3, 4, 5)
QUANTILES = {"p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}
# ESI / IMMEDR levels as strings, plus "all", which pools levels 1-5.
GROUPS = (*(str(level) for level in LEVELS), "all")
ACUITY_LABEL = "IMMEDR (used as ESI)"

# Two-sided 95% critical values of Student's t for df 1-30, from the standard Student t table.
T_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
    20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}
T_95_LARGE_DF = 1.96


def weighted_quantile(values, weights, q):
    """Smallest value whose cumulative weight share (values sorted ascending) is >= q."""
    values = [float(v) for v in values]
    weights = [float(w) for w in weights]
    if not values:
        raise ValueError("weighted_quantile needs at least one value")
    if len(values) != len(weights):
        raise ValueError(f"got {len(values)} values but {len(weights)} weights")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("values must be finite")
    if not all(math.isfinite(w) and w >= 0 for w in weights):
        raise ValueError("weights must be finite and non-negative")
    if not (isinstance(q, (int, float)) and 0 <= q <= 1):
        raise ValueError(f"q must be in [0, 1], got {q!r}")
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must have a positive total")

    cumulative = 0.0
    pairs = sorted(zip(values, weights))
    for value, weight in pairs:
        cumulative += weight
        if cumulative / total >= q:
            return value
    return pairs[-1][0]  # only reached through rounding when q is 1


def mean_ci(values, level=0.95):
    """
    Mean and two-sided t-interval across replications: (mean, low, high), with
    mean +/- t * sd / sqrt(R), where sd is the sample standard deviation and R the number of values.
    NaN values are ignored. With fewer than 2 values the bounds are NaN. Only level 0.95 is supported.
    """
    if level != 0.95:
        raise ValueError(f"only level 0.95 is supported, got {level!r}")
    values = [float(v) for v in values if not math.isnan(float(v))]
    if not values:
        return float("nan"), float("nan"), float("nan")
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, float("nan"), float("nan")
    df = len(values) - 1
    half_width = T_95.get(df, T_95_LARGE_DF) * statistics.stdev(values) / math.sqrt(len(values))
    return mean, mean - half_width, mean + half_width


def nhamcs_targets(df):
    """
    PATWT-weighted mean (sum(w * x) / sum(w)) and 25th/50th/75th/90th percentiles of WAITTIME and LOV
    for each IMMEDR level 1-5 and for "all" (levels 1-5 pooled), plus the unweighted number of records
    used.

    Validity is decided per variable: WAITTIME is used when >= 0 and LOV when > 0, so a record with a
    negative sentinel code in one variable still counts for the other. Records with PATWT <= 0 (or
    missing) and IMMEDR outside 1-5 are excluded.

    These are point estimates only. Design-based uncertainty (from the CSTRATM/CPSUM strata and PSUs)
    is not computed yet.

    Returns a tidy DataFrame with columns group ("1".."5", "all"), variable ("waittime"/"lov"), n, mean,
    p25, p50, p75, p90.
    """
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    immedr = pd.to_numeric(df["immedr"], errors="coerce")
    weights = pd.to_numeric(df["patwt"], errors="coerce")
    base = immedr.isin(LEVELS) & weights.gt(0) & weights.lt(float("inf"))

    variables = {
        "waittime": lambda s: s.ge(0),
        "lov": lambda s: s.gt(0),
    }
    rows = []
    for variable, is_valid in variables.items():
        values = pd.to_numeric(df[variable], errors="coerce")
        keep = base & is_valid(values)
        for group in GROUPS:
            mask = keep if group == "all" else keep & immedr.eq(int(group))
            row = {"group": group, "variable": variable, "n": int(mask.sum())}
            if mask.any():
                row["mean"] = float((values[mask] * weights[mask]).sum() / weights[mask].sum())
            else:
                row["mean"] = float("nan")
            for name, q in QUANTILES.items():
                row[name] = weighted_quantile(values[mask], weights[mask], q) if mask.any() else float("nan")
            rows.append(row)
    return pd.DataFrame(rows, columns=["group", "variable", "n", "mean", *QUANTILES])


class TimedESI(ESI):
    """
    ESI metrics that also store each patient's arrival time, and each door-to-provider and
    length-of-visit observation's arrival time.
    """

    def __init__(self, env):
        super().__init__()
        self.env = env
        self.arrival_times = {}
        # (arrival time, value) pairs per ESI level.
        self.timed_wait_times = {level: [] for level in LEVELS}
        self.timed_lengths_of_visit = {level: [] for level in LEVELS}

    def record_arrival(self, patient):
        super().record_arrival(patient)
        self.arrival_times[patient] = self.env.now()

    def record_wait_time(self, esi_level, wait_time):
        super().record_wait_time(esi_level, wait_time)
        self.timed_wait_times[esi_level].append((self.env.now() - wait_time, wait_time))

    def record_length_of_visit(self, esi_level, length_of_visit):
        super().record_length_of_visit(esi_level, length_of_visit)
        self.timed_lengths_of_visit[esi_level].append((self.env.now() - length_of_visit, length_of_visit))

    def arrived_between(self, patients, start, end):
        """The given patients who arrived in [start, end)."""
        return [patient for patient in patients if start <= self.arrival_times[patient] < end]

    def in_window(self, start, end):
        """Patients still in the ED who arrived in [start, end)."""
        return self.arrived_between(self.in_ed, start, end)

    def pending(self, start, end):
        """Number of patients still in the ED who arrived in [start, end)."""
        return len(self.in_window(start, end))

    def observations_between(self, start, end):
        """Door-to-provider and LOV values by ESI for patients who arrived in [start, end)."""
        def within(timed):
            return {
                level: [value for arrival, value in pairs if start <= arrival < end]
                for level, pairs in timed.items()
            }
        return within(self.timed_wait_times), within(self.timed_lengths_of_visit)


def _summary(values):
    """Median, mean, 90th percentile (the weighted_quantile rule with equal weights) and n; NaN when empty."""
    n = len(values)
    if n == 0:
        return float("nan"), float("nan"), float("nan"), 0
    return statistics.median(values), statistics.fmean(values), weighted_quantile(values, [1] * n, 0.9), n


def run_replications(n_reps, days, warmup_days, first_seed=1, max_runoff_days=7):
    """
    Run `n_reps` independent replications (seeds first_seed, first_seed + 1, ...), set up as in
    main.py. The observed patients are those who arrive between day `warmup_days` and day `days`.
    After day `days` the run continues unchanged (arrivals and the hospital census keep going) until
    every observed patient has left the ED, or for at most `max_runoff_days` more days.

    Returns a tidy DataFrame with one row per replication and group (ESI "1".."5", and "all", which
    pools every observed patient): rep, seed, group, d2p_median, d2p_mean, d2p_p90, d2p_n, lov_median,
    lov_mean, lov_p90, lov_n, observed_arrivals (arrivals in the window), unresolved (observed patients
    still in the ED when the run-off cap was hit; should be 0), runoff_minutes (minutes run past day
    `days`), and mean_bed_occupancy (the time-weighted mean of claimed ED beds over the whole run,
    warm-up and run-off included). The last two are the same on every row of a rep. On the "all" row,
    observed_arrivals and unresolved are the sums over ESI. A statistic with n = 0 is NaN.
    """
    if n_reps < 1 or days <= 0 or not 0 <= warmup_days < days or not max_runoff_days >= 0:
        raise ValueError(
            "need n_reps >= 1, 0 <= warmup_days < days and max_runoff_days >= 0, "
            f"got {n_reps}, {days}, {warmup_days}, {max_runoff_days}"
        )

    previous_animate = config.ANIMATE
    config.ANIMATE = False
    try:
        data = ERDataReader(config.DATASET_PATH, arrivals_per_day=config.ARRIVALS_PER_DAY).load_and_prepare()
        warmup_end = warmup_days * MINUTES_PER_DAY
        cutoff = days * MINUTES_PER_DAY
        runoff_cap = cutoff + max_runoff_days * MINUTES_PER_DAY
        rows = []
        for i in range(n_reps):
            seed = first_seed + i
            env, staff, beds, testing, hospital_beds = create_environment(random_seed=seed)
            metrics = TimedESI(env)
            HospitalCensus(hospital_beds=hospital_beds, metrics=metrics, env=env)
            ArrivalGenerator(
                staff=staff, beds=beds, testing=testing, hospital_beds=hospital_beds,
                metrics=metrics, data=data, env=env,
            )
            env.run(till=cutoff)

            # Observation window and run-off: only patients arriving in [warmup_end, cutoff) are
            # observed. The run then continues as before, so the ED stays realistically loaded, until
            # all of them have left. Each observed patient then has a door-to-provider time and a
            # length of visit, so long visits are not lost to right-censoring at the end of the run.
            while metrics.pending(warmup_end, cutoff) > 0 and env.now() < runoff_cap:
                env.run(till=min(env.now() + 60, runoff_cap))

            waits, visits = metrics.observations_between(warmup_end, cutoff)
            observed = [patient.esi for patient in metrics.arrived_between(metrics.arrival_times, warmup_end, cutoff)]
            unresolved = [patient.esi for patient in metrics.in_window(warmup_end, cutoff)]
            runoff_minutes = env.now() - cutoff
            occupancy = beds.claimed_quantity.mean()
            for group in GROUPS:
                levels = LEVELS if group == "all" else (int(group),)
                row = {"rep": i + 1, "seed": seed, "group": group}
                for name, by_level in (("d2p", waits), ("lov", visits)):
                    median, mean, p90, n = _summary([value for level in levels for value in by_level[level]])
                    row.update({f"{name}_median": median, f"{name}_mean": mean, f"{name}_p90": p90, f"{name}_n": n})
                row.update({
                    "observed_arrivals": sum(observed.count(level) for level in levels),
                    "unresolved": sum(unresolved.count(level) for level in levels),
                    "runoff_minutes": runoff_minutes,
                    "mean_bed_occupancy": occupancy,
                })
                rows.append(row)
    finally:
        config.ANIMATE = previous_animate
    return pd.DataFrame(rows)


STATS = ("median", "mean", "p90")
NHAMCS_STAT = {"median": "p50", "mean": "mean", "p90": "p90"}
MEASURES = (
    ("d2p", "waittime", "Door-to-provider (sim) vs WAITTIME (NHAMCS)"),
    ("lov", "lov", "Length of visit (sim) vs LOV (NHAMCS)"),
)


def compare(replications, targets):
    """
    One row per group (ESI / IMMEDR "1".."5" and "all"). Simulation: for the median, mean and 90th
    percentile, the mean across replications of each replication's statistic and its 95% t-interval
    (columns sim_<measure>_<stat>, _low and _high), and the mean count per replication. NHAMCS: the
    PATWT-weighted median, mean and 90th percentile, and unweighted n. Door-to-provider is compared
    with WAITTIME and LOV with LOV.
    """
    by_group = replications.groupby("group")
    table = pd.DataFrame(index=pd.Index(GROUPS, name=f"ESI / {ACUITY_LABEL}"))
    for sim_name, nhamcs_name, _ in MEASURES:
        target = targets[targets["variable"] == nhamcs_name].set_index("group")
        for stat in STATS:
            cis = [mean_ci(by_group.get_group(group)[f"{sim_name}_{stat}"]) for group in GROUPS]
            column = f"sim_{sim_name}_{stat}"
            table[column] = [ci[0] for ci in cis]
            table[f"{column}_low"] = [ci[1] for ci in cis]
            table[f"{column}_high"] = [ci[2] for ci in cis]
            table[f"nhamcs_{nhamcs_name}_{stat}"] = target[NHAMCS_STAT[stat]]
        table[f"sim_{sim_name}_n_per_rep"] = by_group[f"{sim_name}_n"].mean()
        table[f"nhamcs_{nhamcs_name}_n"] = target["n"]
    return table


def _format_ci(low, high):
    if math.isnan(low) or math.isnan(high):
        return "n/a"
    return f"[{low:.1f}, {high:.1f}]"


def main():
    parser = argparse.ArgumentParser(description="Compare simulated outcomes with NHAMCS 2022 targets.")
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--days", type=float, default=14)
    parser.add_argument("--warmup-days", type=float, default=2)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--max-runoff-days", type=float, default=7)
    parser.add_argument("--save-csv", metavar="PATH", help="write the per-replication results to this CSV file")
    args = parser.parse_args()
    if args.save_csv and (
        os.path.isdir(args.save_csv) or not os.path.isdir(os.path.dirname(os.path.abspath(args.save_csv)))
    ):
        parser.error(f"--save-csv: {args.save_csv} is a directory or its directory does not exist")

    targets = nhamcs_targets(pd.read_parquet(ERDataReader(config.DATASET_PATH).filepath))
    replications = run_replications(
        args.reps, args.days, args.warmup_days, args.first_seed, max_runoff_days=args.max_runoff_days
    )
    # Saved before any printing, so a failure while reporting cannot lose a long run.
    # The run settings go on every row, so compare_runs.py can check that two runs are comparable.
    if args.save_csv:
        replications.assign(
            days=args.days, warmup_days=args.warmup_days, max_runoff_days=args.max_runoff_days
        ).to_csv(args.save_csv, index=False)
    table = compare(replications, targets)

    seeds = sorted(replications["seed"].unique())
    print(
        f"Replications: {args.reps}; patients arriving between day {args.warmup_days:g} and day "
        f"{args.days:g} are observed; the run continues until all of them leave (at most "
        f"{args.max_runoff_days:g} extra days); seeds {', '.join(str(s) for s in seeds)}"
    )
    print(
        "Times in minutes. Simulation values are means across replications of each replication's "
        "statistic, with a 95% t-interval."
    )
    print(
        "NHAMCS values are PATWT-weighted point estimates, IMMEDR 1-5 (All pools 1-5), unweighted n; "
        "design-based uncertainty not computed.\n"
    )

    for sim_name, nhamcs_name, title in MEASURES:
        view = pd.DataFrame(index=pd.Index([group.capitalize() for group in GROUPS], name=table.index.name))
        for stat in STATS:
            column = f"sim_{sim_name}_{stat}"
            view[f"sim {stat}"] = table[column].round(1).to_numpy()
            view[f"sim {stat} 95% CI"] = [
                _format_ci(low, high) for low, high in zip(table[f"{column}_low"], table[f"{column}_high"])
            ]
            view[f"NHAMCS {stat}"] = table[f"nhamcs_{nhamcs_name}_{stat}"].round(1).to_numpy()
        view["sim n/rep"] = table[f"sim_{sim_name}_n_per_rep"].round(1).to_numpy()
        view["NHAMCS n"] = table[f"nhamcs_{nhamcs_name}_n"].to_numpy()
        print(title)
        print(view.to_string())
        print()

    by_rep = replications.groupby("rep")
    runoff = by_rep["runoff_minutes"].first()
    print(f"Run-off past day {args.days:g}: mean {runoff.mean():.1f} min, max {runoff.max():.1f} min")
    unresolved = int(replications["unresolved"].sum())
    print(f"Observed patients still in the ED at the run-off cap, all replications: {unresolved}")
    if unresolved > 0:
        print(
            f"WARNING: {unresolved} observed patient(s) were still in the ED after {args.max_runoff_days:g} "
            "run-off days; their visits are missing, so the results are still right-censored."
        )
    occupancy = by_rep["mean_bed_occupancy"].first()
    print(
        f"Mean ED bed occupancy (whole run incl. warm-up and run-off), averaged over replications: "
        f"{occupancy.mean():.2f} of {config.NUM_BEDS} beds"
    )
    print(
        "Caveat: NHAMCS describes a national mix of EDs of all sizes, while the simulation is one ED "
        "sized to the national mean volume."
    )

    if args.save_csv:
        print(f"Per-replication results written to {args.save_csv}")


if __name__ == "__main__":
    main()
