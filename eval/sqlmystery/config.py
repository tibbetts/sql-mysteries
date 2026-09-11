"""Difficulty tiers and knobs."""
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
    branching: int = 0         # rivals per hop that satisfy the full clue set but dead-end or have an alibi
    fuzzy: bool = False        # clue text uses vague quantities the solver must interpret

    def scaled(self, persons: int) -> "Tier":
        return replace(self, persons=persons)

    def with_knobs(self, branching: int | None = None, fuzzy: bool | None = None) -> "Tier":
        b = self.branching if branching is None else branching
        f = self.fuzzy if fuzzy is None else fuzzy
        base = self.name.split("-")[0]
        name = base + (f"-b{b}" if b else "") + ("-fuzzy" if f else "")
        return replace(self, name=name, branching=b, fuzzy=f)


_BASE = [
    Tier("easy", 10_000, 3, (2, 2), 3, False, False),
    Tier("medium", 50_000, 5, (3, 3), 20, False, False),
    Tier("hard", 200_000, 8, (3, 4), 50, True, True),
    Tier("extreme", 1_000_000, 12, (4, 4), 100, True, True),
]

TIERS: dict[str, Tier] = {t.name: t for t in _BASE}
TIERS["hard-branching"] = replace(TIERS["hard"], name="hard-branching", branching=2)
TIERS["hard-fuzzy"] = replace(TIERS["hard"], name="hard-fuzzy", fuzzy=True)
TIERS["hard-full"] = replace(TIERS["hard"], name="hard-full", branching=2, fuzzy=True)
TIERS["extreme-full"] = replace(TIERS["extreme"], name="extreme-full", branching=3, fuzzy=True)
