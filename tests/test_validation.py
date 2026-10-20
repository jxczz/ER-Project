import contextlib
import io
import math
import os
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import salabim as sim

import config
import validation
from validation import TimedESI, mean_ci, nhamcs_targets, run_replications, weighted_quantile


class WeightedQuantileTests(unittest.TestCase):
    def test_equal_weights_give_ordinary_median_of_odd_length_list(self):
        values = [7, 1, 9, 3, 5]
        self.assertEqual(weighted_quantile(values, [1] * 5, 0.5), statistics.median(values))

    def test_heavily_weighted_value_dominates(self):
        values = [1, 2, 3, 4, 100]
        weights = [1, 1, 1, 1, 1000]
        for q in (0.25, 0.5, 0.75):
            self.assertEqual(weighted_quantile(values, weights, q), 100)

    def test_invalid_inputs_raise(self):
        for values, weights, q in (
            ([], [], 0.5),
            ([1, 2], [1, -1], 0.5),
            ([1, 2], [1, math.nan], 0.5),
            ([1, 2], [1, math.inf], 0.5),
            ([1, 2], [1, 1], -0.1),
            ([1, 2], [1, 1], 1.1),
            ([1, 2], [1, 1], math.nan),
            ([1, 2], [0, 0], 0.5),
            ([1, 2], [1], 0.5),
        ):
            with self.subTest(values=values, weights=weights, q=q):
                with self.assertRaises(ValueError):
                    weighted_quantile(values, weights, q)


class MeanCiTests(unittest.TestCase):
    def test_t_interval_for_three_values(self):
        mean, low, high = mean_ci([1, 2, 3])
        self.assertEqual(mean, 2)
        # sd is 1 and df is 2, so the half-width is 4.303 / sqrt(3).
        half_width = 4.303 / math.sqrt(3)
        self.assertAlmostEqual(high - mean, half_width, places=3)
        self.assertAlmostEqual(mean - low, half_width, places=3)

    def test_single_value_has_nan_bounds(self):
        mean, low, high = mean_ci([5.0])
        self.assertEqual(mean, 5.0)
        self.assertTrue(math.isnan(low))
        self.assertTrue(math.isnan(high))

    def test_nan_values_are_ignored(self):
        self.assertEqual(mean_ci([1, math.nan, 2, 3]), mean_ci([1, 2, 3]))

    def test_other_levels_raise(self):
        with self.assertRaises(ValueError):
            mean_ci([1, 2, 3], level=0.9)


class FormatCiTests(unittest.TestCase):
    def test_interval_is_ascii(self):
        for (low, high), expected in (((1.0, 2.5), "[1.0, 2.5]"), ((float("nan"), 2.0), "n/a")):
            text = validation._format_ci(low, high)
            self.assertEqual(text, expected)
            text.encode("ascii")  # raises UnicodeEncodeError if not ASCII


