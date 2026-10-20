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
QUANTILES = {"p25": 0.25, "p50": 0.5, "p75": 0.75}
ACUITY_LABEL = "IMMEDR (used as ESI)"


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


def nhamcs_targets(df):
    """
    PATWT-weighted 25th/50th/75th percentiles of WAITTIME and LOV for each IMMEDR level 1-5, plus the
    unweighted number of records used.

    Validity is decided per variable: WAITTIME is used when >= 0 and LOV when > 0, so a record with a
    negative sentinel code in one variable still counts for the other. Records with PATWT <= 0 (or
    missing) and IMMEDR outside 1-5 are excluded.

    These are point estimates only. Design-based uncertainty (from the CSTRATM/CPSUM strata and PSUs)
    is not computed yet.

    Returns a tidy DataFrame with columns immedr, variable ("waittime"/"lov"), n, p25, p50, p75.
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
        for level in LEVELS:
            mask = keep & immedr.eq(level)
            row = {"immedr": level, "variable": variable, "n": int(mask.sum())}
            for name, q in QUANTILES.items():
                row[name] = weighted_quantile(values[mask], weights[mask], q) if mask.any() else float("nan")
            rows.append(row)
    return pd.DataFrame(rows, columns=["immedr", "variable", "n", *QUANTILES])


class TimedESI(ESI):
    """ESI metrics that also store each door-to-provider and length-of-visit observation's arrival time."""

    def __init__(self, env):
        super().__init__()
        self.env = env
        # (arrival time, value) pairs per ESI level.
        self.timed_wait_times = {level: [] for level in LEVELS}
        self.timed_lengths_of_visit = {level: [] for level in LEVELS}

    def record_wait_time(self, esi_level, wait_time):
        super().record_wait_time(esi_level, wait_time)
        self.timed_wait_times[esi_level].append((self.env.now() - wait_time, wait_time))

    def record_length_of_visit(self, esi_level, length_of_visit):
        super().record_length_of_visit(esi_level, length_of_visit)
        self.timed_lengths_of_visit[esi_level].append((self.env.now() - length_of_visit, length_of_visit))

    def observations_since(self, start):
        """Door-to-provider and LOV values by ESI for patients who arrived at or after `start`."""
        def after(timed):
            return {level: [value for arrival, value in pairs if arrival >= start] for level, pairs in timed.items()}
        return after(self.timed_wait_times), after(self.timed_lengths_of_visit)


def _median(values):
    return statistics.median(values) if values else float("nan")


def run_replications(n_reps, days, warmup_days, first_seed=1):
    """
    Run `n_reps` independent replications (seeds first_seed, first_seed + 1, ...) of `days` days each,
    set up as in main.py, and summarize patients who arrived after the warm-up.

    Returns a tidy DataFrame with one row per replication and ESI level: rep, seed, esi,
    d2p_median, d2p_n, lov_median, lov_n, and mean_bed_occupancy (the replication's time-weighted
    mean of claimed ED beds over the whole run, warm-up included; the same on every row of a rep).
    """
    if n_reps < 1 or days <= 0 or not 0 <= warmup_days < days:
        raise ValueError(f"need n_reps >= 1 and 0 <= warmup_days < days, got {n_reps}, {days}, {warmup_days}")

    previous_animate = config.ANIMATE
    config.ANIMATE = False
    try:
        data = ERDataReader(config.DATASET_PATH, arrivals_per_day=config.ARRIVALS_PER_DAY).load_and_prepare()
        warmup_end = warmup_days * MINUTES_PER_DAY
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
            env.run(till=days * MINUTES_PER_DAY)

            # Right-censoring: patients still in the ED at the end have no length of visit (and those
            # not yet examined have no door-to-provider time), so they are not counted. This biases
            # both measures slightly low, most for the longest visits near the end of the run.
            waits, visits = metrics.observations_since(warmup_end)
            occupancy = beds.claimed_quantity.mean()
            for level in LEVELS:
                rows.append({
                    "rep": i + 1,
                    "seed": seed,
                    "esi": level,
                    "d2p_median": _median(waits[level]),
                    "d2p_n": len(waits[level]),
                    "lov_median": _median(visits[level]),
                    "lov_n": len(visits[level]),
                    "mean_bed_occupancy": occupancy,
                })
    finally:
        config.ANIMATE = previous_animate
    return pd.DataFrame(rows)


