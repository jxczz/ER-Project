import math
import statistics
import unittest
from unittest.mock import patch

import pandas as pd
import salabim as sim

import config
import validation
from validation import TimedESI, nhamcs_targets, run_replications, weighted_quantile


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

    def target(self, targets, level, variable):
        return targets[(targets["immedr"] == level) & (targets["variable"] == variable)].iloc[0]

    def test_sentinels_weights_and_out_of_range_levels_are_excluded(self):
        targets = nhamcs_targets(self.df)
        self.assertEqual(sorted(targets["immedr"].unique()), [1, 2, 3, 4, 5])

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

    def test_input_columns_are_not_modified(self):
        nhamcs_targets(self.df)
        self.assertEqual(list(self.df.columns), ["IMMEDR", "WAITTIME", "LOV", "PATWT"])


class WarmupExclusionTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

    def test_pre_warmup_arrivals_are_dropped(self):
        env = sim.Environment(trace=False)
        metrics = TimedESI(env)

        env.run(till=100)
        metrics.record_wait_time(3, 30)  # arrived at 70, before the warm-up ends at 80
        metrics.record_wait_time(3, 5)   # arrived at 95
        env.run(till=200)
        metrics.record_length_of_visit(3, 150)  # arrived at 50
        metrics.record_length_of_visit(3, 100)  # arrived at 100
        metrics.record_length_of_visit(2, 120)  # arrived at 80, exactly at the warm-up end

        waits, visits = metrics.observations_since(80)
        self.assertEqual(waits[3], [5])
        self.assertEqual(visits[3], [100])
        self.assertEqual(visits[2], [120])
        # The parent ESI lists still record everything.
        self.assertEqual(metrics.wait_times[3], [30, 5])
        self.assertEqual(metrics.lengths_of_visit[3], [150, 100])


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
        self.assertEqual(len(results), 10)
        self.assertGreater(results["d2p_n"].sum(), 0)
        self.assertGreater(results["lov_n"].sum(), 0)


if __name__ == "__main__":
    unittest.main()
