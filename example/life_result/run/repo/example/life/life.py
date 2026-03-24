from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LifeWorld:
    width: int
    height: int
    rows: list[list[int]]

    @classmethod
    def from_seed(cls, width: int, height: int, seed: int) -> "LifeWorld":
        state = seed & 0x7FFFFFFF
        rows: list[list[int]] = []
        for _ in range(height):
            row: list[int] = []
            for _ in range(width):
                state = (1103515245 * state + 12345) & 0x7FFFFFFF
                row.append(1 if state % 5 in (0, 1) else 0)
            rows.append(row)
        return cls(width=width, height=height, rows=rows)

    def checksum(self) -> int:
        total = 0
        for row in self.rows:
            for value in row:
                total = (total * 131 + value) % 1_000_000_007
        return total


def step_world(world: LifeWorld) -> LifeWorld:
    next_rows: list[list[int]] = []
    width = world.width
    height = world.height
    for y in range(height):
        next_row: list[int] = []
        for x in range(width):
            neighbors = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbors += world.rows[(y + dy) % height][(x + dx) % width]
            cell = world.rows[y][x]
            if cell:
                next_row.append(1 if neighbors in (2, 3) else 0)
            else:
                next_row.append(1 if neighbors == 3 else 0)
        next_rows.append(next_row)
    return LifeWorld(width=width, height=height, rows=next_rows)


def run_simulation(width: int, height: int, steps: int, seed: int) -> int:
    world = LifeWorld.from_seed(width, height, seed)
    for _ in range(steps):
        world = step_world(world)
    return world.checksum()
