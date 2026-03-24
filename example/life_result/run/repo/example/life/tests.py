from __future__ import annotations

import unittest

from life import LifeWorld, run_simulation, step_world


class LifeTests(unittest.TestCase):
    def test_seed_is_deterministic(self) -> None:
        first = LifeWorld.from_seed(10, 12, 99)
        second = LifeWorld.from_seed(10, 12, 99)
        self.assertEqual(first.rows, second.rows)

    def test_step_preserves_dimensions(self) -> None:
        world = LifeWorld.from_seed(15, 17, 7)
        stepped = step_world(world)
        self.assertEqual((stepped.width, stepped.height), (15, 17))

    def test_simulation_known_checksums(self) -> None:
        self.assertEqual(run_simulation(12, 12, 8, 3), 594649359)
        self.assertEqual(run_simulation(18, 10, 12, 5), 760575121)
        self.assertEqual(run_simulation(16, 16, 6, 13), 209157710)


if __name__ == "__main__":
    unittest.main()