class NhamcsTargetsTests(unittest.TestCase):
    def setUp(self):
        # Upper-case columns, as in the raw file. The valid IMMEDR 2 records have WAITTIME 10/20/30
        # with weights 1/1/3 (cumulative shares 0.2, 0.4, 1.0), so the weighted median is 30 and the
        # 25th percentile is 20. Every other record should be excluded from WAITTIME.
        self.df = pd.DataFrame(
            [
                # IMMEDR, WAITTIME, LOV, PATWT
                (2, 10, 100, 1),
                (2, 20, 200, 1),
                (2, 30, -9, 3),      # LOV sentinel: still counts for WAITTIME
                (2, -9, 400, 1000),  # WAITTIME sentinel: still counts for LOV
                (2, -7, 0, 1000),    # WAITTIME sentinel, and LOV 0 is not a valid visit length
                (2, 99, 999, 0),     # PATWT 0
                (2, 99, 999, -5),    # PATWT negative
                (7, 99, 999, 1000),  # IMMEDR outside 1-5
                (0, 99, 999, 1000),
                (-8, 99, 999, 1000),
                (4, 0, 50, 2),       # WAITTIME 0 is valid
            ],
            columns=["IMMEDR", "WAITTIME", "LOV", "PATWT"],
        )

    def target(self, targets, group, variable):
        return targets[(targets["group"] == str(group)) & (targets["variable"] == variable)].iloc[0]

    def test_sentinels_weights_and_out_of_range_levels_are_excluded(self):
        targets = nhamcs_targets(self.df)
        self.assertEqual(sorted(targets["group"].unique()), ["1", "2", "3", "4", "5", "all"])

        wait = self.target(targets, 2, "waittime")
        self.assertEqual(wait["n"], 3)
        self.assertEqual((wait["p25"], wait["p50"], wait["p75"]), (20, 30, 30))

        # LOV for IMMEDR 2: 100 (w1), 200 (w1), 400 (w1000); the sentinel and 0 are dropped.
        lov = self.target(targets, 2, "lov")
        self.assertEqual(lov["n"], 3)
        self.assertEqual(lov["p50"], 400)

        self.assertEqual(self.target(targets, 4, "waittime")["p50"], 0)
        empty = self.target(targets, 1, "waittime")
        self.assertEqual(empty["n"], 0)
        self.assertTrue(math.isnan(empty["p50"]))
        self.assertTrue(math.isnan(empty["mean"]))
        self.assertTrue(math.isnan(empty["p90"]))

    def test_weighted_mean_and_p90(self):
        wait = self.target(nhamcs_targets(self.df), 2, "waittime")
        # (10 * 1 + 20 * 1 + 30 * 3) / 5 = 24; cumulative shares 0.2, 0.4, 1.0, so the p90 is 30.
        self.assertAlmostEqual(wait["mean"], 24)
        self.assertEqual(wait["p90"], 30)

    def test_all_group_pools_only_immedr_1_to_5(self):
        # Valid WAITTIME records with IMMEDR 1-5: 0 (w2, IMMEDR 4), 10 (w1), 20 (w1), 30 (w3). The
        # IMMEDR 7, 0 and -8 records (99, w1000) would dominate every statistic if they were pooled.
        wait = self.target(nhamcs_targets(self.df), "all", "waittime")
        self.assertEqual(wait["n"], 4)
        self.assertAlmostEqual(wait["mean"], 120 / 7)
        self.assertEqual(wait["p50"], 20)  # cumulative shares 2/7, 3/7, 4/7, 1
        self.assertEqual(wait["p90"], 30)

        # LOV: 100 (w1), 200 (w1), 400 (w1000) from IMMEDR 2 and 50 (w2) from IMMEDR 4.
        lov = self.target(nhamcs_targets(self.df), "all", "lov")
        self.assertEqual(lov["n"], 4)
        self.assertAlmostEqual(lov["mean"], (100 + 200 + 400 * 1000 + 50 * 2) / 1004)

    def test_input_columns_are_not_modified(self):
        nhamcs_targets(self.df)
        self.assertEqual(list(self.df.columns), ["IMMEDR", "WAITTIME", "LOV", "PATWT"])


class StandInPatient:
    def __init__(self, esi):
        self.esi = esi


class ObservationWindowTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

    def test_only_window_arrivals_are_kept_and_pending_drops_to_zero(self):
        env = sim.Environment(trace=False)
        metrics = TimedESI(env)
        before, at_start, inside, at_end = (StandInPatient(3), StandInPatient(2), StandInPatient(3), StandInPatient(3))

        # The window is [80, 200).
        env.run(till=50)
        metrics.record_arrival(before)
        env.run(till=80)
        metrics.record_arrival(at_start)  # exactly at the window start: observed
        env.run(till=100)
        metrics.record_arrival(inside)
        env.run(till=200)
        metrics.record_arrival(at_end)    # exactly at the window end: not observed
        self.assertEqual(metrics.pending(80, 200), 2)

        env.run(till=230)
        for patient, arrived in ((before, 50), (at_start, 80), (inside, 100), (at_end, 200)):
            metrics.record_wait_time(patient.esi, env.now() - arrived)
        self.assertEqual(metrics.pending(80, 200), 2)  # examined but not yet departed

        env.run(till=300)
        for patient, arrived in ((before, 50), (at_start, 80), (inside, 100)):
            metrics.record_length_of_visit(patient.esi, env.now() - arrived)
            metrics.record_departure(patient)
        self.assertEqual(metrics.pending(80, 200), 0)
        self.assertEqual(metrics.in_ed, {at_end})

        waits, visits = metrics.observations_between(80, 200)
        self.assertEqual(waits[3], [130])
        self.assertEqual(waits[2], [150])
        self.assertEqual(visits[3], [200])
        self.assertEqual(visits[2], [220])
        # The parent ESI lists still record everything.
        self.assertEqual(metrics.wait_times[3], [180, 130, 30])
        self.assertEqual(metrics.lengths_of_visit[3], [250, 200])
        self.assertEqual(metrics.arrivals[3], 3)


