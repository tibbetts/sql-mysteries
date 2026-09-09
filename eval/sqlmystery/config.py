"""Difficulty tiers."""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Tier:
    name: str
    persons: int
    chain_length: int          # persons to identify, witnesses included
    preds_per_edge: tuple[int, int]
    decoys_per_pred: int
    hard_kinds: bool
    dirty: bool

    def scaled(self, persons: int) -> "Tier":
        return replace(self, persons=persons)


TIERS: dict[str, Tier] = {
    t.name: t
    for t in [
        Tier("easy", 10_000, 3, (2, 2), 3, False, False),
        Tier("medium", 50_000, 5, (3, 3), 20, False, False),
        Tier("hard", 200_000, 8, (3, 4), 50, True, True),
        Tier("extreme", 1_000_000, 12, (4, 4), 100, True, True),
    ]
}
