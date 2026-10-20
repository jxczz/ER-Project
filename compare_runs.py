"""
Paired same-seed comparison of two saved validation runs: did a model change really change outcomes?
This is a measurement tool only; it does not change the model.

    python validation.py --reps 10 --days 14 --warmup-days 2 --save-csv baseline.csv
    (make the model change)
    python validation.py --reps 10 --days 14 --warmup-days 2 --save-csv candidate.csv
    python compare_runs.py baseline.csv candidate.csv

Each seed's candidate statistic is paired with the same seed's baseline statistic, and the 95%
t-interval of the per-seed differences (candidate minus baseline) is reported for every ESI group.
"""
import argparse
import math
import sys

import pandas as pd

from validation import GROUPS, STATS, mean_ci

MEASURES = (("d2p", "Door-to-provider"), ("lov", "Length of visit"))
SETTINGS = ("days", "warmup_days", "max_runoff_days")
# The per-replication columns written by validation.py --save-csv, plus the run settings.
REQUIRED_COLUMNS = (
    "rep", "seed", "group",
    "d2p_median", "d2p_mean", "d2p_p90", "d2p_n",
    "lov_median", "lov_mean", "lov_p90", "lov_n",
    "observed_arrivals", "unresolved", "runoff_minutes", "mean_bed_occupancy",
    *SETTINGS,
)
KEY = ["seed", "group"]


def load_run(path):
    """
    Read a validation.py --save-csv file, with group as a string. Raises ValueError on missing columns,
    non-numeric values in the other required columns, and blank seeds or settings.
    """
    run = pd.read_csv(path, dtype={"group": str})
    missing = [column for column in REQUIRED_COLUMNS if column not in run.columns]
    if missing:
        raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")
    for column in REQUIRED_COLUMNS:
        if column != "group":
            try:
                run[column] = pd.to_numeric(run[column])
            except (ValueError, TypeError):
                raise ValueError(f"{path}: column {column} has non-numeric values") from None
    blank = [column for column in ("seed", *SETTINGS) if run[column].isna().any()]
    if blank:
        raise ValueError(f"{path}: column(s) {', '.join(blank)} have blank values")
    return run


def run_settings(run, name="run"):
    """The run's days, warmup_days and max_runoff_days; each must have a single value in the file."""
    settings = {}
    for column in SETTINGS:
        values = run[column].unique()
        if len(values) != 1:
            raise ValueError(f"{name}: {column} must have a single value, got {sorted(values.tolist())}")
        settings[column] = values[0]
    return settings


def _check_keys(run, name):
    duplicated = run[run.duplicated(KEY, keep=False)][KEY].drop_duplicates()
    if not duplicated.empty:
        pairs = ", ".join(f"({row.seed}, {row.group})" for row in duplicated.itertuples())
        raise ValueError(f"{name}: (seed, group) appears more than once: {pairs}")
    unknown = sorted(set(run["group"]) - set(GROUPS), key=str)
    if unknown:
        raise ValueError(f"{name}: unknown group(s) {unknown}; expected {list(GROUPS)}")


def paired_differences(baseline, candidate):
    """
    Pair the two runs on (seed, group) and, for each group, measure ("d2p", "lov") and stat ("median",
    "mean", "p90"), summarise the per-seed differences candidate minus baseline.

    Raises ValueError when the runs differ in days, warmup_days or max_runoff_days, in their seeds, in
    their groups or in their (seed, group) pairs, or when a (seed, group) appears more than once.

    Returns a tidy DataFrame with columns group, measure, stat, baseline_mean, candidate_mean (means
    over the complete pairs, the seeds where both values are non-NaN, so candidate_mean - baseline_mean
    equals diff_mean; NaN when there are none), diff_mean, diff_low, diff_high (validation.mean_ci of
    the differences, which ignores NaN), n_pairs (number of non-NaN differences) and changed (True when
    the 95% interval excludes 0).
    Groups are in the order "1".."5", "all".
    """
    baseline_settings = run_settings(baseline, "baseline")
    candidate_settings = run_settings(candidate, "candidate")
    for column in SETTINGS:
        if baseline_settings[column] != candidate_settings[column]:
            raise ValueError(
                f"runs have different {column}: baseline {baseline_settings[column]:g}, "
                f"candidate {candidate_settings[column]:g}"
            )
    for name, run in (("baseline", baseline), ("candidate", candidate)):
        _check_keys(run, name)

    baseline_seeds, candidate_seeds = set(baseline["seed"]), set(candidate["seed"])
    if baseline_seeds != candidate_seeds:
        raise ValueError(
            f"runs have different seeds: only in baseline {sorted(baseline_seeds - candidate_seeds)}, "
            f"only in candidate {sorted(candidate_seeds - baseline_seeds)}"
        )
    baseline_groups, candidate_groups = set(baseline["group"]), set(candidate["group"])
    if baseline_groups != candidate_groups:
        raise ValueError(
            f"runs have different groups: only in baseline {sorted(baseline_groups - candidate_groups)}, "
            f"only in candidate {sorted(candidate_groups - baseline_groups)}"
        )
    baseline_keys = set(zip(baseline["seed"], baseline["group"]))
    candidate_keys = set(zip(candidate["seed"], candidate["group"]))
    if baseline_keys != candidate_keys:
        raise ValueError(
            "runs have different (seed, group) pairs: only in baseline "
            f"{sorted(baseline_keys - candidate_keys)}, only in candidate {sorted(candidate_keys - baseline_keys)}"
        )

    paired = baseline.merge(candidate, on=KEY, suffixes=("_baseline", "_candidate"), validate="one_to_one")
    rows = []
    for group in (group for group in GROUPS if group in baseline_groups):
        pairs = paired[paired["group"] == group]
        for measure, _ in MEASURES:
            for stat in STATS:
                column = f"{measure}_{stat}"
                differences = pairs[f"{column}_candidate"] - pairs[f"{column}_baseline"]
                complete = differences.notna()
                diff_mean, diff_low, diff_high = mean_ci(differences)
                rows.append({
                    "group": group,
                    "measure": measure,
                    "stat": stat,
                    "baseline_mean": mean_ci(pairs.loc[complete, f"{column}_baseline"])[0],
                    "candidate_mean": mean_ci(pairs.loc[complete, f"{column}_candidate"])[0],
                    "diff_mean": diff_mean,
                    "diff_low": diff_low,
                    "diff_high": diff_high,
                    "n_pairs": int(differences.notna().sum()),
                    # NaN bounds compare False, so they never count as a change.
                    "changed": bool(diff_low > 0 or diff_high < 0),
                })
    return pd.DataFrame(rows, columns=[
        "group", "measure", "stat", "baseline_mean", "candidate_mean",
        "diff_mean", "diff_low", "diff_high", "n_pairs", "changed",
    ])


