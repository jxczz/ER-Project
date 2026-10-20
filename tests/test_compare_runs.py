import contextlib
import io
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import compare_runs
from compare_runs import REQUIRED_COLUMNS, load_run, paired_differences
from validation import GROUPS, mean_ci

SEEDS = (1, 2, 3)


def make_run(seeds=SEEDS, days=14.0, warmup_days=2.0, max_runoff_days=7.0):
    """A hand-made run with the full validation.py --save-csv column set; values differ by seed and group."""
    rows = []
    for rep, seed in enumerate(seeds, start=1):
        for g, group in enumerate(GROUPS):
            rows.append({
                "rep": rep, "seed": seed, "group": group,
                "d2p_median": 10.0 + g + seed, "d2p_mean": 20.0 + 2 * g + seed, "d2p_p90": 40.0 + g * seed,
                "d2p_n": 100 + g,
                "lov_median": 150.0 + g + seed, "lov_mean": 200.0 + g * seed, "lov_p90": 400.0 + g + 2 * seed,
                "lov_n": 100 + g,
                "observed_arrivals": 100 + g, "unresolved": 0, "runoff_minutes": 300.0 + seed,
                "mean_bed_occupancy": 20.0 + seed / 10,
                "days": days, "warmup_days": warmup_days, "max_runoff_days": max_runoff_days,
            })
    return pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))


def row(differences, group, measure, stat):
    match = differences[
        (differences["group"] == group) & (differences["measure"] == measure) & (differences["stat"] == stat)
    ]
    return match.iloc[0]


class CsvTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def save(self, run, name):
        path = os.path.join(self.directory, name)
        run.to_csv(path, index=False)
        return path


class LoadRunTests(CsvTestCase):
    def test_group_is_read_as_string(self):
        run = load_run(self.save(make_run(), "run.csv"))
        self.assertEqual(list(run["group"].unique()), list(GROUPS))

    def test_missing_column_raises_naming_it(self):
        path = self.save(make_run().drop(columns=["lov_p90", "warmup_days"]), "run.csv")
        with self.assertRaises(ValueError) as caught:
            load_run(path)
        self.assertIn("lov_p90", str(caught.exception))
        self.assertIn("warmup_days", str(caught.exception))

    def test_malformed_values_raise_naming_the_column(self):
        text_stat = make_run().astype({"d2p_mean": object})
        text_stat.loc[0, "d2p_mean"] = "abc"
        text_days = make_run().astype({"days": object})
        text_days["days"] = "abc"
        blank_days = make_run()
        blank_days["days"] = math.nan
        blank_seed = make_run()
        blank_seed.loc[0, "seed"] = math.nan
        for name, run, message in (
            ("text statistic", text_stat, "d2p_mean has non-numeric values"),
            ("text setting", text_days, "days has non-numeric values"),
            ("blank setting", blank_days, "days have blank values"),
            ("blank seed", blank_seed, "seed have blank values"),
        ):
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    load_run(self.save(run, "run.csv"))
                self.assertIn(message, str(caught.exception))


