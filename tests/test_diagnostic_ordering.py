import random
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import config as cfg
from simulation.data_reader import ERDataReader
from simulation.patient import INITIAL_TEST_COMBINATIONS, Patient, draw_initial_test_types

COMBINATIONS = ("none", "lab", "imaging", "both")
LAB_COLUMNS = (
    "ABG BAC BMP BNP BUNCREAT CARDENZ CBC CMP BLOODCX DDIMER ELECTROL GLUCOSE LACTATE LFT PTTINR OTHERBLD"
).split()


def nhamcs_initial_test_shares(df):
    """Return ({esi: {combination: PATWT share}}, {esi: unweighted n}) for IMMEDR 1-5, excluding DIAGSCRN == 2."""
    df = df[df["IMMEDR"].isin([1, 2, 3, 4, 5]) & (df["DIAGSCRN"] != 2)]
    lab = df[LAB_COLUMNS].eq(1).any(axis=1)
    imaging = df["ANYIMAGE"].eq(1)
    combination = pd.Series("none", index=df.index)
    combination[lab & ~imaging] = "lab"
    combination[~lab & imaging] = "imaging"
    combination[lab & imaging] = "both"
    weights = df.assign(combination=combination).groupby(["IMMEDR", "combination"])["PATWT"].sum().unstack(fill_value=0)
    shares = weights.div(weights.sum(axis=1), axis=0)
    counts = df.groupby("IMMEDR").size()
    return (
        {int(esi): {name: float(shares.loc[esi].get(name, 0.0)) for name in COMBINATIONS} for esi in shares.index},
        {int(esi): int(n) for esi, n in counts.items()},
    )


class InitialTestWeightTests(unittest.TestCase):
    def test_weights_match_nhamcs(self):
        path = ERDataReader(cfg.DATASET_PATH).filepath
        if not path.exists():
            self.skipTest(f"dataset not found: {path}")
        shares, _counts = nhamcs_initial_test_shares(pd.read_parquet(path))
        self.assertEqual(sorted(shares), [1, 2, 3, 4, 5])
        for esi, expected in shares.items():
            for name in COMBINATIONS:
                with self.subTest(esi=esi, combination=name):
                    self.assertAlmostEqual(cfg.INITIAL_TEST_WEIGHTS[esi][name], expected[name], delta=0.0015)

    def test_each_level_sums_to_one(self):
        self.assertEqual(sorted(cfg.INITIAL_TEST_WEIGHTS), [1, 2, 3, 4, 5])
        for esi, weights in cfg.INITIAL_TEST_WEIGHTS.items():
            with self.subTest(esi=esi):
                self.assertEqual(set(weights), set(COMBINATIONS))
                self.assertAlmostEqual(sum(weights.values()), 1, delta=0.002)

    def test_combinations_cover_test_types(self):
        # The joint draw must order exactly the configured test types, so a renamed or added type fails here.
        self.assertEqual(set(INITIAL_TEST_COMBINATIONS), set(COMBINATIONS))
        self.assertEqual(set().union(*INITIAL_TEST_COMBINATIONS.values()), set(cfg.TEST_TYPES))

    def test_draws_match_weights(self):
        random.seed(12345)
        draws = 20_000
        for esi in (1, 5):
            counts = Counter(tuple(draw_initial_test_types(esi)) for _ in range(draws))
            observed = {name: counts[tuple(INITIAL_TEST_COMBINATIONS[name])] for name in COMBINATIONS}
            self.assertEqual(sum(observed.values()), draws)
            for name in COMBINATIONS:
                with self.subTest(esi=esi, combination=name):
                    self.assertAlmostEqual(observed[name] / draws, cfg.INITIAL_TEST_WEIGHTS[esi][name], delta=0.015)


class OrderTestsTests(unittest.TestCase):
    def setUp(self):
        self.test_types = {
            name: {"capacity": 1, "mean_time": 1, "follow_up_probability": dict.fromkeys(range(1, 6), 0)}
            for name in ("lab", "imaging")
        }
        for patcher in (
            patch.object(cfg, "TEST_TYPES", self.test_types),
            # Record what was ordered without creating salabim components.
            patch("simulation.patient.DiagnosticTest", side_effect=lambda **kwargs: SimpleNamespace(**kwargs)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def order(self, follow_up):
        patient = SimpleNamespace(esi=3, testing={"lab": "lab pool", "imaging": "imaging pool"}, env=None, tests=[])
        return [(test.test_type, test.follow_up) for test in Patient._order_tests(patient, follow_up)]

    def test_initial_tests_come_from_joint_draw(self):
        with patch("simulation.patient.draw_initial_test_types", return_value=["lab", "imaging"]) as draw:
            self.assertEqual(self.order(follow_up=False), [("lab", False), ("imaging", False)])
        draw.assert_called_once_with(3)

    def test_follow_up_uses_follow_up_probability(self):
        self.test_types["imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1)
        with patch("simulation.patient.draw_initial_test_types") as draw:
            for _ in range(50):
                self.assertEqual(self.order(follow_up=True), [("imaging", True)])
        draw.assert_not_called()

    def test_follow_up_orders_at_least_one_test(self):
        random.seed(0)
        # Chances too small to fire: the fallback picks by the follow-up chances, so always imaging.
        self.test_types["imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1e-12)
        for _ in range(50):
            self.assertEqual(self.order(follow_up=True), [("imaging", True)])
        # All chances zero: still exactly one test.
        self.test_types["imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 0)
        for _ in range(50):
            ordered = self.order(follow_up=True)
            self.assertEqual(len(ordered), 1)
            self.assertIn(ordered[0], [("lab", True), ("imaging", True)])


if __name__ == "__main__":
    unittest.main()