def _format_number(value):
    # Adding 0.0 turns -0.0 into 0.0.
    return "n/a" if math.isnan(value) else f"{value + 0.0:.1f}"


def _format_ci(low, high):
    if math.isnan(low) or math.isnan(high):
        return "n/a"
    return f"[{low + 0.0:.1f}, {high + 0.0:.1f}]"


def format_table(differences, measure):
    """One printable table for a measure: a row per group, and per stat baseline, candidate, diff, CI and '*'."""
    rows = differences[differences["measure"] == measure]
    groups = list(dict.fromkeys(rows["group"]))
    table = pd.DataFrame(index=pd.Index([group.capitalize() for group in groups], name="ESI"))
    for stat in STATS:
        by_group = rows[rows["stat"] == stat].set_index("group").loc[groups]
        table[(stat, "baseline")] = [_format_number(v) for v in by_group["baseline_mean"]]
        table[(stat, "candidate")] = [_format_number(v) for v in by_group["candidate_mean"]]
        table[(stat, "diff")] = [_format_number(v) for v in by_group["diff_mean"]]
        table[(stat, "95% CI")] = [_format_ci(lo, hi) for lo, hi in zip(by_group["diff_low"], by_group["diff_high"])]
        table[(stat, "")] = ["*" if changed else "" for changed in by_group["changed"]]
    table.columns = pd.MultiIndex.from_tuples(table.columns)
    return table.to_string()


def main():
    parser = argparse.ArgumentParser(description="Paired same-seed comparison of two validation.py --save-csv runs.")
    parser.add_argument("baseline", help="CSV saved by validation.py --save-csv before the change")
    parser.add_argument("candidate", help="CSV saved by validation.py --save-csv after the change")
    args = parser.parse_args()

    try:
        baseline = load_run(args.baseline)
        candidate = load_run(args.candidate)
        differences = paired_differences(baseline, candidate)
    except (ValueError, OSError) as error:
        sys.exit(f"error: {error}")

    settings = run_settings(baseline)
    seeds = sorted(baseline["seed"].unique())
    print(f"Baseline:  {args.baseline}")
    print(f"Candidate: {args.candidate}")
    print(
        f"Settings: days {settings['days']:g}, warmup_days {settings['warmup_days']:g}, "
        f"max_runoff_days {settings['max_runoff_days']:g}"
    )
    print(f"Seeds ({len(seeds)}): {', '.join(str(seed) for seed in seeds)}")
    print("Times in minutes. Baseline and candidate values are means over the seeds where both values exist.\n")

    for measure, title in MEASURES:
        print(title)
        print(format_table(differences, measure))
        print()

    # Only intervals that exist (at least 2 pairs) can exclude 0.
    n_intervals = int(differences["diff_low"].notna().sum())
    print(
        "Note 1: Differences are candidate minus baseline, paired by seed. A \"*\" means the 95% CI "
        f"excludes 0. With {n_intervals} intervals, about {round(0.05 * n_intervals)} may do so by chance."
    )
    print(
        "Note 2: Same seeds reduce noise, but a model change can shift later random draws, so pairs are "
        "not identical streams."
    )


if __name__ == "__main__":
    main()
