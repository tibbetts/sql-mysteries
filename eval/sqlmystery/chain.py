"""Build the clue chain: choose chain persons, sample predicates, plant target and decoys."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import Tier
from .db import Db
from .predicates import Ctx, DirtyFlags, Predicate, from_json, sample_kinds
from .world import WorldInfo


@dataclass
class Clue:
    speaker: int | None          # person who states it; None means the crime scene report
    predicate: Predicate
    rendered: str                # the English sentence fragment used in the narrative

    def to_json(self) -> dict:
        return {"speaker": self.speaker, "predicate": self.predicate.to_json(), "rendered": self.rendered}

    @classmethod
    def from_json(cls, d: dict) -> "Clue":
        return cls(d["speaker"], from_json(d["predicate"]), d["rendered"])


@dataclass
class Hop:
    role: str                    # witness | murderer | accomplice | mastermind
    person_id: int
    name: str
    clues: list[Clue] = field(default_factory=list)

    @property
    def predicates(self) -> list[Predicate]:
        return [c.predicate for c in self.clues]

    def to_json(self) -> dict:
        return {"role": self.role, "person_id": self.person_id, "name": self.name, "clues": [c.to_json() for c in self.clues]}

    @classmethod
    def from_json(cls, d: dict) -> "Hop":
        return cls(d["role"], d["person_id"], d["name"], [Clue.from_json(c) for c in d["clues"]])


@dataclass
class Chain:
    hops: list[Hop]
    dirty: DirtyFlags

    @property
    def witnesses(self) -> list[Hop]:
        return [h for h in self.hops if h.role == "witness"]

    @property
    def murderer(self) -> Hop:
        return next(h for h in self.hops if h.role == "murderer")

    @property
    def mastermind(self) -> Hop:
        return self.hops[-1]

    def to_json(self) -> dict:
        return {"dirty": self.dirty.to_json(), "hops": [h.to_json() for h in self.hops]}

    @classmethod
    def from_json(cls, d: dict) -> "Chain":
        return cls([Hop.from_json(h) for h in d["hops"]], DirtyFlags.from_json(d["dirty"]))


def conjunction_sql(preds: list[Predicate], ctx: Ctx) -> str:
    clauses = " AND ".join(f"id IN ({p.sql(ctx)})" for p in preds)
    return f"SELECT id FROM person WHERE {clauses} ORDER BY id"


class ChainError(RuntimeError):
    pass


def roles_for(length: int) -> list[str]:
    if length < 3:
        raise ValueError("chain_length must be at least 3")
    witnesses = 1 if length == 3 else 2
    return ["witness"] * witnesses + ["murderer"] + ["accomplice"] * (length - witnesses - 2) + ["mastermind"]


def _unique_name_ids(db: Db) -> list[int]:
    return db.ids("SELECT id FROM person WHERE name IN (SELECT name FROM person GROUP BY name HAVING COUNT(*)=1) ORDER BY id")


def build_chain(db: Db, rng: random.Random, tier: Tier, info: WorldInfo) -> Chain:
    dirty = DirtyFlags(event_date_text=tier.dirty, checkin_time_text=tier.dirty and tier.chain_length >= 12)
    roles = roles_for(tier.chain_length)
    candidates = _unique_name_ids(db)
    chain_ids = rng.sample(candidates, len(roles))
    chain_set = set(chain_ids)
    hops = [Hop(role, pid, db.one("SELECT name FROM person WHERE id=?", (pid,))[0]) for role, pid in zip(roles, chain_ids)]
    all_ids = db.ids("SELECT id FROM person ORDER BY id")
    reserved: set[str] = set()

    for idx, hop in enumerate(hops):
        speakers = _speakers_for(hops, idx)
        for attempt in range(10):
            try:
                _plant_hop(db, rng, tier, info, dirty, hop, speakers, hops[:idx], chain_set, all_ids, reserved)
                break
            except ChainError:
                hop.clues = []
        else:
            raise ChainError(f"could not build hop {idx} ({hop.role})")

    # Fixpoint: later hops' planting can disturb earlier exclusive predicates. Re-assert and re-check.
    for _ in range(5):
        stable = True
        for hop in hops:
            ctx = Ctx(info=info, dirty=dirty, known=[], reserved=reserved)
            if db.ids(conjunction_sql(hop.predicates, ctx)) != [hop.person_id]:
                stable = False
                for p in hop.predicates:
                    p.plant(db, rng, ctx, hop.person_id)
                _resolve_collisions(db, rng, ctx, hop, hops, chain_set)
        if stable:
            break
    else:
        raise ChainError("chain did not stabilize")
    db.commit()
    return Chain(hops, dirty)


def _speakers_for(hops: list[Hop], idx: int) -> list[int | None]:
    """Who states the clues for hop idx. Witnesses come from the crime report; the murderer from the
    witnesses; each later hop from the previous person."""
    hop = hops[idx]
    if hop.role == "witness":
        return [None]
    if hop.role == "murderer":
        return [h.person_id for h in hops if h.role == "witness"]
    return [hops[idx - 1].person_id]


def _plant_hop(db, rng, tier, info, dirty, hop, speakers, prior, chain_set, all_ids, reserved):
    n = rng.randint(*tier.preds_per_edge)
    allow_hard = tier.hard_kinds and hop.role != "witness"
    known = [(h.role, h.person_id) for h in prior]
    base_ctx = Ctx(info=info, dirty=dirty, known=known, reserved=reserved)
    kinds = sample_kinds(rng, base_ctx, n, allow_hard)
    clues: list[Clue] = []
    for i, kind in enumerate(kinds):
        speaker = speakers[i % len(speakers)]
        ctx = Ctx(info=info, dirty=dirty, known=known + ([("speaker", speaker)] if speaker is not None else []), reserved=reserved)
        if kind.needs_known and speaker is None:
            raise ChainError("needs_known predicate without speaker")
        pred = kind.sample(db, rng, hop.person_id, ctx)
        clues.append(Clue(speaker, pred, pred.text(rng, ctx)))
    hop.clues = clues
    ctx = Ctx(info=info, dirty=dirty, known=known, reserved=reserved)

    for c in clues:
        c.predicate.plant(db, rng, ctx, hop.person_id)

    # single-predicate decoys, disjoint across predicates
    used: set[int] = set(chain_set)
    for c in clues:
        if c.predicate.exclusive:
            continue
        for pid in _pick(rng, all_ids, used, tier.decoys_per_pred):
            c.predicate.plant(db, rng, ctx, pid)
    # near-miss decoys: all but one predicate
    non_exclusive = [c.predicate for c in clues if not c.predicate.exclusive]
    if len(clues) >= 2:
        for pid in _pick(rng, all_ids, used, max(1, tier.decoys_per_pred // 4)):
            omit = rng.choice(clues).predicate
            for p in non_exclusive:
                if p is not omit:
                    p.plant(db, rng, ctx, pid)
            if omit.exclusive:
                # every non-exclusive predicate was planted; the exclusive one is missing by nature
                pass
            elif len(non_exclusive) == len(clues):
                pass
    _resolve_collisions(db, rng, ctx, hop, prior + [hop], chain_set)


def _pick(rng: random.Random, all_ids: list[int], used: set[int], k: int) -> list[int]:
    out: list[int] = []
    tries = 0
    while len(out) < k and tries < k * 20:
        pid = rng.choice(all_ids)
        tries += 1
        if pid in used:
            continue
        used.add(pid)
        out.append(pid)
    return out


def _resolve_collisions(db, rng, ctx, hop, hops, chain_set):
    hop_groups = {h.person_id: {p.group for p in h.predicates} for h in hops}
    for _ in range(10):
        matched = db.ids(conjunction_sql(hop.predicates, ctx))
        colliders = [pid for pid in matched if pid != hop.person_id]
        if hop.person_id in matched and not colliders:
            return
        if hop.person_id not in matched:
            for p in hop.predicates:
                p.plant(db, rng, ctx, hop.person_id)
        for pid in colliders:
            choices = hop.predicates
            if pid in chain_set:
                choices = [p for p in hop.predicates if p.group not in hop_groups.get(pid, set())]
                if not choices:
                    raise ChainError("collision with chain person on shared predicate group")
            rng.choice(choices).unplant(db, rng, ctx, pid)
    raise ChainError("could not resolve collisions")