def compare(replications, targets):
    """
    One row per ESI / IMMEDR level. Simulation: mean, min and max across replications of the
    per-replication medians, and the mean count per replication. NHAMCS: weighted median, 25th and
    75th percentiles, and unweighted n. Door-to-provider is compared with WAITTIME and LOV with LOV.
    """
    by_esi = replications.groupby("esi")
    table = pd.DataFrame(index=pd.Index(LEVELS, name=f"ESI / {ACUITY_LABEL}"))
    for sim_name, nhamcs_name in (("d2p", "waittime"), ("lov", "lov")):
        medians = by_esi[f"{sim_name}_median"]
        table[f"sim_{sim_name}_mean_median"] = medians.mean()
        table[f"sim_{sim_name}_min"] = medians.min()
        table[f"sim_{sim_name}_max"] = medians.max()
        table[f"sim_{sim_name}_n_per_rep"] = by_esi[f"{sim_name}_n"].mean()
        target = targets[targets["variable"] == nhamcs_name].set_index("immedr")
        table[f"nhamcs_{nhamcs_name}_median"] = target["p50"]
        table[f"nhamcs_{nhamcs_name}_p25"] = target["p25"]
        table[f"nhamcs_{nhamcs_name}_p75"] = target["p75"]
        table[f"nhamcs_{nhamcs_name}_n"] = target["n"]
    return table


def main():
    parser = argparse.ArgumentParser(description="Compare simulated outcomes with NHAMCS 2022 targets.")
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--days", type=float, default=14)
    parser.add_argument("--warmup-days", type=float, default=2)
    parser.add_argument("--first-seed", type=int, default=1)
    args = parser.parse_args()

    targets = nhamcs_targets(pd.read_parquet(ERDataReader(config.DATASET_PATH).filepath))
    replications = run_replications(args.reps, args.days, args.warmup_days, args.first_seed)
    table = compare(replications, targets)

    seeds = sorted(replications["seed"].unique())
    print(
        f"Replications: {args.reps}, {args.days:g} days each, first {args.warmup_days:g} days warm-up "
        f"(excluded by arrival time); seeds {', '.join(str(s) for s in seeds)}"
    )
    print("Times in minutes. Simulation: mean [min, max] across replications of each replication's median.")
    print("NHAMCS: PATWT-weighted median [25th, 75th percentile], unweighted n; point estimates only.\n")

    for sim_name, nhamcs_name, title in (
        ("d2p", "waittime", "Door-to-provider (sim) vs WAITTIME (NHAMCS)"),
        ("lov", "lov", "Length of visit (sim) vs LOV (NHAMCS)"),
    ):
        view = pd.DataFrame({
            "sim mean median": table[f"sim_{sim_name}_mean_median"].round(1),
            "sim min": table[f"sim_{sim_name}_min"].round(1),
            "sim max": table[f"sim_{sim_name}_max"].round(1),
            "sim n/rep": table[f"sim_{sim_name}_n_per_rep"].round(1),
            "NHAMCS median": table[f"nhamcs_{nhamcs_name}_median"],
            "NHAMCS p25": table[f"nhamcs_{nhamcs_name}_p25"],
            "NHAMCS p75": table[f"nhamcs_{nhamcs_name}_p75"],
            "NHAMCS n": table[f"nhamcs_{nhamcs_name}_n"],
        })
        print(title)
        print(view.to_string())
        print()

    occupancy = replications.groupby("rep")["mean_bed_occupancy"].first()
    print(
        f"Mean ED bed occupancy (whole run incl. warm-up), averaged over replications: "
        f"{occupancy.mean():.2f} of {config.NUM_BEDS} beds"
    )
    print(
        "Caveat: NHAMCS describes a national mix of EDs of all sizes, while the simulation is one ED "
        "sized to the national mean volume."
    )


if __name__ == "__main__":
    main()
