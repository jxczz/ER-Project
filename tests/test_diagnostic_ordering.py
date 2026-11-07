import copy
import random
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import config as cfg
from simulation.data_reader import ERDataReader
from simulation.patient import INITIAL_TEST_COMBINATIONS, Patient, draw_initial_test_types

COMBINATIONS = (
    "none", "lab", "ct", "other imaging",
    "lab+ct", "lab+other imaging", "ct+other imaging", "lab+ct+other imaging",
)
LAB_COLUMNS = (
    "ABG BAC BMP BNP BUNCREAT CARDENZ CBC CMP BLOODCX DDIMER ELECTROL GLUCOSE LACTATE LFT PTTINR OTHERBLD"
).split()
OTHER_IMAGING_COLUMNS = ["XRAY", "ULTRASND", "MRI", "OTHIMAGE"]
# The table before CT was split out (lab x ANYIMAGE), which the new combinations must collapse back to.
OLD_INITIAL_TEST_WEIGHTS = {
    1: {"none": 0.110, "lab": 0.107, "imaging": 0.084, "both": 0.699},
    2: {"none": 0.095, "lab": 0.171, "imaging": 0.069, "both": 0.665},
    3: {"none": 0.189, "lab": 0.160, "imaging": 0.168, "both": 0.483},
    4: {"none": 0.568, "lab": 0.045, "imaging": 0.304, "both": 0.084},
    5: {"none": 0.798, "lab": 0.058, "imaging": 0.095, "both": 0.049},
}


def eligible_visits(df):
    return df[df["IMMEDR"].isin([1, 2, 3, 4, 5]) & (df["DIAGSCRN"] != 2)]


def nhamcs_initial_test_shares(df):
    """Return ({esi: {combination: PATWT share}}, {esi: unweighted n}) for IMMEDR 1-5, excluding DIAGSCRN == 2."""
    df = eligible_visits(df)
    flags = {
        "lab": df[LAB_COLUMNS].eq(1).any(axis=1),
        "ct": df["CATSCAN"].eq(1),
        "other imaging": df[OTHER_IMAGING_COLUMNS].eq(1).any(axis=1),
    }
    combination = pd.Series("none", index=df.index)
    for name, types in INITIAL_TEST_COMBINATIONS.items():
        ordered = pd.Series(True, index=df.index)
        for test_type, flag in flags.items():
            ordered &= flag if test_type in types else ~flag
        combination[ordered] = name
    weights = df.assign(combination=combination).groupby(["IMMEDR", "combination"])["PATWT"].sum().unstack(fill_value=0)
    shares = weights.div(weights.sum(axis=1), axis=0)
    counts = df.groupby("IMMEDR").size()
    return (
        {int(esi): {name: float(shares.loc[esi].get(name, 0.0)) for name in COMBINATIONS} for esi in shares.index},
        {int(esi): int(n) for esi, n in counts.items()},
    )


def collapse_to_lab_and_imaging(shares):
    """Sum 8-way shares into the old none/lab/imaging/both, where imaging = CT or other imaging."""
    collapsed = dict.fromkeys(("none", "lab", "imaging", "both"), 0.0)
    for name, share in shares.items():
        types = set(INITIAL_TEST_COMBINATIONS[name])
        lab, imaging = "lab" in types, bool(types & {"ct", "other imaging"})
        collapsed["both" if lab and imaging else "lab" if lab else "imaging" if imaging else "none"] += share
    return collapsed