class PairedDifferencesTests(CsvTestCase):
    def pair(self, baseline, candidate):
        return paired_differences(
            load_run(self.save(baseline, "baseline.csv")), load_run(self.save(candidate, "candidate.csv"))
        )

    def test_identical_runs_have_zero_differences(self):
        differences = self.pair(make_run(), make_run())
        self.assertEqual(len(differences), len(GROUPS) * 2 * 3)
        self.assertEqual(list(dict.fromkeys(differences["group"])), list(GROUPS))
        self.assertTrue((differences["diff_mean"] == 0).all())
        self.assertTrue((differences["diff_low"] == 0).all())
        self.assertTrue((differences["diff_high"] == 0).all())
        self.assertFalse(differences["changed"].any())
        self.assertTrue((differences["n_pairs"] == len(SEEDS)).all())
        self.assertTrue((differences["baseline_mean"] == differences["candidate_mean"]).all())

    def test_known_shift_is_detected(self):
        baseline = make_run()
        candidate = make_run()
        noise = {1: 0.3, 2: -0.2, 3: 0.5}
        shift = candidate["group"] == "3"
        candidate.loc[shift, "d2p_mean"] += 5 + candidate.loc[shift, "seed"].map(noise)

        differences = self.pair(baseline, candidate)
        shifted = row(differences, "3", "d2p", "mean")
        expected = [5.3, 4.8, 5.5]
        self.assertAlmostEqual(shifted["diff_mean"], (5.3 + 4.8 + 5.5) / 3)
        _, low, high = mean_ci(expected)
        self.assertAlmostEqual(shifted["diff_low"], low)
        self.assertAlmostEqual(shifted["diff_high"], high)
        self.assertTrue(shifted["changed"])
        self.assertEqual(shifted["n_pairs"], 3)
        self.assertAlmostEqual(shifted["candidate_mean"] - shifted["baseline_mean"], shifted["diff_mean"])
        # Nothing else moved.
        others = differences.drop(index=shifted.name)
        self.assertTrue((others["diff_mean"] == 0).all())
        self.assertFalse(others["changed"].any())

    def test_nan_statistic_is_ignored(self):
        baseline = make_run()
        # Group "1" lov_p90: baseline 400, 430, 410 for seeds 1, 2, 3 (uneven, so dropping seed 2 moves
        # the mean). The candidate adds 3, 50 and 7, and then seed 2's value is lost.
        group_1 = baseline["group"] == "1"
        baseline.loc[group_1, "lov_p90"] = baseline.loc[group_1, "seed"].map({1: 400.0, 2: 430.0, 3: 410.0})
        candidate = baseline.copy()
        candidate.loc[group_1, "lov_p90"] += candidate.loc[group_1, "seed"].map({1: 3.0, 2: 50.0, 3: 7.0})
        candidate.loc[group_1 & (candidate["seed"] == 2), "lov_p90"] = math.nan
        differences = self.pair(baseline, candidate)
        affected = row(differences, "1", "lov", "p90")
        self.assertEqual(affected["n_pairs"], 2)
        self.assertAlmostEqual(affected["diff_mean"], (3 + 7) / 2)
        self.assertAlmostEqual(affected["candidate_mean"] - affected["baseline_mean"], affected["diff_mean"])
        self.assertAlmostEqual(affected["baseline_mean"], (400 + 410) / 2)
        self.assertEqual(row(differences, "2", "lov", "p90")["n_pairs"], 3)

    def test_mismatches_raise(self):
        duplicated = make_run()
        duplicated = pd.concat([duplicated, duplicated.iloc[[0]]], ignore_index=True)
        missing_group = make_run()
        missing_group = missing_group[missing_group["group"] != "5"]
        missing_pair = make_run()
        missing_pair = missing_pair[~((missing_pair["seed"] == 2) & (missing_pair["group"] == "4"))]
        mixed_days = make_run()
        mixed_days.loc[0, "days"] = 7.0
        for name, candidate, message in (
            ("different seeds", make_run(seeds=(1, 2, 4)), "seeds"),
            ("different days", make_run(days=7.0), "different days:"),
            ("different warmup_days", make_run(warmup_days=1.0), "different warmup_days:"),
            ("different max_runoff_days", make_run(max_runoff_days=3.0), "different max_runoff_days:"),
            ("several days in one file", mixed_days, "single value"),
            ("missing group", missing_group, "groups"),
            ("missing (seed, group)", missing_pair, "(seed, group) pairs"),
            ("duplicated (seed, group)", duplicated, "more than once"),
        ):
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    self.pair(make_run(), candidate)
                self.assertIn(message, str(caught.exception))


class CliTests(CsvTestCase):
    def run_main(self, baseline, candidate):
        argv = ["compare_runs.py", self.save(baseline, "baseline.csv"), self.save(candidate, "candidate.csv")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            compare_runs.main()
        return stdout.getvalue(), stderr.getvalue()

    def test_prints_tables_and_notes(self):
        candidate = make_run()
        candidate.loc[candidate["group"] == "all", "d2p_median"] += [4.9, 5.0, 5.2]
        output, _ = self.run_main(make_run(), candidate)
        for text in (
            "baseline.csv", "candidate.csv", "days 14, warmup_days 2, max_runoff_days 7", "Seeds (3): 1, 2, 3",
            "Door-to-provider", "Length of visit", "baseline", "candidate", "95% CI", "[0.0, 0.0]", "*",
            "candidate minus baseline, paired by seed", "With 36 intervals, about 2",
            "pairs are not identical streams",
        ):
            self.assertIn(text, output)
        output.encode("ascii")  # raises UnicodeEncodeError if not ASCII
        self.assertEqual(output.count("*"), 2)  # the marker on the shifted row, plus the '"*"' in note 1

    def test_single_seed_has_no_intervals(self):
        output, _ = self.run_main(make_run(seeds=(1,)), make_run(seeds=(1,)))
        self.assertIn("With 0 intervals, about 0", output)
        self.assertNotIn("[", output)  # every CI is n/a

    def test_malformed_file_exits_with_message_and_no_traceback(self):
        candidate = make_run().astype({"d2p_mean": object})
        candidate.loc[0, "d2p_mean"] = "abc"
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["compare_runs.py", self.save(make_run(), "baseline.csv"), self.save(candidate, "candidate.csv")]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                compare_runs.main()
        self.assertIn("non-numeric", str(caught.exception.code))
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())

    def test_mismatch_exits_with_message_and_no_traceback(self):
        stderr = io.StringIO()
        argv = [
            "compare_runs.py", self.save(make_run(), "baseline.csv"),
            self.save(make_run(seeds=(11, 12, 13)), "candidate.csv"),
        ]
        stdout = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                compare_runs.main()
        message = str(caught.exception.code)
        self.assertIn("different seeds", message)
        self.assertNotIn("Traceback", message + stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