class RunReplicationsSmokeTest(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

    def test_two_short_replications(self):
        real_create = validation.create_environment
        animate_at_build = []

        def create(**kwargs):
            animate_at_build.append(config.ANIMATE)
            return real_create(**kwargs)

        with patch.object(config, "ANIMATE", True), patch.object(validation, "create_environment", side_effect=create):
            results = run_replications(2, days=2, warmup_days=1)
            self.assertIs(config.ANIMATE, True)  # restored by run_replications itself

        self.assertEqual(animate_at_build, [False, False])
        self.assertEqual(sorted(results["rep"].unique()), [1, 2])
        self.assertEqual(sorted(results["seed"].unique()), [1, 2])
        self.assertEqual(len(results), 12)
        for rep, rows in results.groupby("rep"):
            with self.subTest(rep=rep):
                self.assertEqual(list(rows["group"]), ["1", "2", "3", "4", "5", "all"])
                by_esi = rows[rows["group"] != "all"]
                overall = rows[rows["group"] == "all"].iloc[0]
                for column in ("d2p_n", "lov_n", "observed_arrivals", "unresolved"):
                    self.assertEqual(overall[column], by_esi[column].sum())
        self.assertGreater(results["d2p_n"].sum(), 0)
        self.assertGreater(results["lov_n"].sum(), 0)
        for column in ("observed_arrivals", "unresolved", "runoff_minutes", "d2p_mean", "d2p_p90", "lov_mean", "lov_p90"):
            self.assertIn(column, results.columns)
        # A statistic is NaN exactly when its group has no observations.
        for name in ("d2p", "lov"):
            for stat in ("median", "mean", "p90"):
                self.assertTrue((results[f"{name}_{stat}"].isna() == (results[f"{name}_n"] == 0)).all())
        # The run-off is a property of the replication, so it is the same on each of its rows.
        self.assertTrue((results.groupby("rep")["runoff_minutes"].nunique() == 1).all())

    def test_no_censoring_every_observed_patient_is_measured(self):
        results = run_replications(2, days=2, warmup_days=1)
        self.assertGreater(results["observed_arrivals"].sum(), 0)
        for row in results.itertuples():
            with self.subTest(rep=row.rep, group=row.group):
                self.assertEqual(row.lov_n, row.observed_arrivals)
                self.assertEqual(row.d2p_n, row.observed_arrivals)
                self.assertEqual(row.unresolved, 0)
                self.assertGreaterEqual(row.runoff_minutes, 0)


class SaveCsvTest(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

    def test_save_csv_writes_per_replication_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "replications.csv")
            argv = ["validation.py", "--reps", "2", "--days", "2", "--warmup-days", "1", "--save-csv", path]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                validation.main()

            self.assertTrue(os.path.exists(path))
            self.assertIn(path, output.getvalue())
            saved = pd.read_csv(path, dtype={"group": str})
            self.assertEqual(list(saved.columns), [
                "rep", "seed", "group",
                "d2p_median", "d2p_mean", "d2p_p90", "d2p_n",
                "lov_median", "lov_mean", "lov_p90", "lov_n",
                "observed_arrivals", "unresolved", "runoff_minutes", "mean_bed_occupancy",
            ])
            self.assertEqual(len(saved), 12)

    def test_missing_directory_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "missing", "replications.csv")
            argv = ["validation.py", "--reps", "2", "--save-csv", path]
            with patch.object(sys, "argv", argv), patch.object(validation, "run_replications") as run, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    validation.main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