class InitialTestWeightTests(unittest.TestCase):
    def read_nhamcs(self):
        path = ERDataReader(cfg.DATASET_PATH).filepath
        if not path.exists():
            self.skipTest(f"dataset not found: {path}")
        return pd.read_parquet(path)

    def test_weights_match_nhamcs(self):
        shares, _counts = nhamcs_initial_test_shares(self.read_nhamcs())
        self.assertEqual(sorted(shares), [1, 2, 3, 4, 5])
        for esi, expected in shares.items():
            for name in COMBINATIONS:
                with self.subTest(esi=esi, combination=name):
                    self.assertAlmostEqual(cfg.INITIAL_TEST_WEIGHTS[esi][name], expected[name], delta=0.0015)

    def test_ct_and_other_imaging_are_exactly_anyimage(self):
        df = eligible_visits(self.read_nhamcs())
        split = df["CATSCAN"].eq(1) | df[OTHER_IMAGING_COLUMNS].eq(1).any(axis=1)
        self.assertTrue(split.equals(df["ANYIMAGE"].eq(1)))

    def test_collapsing_reproduces_lab_and_imaging_shares(self):
        shares, _counts = nhamcs_initial_test_shares(self.read_nhamcs())
        for esi in range(1, 6):
            from_data = collapse_to_lab_and_imaging(shares[esi])
            from_config = collapse_to_lab_and_imaging(cfg.INITIAL_TEST_WEIGHTS[esi])
            for name, old in OLD_INITIAL_TEST_WEIGHTS[esi].items():
                with self.subTest(esi=esi, combination=name):
                    # The old table is the unrounded shares rounded to 3 decimals.
                    self.assertAlmostEqual(from_data[name], old, delta=0.0005)
                    # Each collapsed share adds up to four entries rounded to 3 decimals.
                    self.assertAlmostEqual(from_config[name], old, delta=0.0025)

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
            for name in ("lab", "ct", "other imaging")
        }
        for patcher in (
            patch.object(cfg, "TEST_TYPES", self.test_types),
            # Record what was ordered without creating salabim components.
            patch("simulation.patient.DiagnosticTest", side_effect=lambda **kwargs: SimpleNamespace(**kwargs)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def order(self, follow_up):
        testing = {"lab": "lab pool", "ct": "ct pool", "other imaging": "other imaging pool"}
        patient = SimpleNamespace(esi=3, testing=testing, env=None, tests=[])
        return [(test.test_type, test.follow_up) for test in Patient._order_tests(patient, follow_up)]

    def test_initial_tests_come_from_joint_draw(self):
        with patch("simulation.patient.draw_initial_test_types", return_value=["lab", "other imaging"]) as draw:
            self.assertEqual(self.order(follow_up=False), [("lab", False), ("other imaging", False)])
        draw.assert_called_once_with(3)

    def test_follow_up_uses_follow_up_probability(self):
        self.test_types["other imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1)
        with patch("simulation.patient.draw_initial_test_types") as draw:
            for _ in range(50):
                self.assertEqual(self.order(follow_up=True), [("other imaging", True)])
        draw.assert_not_called()

    def test_follow_up_orders_at_least_one_test(self):
        random.seed(0)
        # Chances too small to fire: the fallback picks by the follow-up chances, so always other imaging.
        self.test_types["other imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 1e-12)
        for _ in range(50):
            self.assertEqual(self.order(follow_up=True), [("other imaging", True)])
        # All chances zero: still exactly one test, picked from every type but CT.
        self.test_types["other imaging"]["follow_up_probability"] = dict.fromkeys(range(1, 6), 0)
        picked = Counter()
        for _ in range(200):
            ordered = self.order(follow_up=True)
            self.assertEqual(len(ordered), 1)
            picked[ordered[0]] += 1
        self.assertEqual(set(picked), {("lab", True), ("other imaging", True)})


class CtFollowUpTests(unittest.TestCase):
    def setUp(self):
        self.test_types = copy.deepcopy(cfg.TEST_TYPES)
        for patcher in (
            patch.object(cfg, "TEST_TYPES", self.test_types),
            patch("simulation.patient.DiagnosticTest", side_effect=lambda **kwargs: SimpleNamespace(**kwargs)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def follow_ups(self, esi):
        patient = SimpleNamespace(esi=esi, testing=dict.fromkeys(self.test_types), env=None, tests=[])
        return [test.test_type for test in Patient._order_tests(patient, follow_up=True)]

    def test_configured_ct_follow_up_probability_is_zero(self):
        self.assertEqual(set(cfg.TEST_TYPES["ct"]["follow_up_probability"].values()), {0})

    def test_ct_is_never_a_follow_up(self):
        random.seed(7)
        for esi in range(1, 6):
            for _ in range(500):
                ordered = self.follow_ups(esi)
                with self.subTest(esi=esi):
                    self.assertTrue(ordered)
                    self.assertNotIn("ct", ordered)

    def test_fallback_never_picks_ct(self):
        random.seed(7)
        # Every chance zero, so the fallback picks uniformly among the eligible types.
        for spec in self.test_types.values():
            spec["follow_up_probability"] = dict.fromkeys(range(1, 6), 0)
        picked = Counter(tuple(self.follow_ups(3)) for _ in range(500))
        self.assertEqual(set(picked), {("lab",), ("other imaging",)})


if __name__ == "__main__":
    unittest.main()
