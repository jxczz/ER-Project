import random
import unittest

import salabim as sim

from simulation.environment import create_environment

SALABIM_DEFAULT_SEED = 1234567


def first_draw_after_seeding(seed):
    random.seed(seed)
    return random.random()


class EnvironmentSeedTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)

    def first_draw_after_environment(self, *args, **kwargs):
        create_environment(*args, **kwargs)
        return random.random()

    def test_seed_is_applied_when_environment_is_created(self):
        for seed in (111, 222, 98765):
            with self.subTest(seed=seed):
                random.seed(0)  # any earlier seeding is overridden by the environment
                self.assertEqual(self.first_draw_after_environment(random_seed=seed), first_draw_after_seeding(seed))

    def test_different_seeds_give_different_draws(self):
        self.assertNotEqual(
            self.first_draw_after_environment(random_seed=111),
            self.first_draw_after_environment(random_seed=222),
        )

    def test_no_seed_keeps_salabim_default(self):
        random.seed(0)
        self.assertEqual(self.first_draw_after_environment(), first_draw_after_seeding(SALABIM_DEFAULT_SEED))


if __name__ == "__main__":
    unittest.main()
