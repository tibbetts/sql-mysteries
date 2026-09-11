"""Build the clue chain: choose chain persons, sample predicates, plant target, decoys, and rivals."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import Tier
from .dates import add_minutes, rand_time
from .db import Db
from .predicates import REGISTRY, Ctx, DirtyFlags, Predicate, from_json, sample_kinds
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
class Exclusion:
    """Why a rival is not the answer. dead_end: their transcript's clues match nobody.
    alibi: a phone call in the database covers the time of the murder."""
    kind: str
    person_id: int
    sql: str
    clues: list[Clue] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"kind": self.kind, "person_id": self.person_id, "sql": self.sql,
                "clues": [c.to_json() for c in self.clues], "params": dict(self.params)}

    @classmethod
    def from_json(cls, d: dict) -> "Exclusion":
        return cls(d["kind"], d["person_id"], d["sql"], [Clue.from_json(c) for c in d["clues"]], d.get("params", {}))


@dataclass
class Rival:
    person_id: int
    name: str
    exclusion: Exclusion

    def to_json(self) -> dict:
        return {"person_id": self.person_id, "name": self.name, "exclusion": self.exclusion.to_json()}

    @classmethod
    def from_json(cls, d: dict) -> "Rival":
        return cls(d["person_id"], d["name"], Exclusion.from_json(d["exclusion"]))


@dataclass
class Hop:
    role: str                    # witness | murderer | accomplice | mastermind
    person_id: int
    name: str
    clues: list[Clue] = field(default_factory=list)
    rivals: list[Rival] = field(default_factory=list)

    @property
    def predicates(self) -> list[Predicate]:
        return [c.predicate for c in self.clues]

    @property
    def expected_ids(self) -> list[int]:
        return sorted([self.person_id] + [r.person_id for r in self.rivals])

    def to_json(self) -> dict:
        return {"role": self.role, "person_id": self.person_id, "name": self.name,
                "clues": [c.to_json() for c in self.clues], "rivals": [r.to_json() for r in self.rivals]}

    @classmethod
    def from_json(cls, d: dict) -> "Hop":
        return cls(d["role"], d["person_id"], d["name"], [Clue.from_json(c) for c in d["clues"]],
                   [Rival.from_json(r) for r in d.get("rivals", [])])


@dataclass
class Chain:
    hops: list[Hop]
    dirty: DirtyFlags
    crime_time: int = 0          # HHMM, stated in the crime scene report

    @property
    def witnesses(self) -> list[Hop]:
        return [h for h in self.hops if h.role == "witness"]

    @property
    def murderer(self) -> Hop:
        return next(h for h in self.hops if h.role == "murderer")

    @property
    def mastermind(self) -> Hop:
        return self.hops[-1]

    @property
    def protected_ids(self) -> set[int]:
        return {h.person_id for h in self.hops} | {r.person_id for h in self.hops for r in h.rivals}

    def to_json(self) -> dict:
        return {"dirty": self.dirty.to_json(), "crime_time": self.crime_time, "hops": [h.to_json() for h in self.hops]}

    @classmethod
    def from_json(cls, d: dict) -> "Chain":
        return cls([Hop.from_json(h) for h in d["hops"]], DirtyFlags.from_json(d["dirty"]), d.get("crime_time", 0))


def conjunction_sql(preds: list[Predicate], ctx: Ctx) -> str:
    clauses = " AND ".join(f"id IN ({p.sql(ctx)})" for p in preds)
    return f"SELECT id FROM person WHERE {clauses} ORDER BY id"


def alibi_sql(pid: int, crime_date: int, crime_time: int) -> str:
    """A call by pid that started within the hour before the murder and lasted at least an hour."""
    return (f"SELECT DISTINCT caller_id FROM phone_call WHERE caller_id={pid} AND date={crime_date} "
            f"AND start_time BETWEEN {add_minutes(crime_time, -60)} AND {crime_time} AND duration_sec >= 3600")


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
    crime_time = rand_time(rng, 1900, 2300)
    roles = roles_for(tier.chain_length)
    candidates = _unique_name_ids(db)
    rng.shuffle(candidates)
    pool = iter(candidates)
    hops = [Hop(role, pid, db.one("SELECT name FROM person WHERE id=?", (pid,))[0]) for role, pid in zip(roles, pool)]
    protected = {h.person_id for h in hops}
    all_ids = db.ids("SELECT id FROM person ORDER BY id")
    reserved: set[str] = set()
    b = _Builder(db, rng, tier, info, dirty, crime_time, all_ids, reserved, protected, pool)

    for idx, hop in enumerate(hops):
        speakers = _speakers_for(hops, idx)
        branching = tier.branching if hop.role in ("murderer", "accomplice") else 0
        for attempt in range(10):
            try:
                b.plant_hop(hop, speakers, hops[:idx], branching)
                break
            except ChainError:
                hop.clues, hop.rivals = [], []
        else:
            raise ChainError(f"could not build hop {idx} ({hop.role})")

    # Fixpoint: later hops' planting can disturb earlier exclusive predicates. Re-assert and re-check.
    for _ in range(5):
        stable = True
        for hop in hops:
            ctx = Ctx(info=info, dirty=dirty, known=[], reserved=reserved, fuzzy=tier.fuzzy)
            if db.ids(conjunction_sql(hop.predicates, ctx)) != hop.expected_ids:
                stable = False
                for pid in hop.expected_ids:
                    for p in hop.predicates:
                        p.plant(db, rng, ctx, pid)
                b.resolve_collisions(ctx, hop, hops)
        if stable:
            break
    else:
        raise ChainError("chain did not stabilize")
    db.commit()
    return Chain(hops, dirty, crime_time)


def _speakers_for(hops: list[Hop], idx: int) -> list[int | None]:
    """Who states the clues for hop idx. Witnesses come from the crime report; the murderer from the
    witnesses; each later hop from the previous person."""
    hop = hops[idx]
    if hop.role == "witness":
        return [None]
    if hop.role == "murderer":
        return [h.person_id for h in hops if h.role == "witness"]
    return [hops[idx - 1].person_id]


class _Builder:
    def __init__(self, db, rng, tier, info, dirty, crime_time, all_ids, reserved, protected, pool):
        self.db, self.rng, self.tier, self.info, self.dirty = db, rng, tier, info, dirty
        self.crime_time, self.all_ids, self.reserved, self.protected, self.pool = crime_time, all_ids, reserved, protected, pool

    def ctx(self, known, speaker=None) -> Ctx:
        k = known + ([("speaker", speaker)] if speaker is not None else [])
        return Ctx(info=self.info, dirty=self.dirty, known=k, reserved=self.reserved, fuzzy=self.tier.fuzzy)

    def plant_hop(self, hop: Hop, speakers, prior: list[Hop], branching: int) -> None:
        db, rng, tier = self.db, self.rng, self.tier
        n = rng.randint(*tier.preds_per_edge)
        allow_hard = tier.hard_kinds and hop.role != "witness"
        known = [(h.role, h.person_id) for h in prior]
        kinds = sample_kinds(rng, self.ctx(known), n, allow_hard, allow_exclusive=branching == 0)
        clues: list[Clue] = []
        for i, kind in enumerate(kinds):
            speaker = speakers[i % len(speakers)]
            if kind.needs_known and speaker is None:
                raise ChainError("needs_known predicate without speaker")
            c = self.ctx(known, speaker)
            pred = kind.sample(db, rng, hop.person_id, c)
            clues.append(Clue(speaker, pred, pred.text(rng, c)))
        hop.clues = clues
        ctx = self.ctx(known)

        # rivals: satisfy the whole clue set, excluded by alibi (murderer hop) or a dead-end transcript
        hop.rivals = []
        for _ in range(branching):
            rid = self._fresh_person()
            name = db.one("SELECT name FROM person WHERE id=?", (rid,))[0]
            if hop.role == "murderer":
                exclusion = self._plant_alibi(rid)
            else:
                exclusion = self._dead_end(rid, known)
            hop.rivals.append(Rival(rid, name, exclusion))
        if hop.role == "murderer":
            # the true murderer must not have an alibi-shaped call
            db.execute(f"DELETE FROM phone_call WHERE caller_id IN ({alibi_sql(hop.person_id, self.info.crime_date, self.crime_time)})")

        for pid in hop.expected_ids:
            for c in clues:
                c.predicate.plant(db, rng, ctx, pid)

        # single-predicate decoys, disjoint across predicates
        used: set[int] = set(self.protected)
        for c in clues:
            if c.predicate.exclusive:
                continue
            for pid in self._pick(used, tier.decoys_per_pred):
                c.predicate.plant(db, rng, ctx, pid)
        # near-miss decoys: all but one predicate
        non_exclusive = [c.predicate for c in clues if not c.predicate.exclusive]
        if len(clues) >= 2:
            for pid in self._pick(used, max(1, tier.decoys_per_pred // 4)):
                omit = rng.choice(clues).predicate
                for p in non_exclusive:
                    if p is not omit:
                        p.plant(db, rng, ctx, pid)
        self.resolve_collisions(ctx, hop, prior + [hop])

    def _fresh_person(self) -> int:
        for pid in self.pool:
            if pid not in self.protected:
                self.protected.add(pid)
                return pid
        raise ChainError("ran out of unique-name persons")

    def _plant_alibi(self, rid: int) -> Exclusion:
        db, rng = self.db, self.rng
        start = add_minutes(self.crime_time, -rng.randint(15, 55))
        duration = 3600 + rng.randint(0, 3600)
        callee = db.one("SELECT id FROM person WHERE id<>? ORDER BY id LIMIT 1 OFFSET ?", (rid, rng.randrange(len(self.all_ids) - 1)))[0]
        db.execute("INSERT INTO phone_call VALUES (?,?,?,?,?)", (rid, callee, self.info.crime_date, start, duration))
        return Exclusion("alibi", rid, alibi_sql(rid, self.info.crime_date, self.crime_time),
                         params={"start_time": start, "duration_sec": duration})

    def _dead_end(self, rid: int, known) -> Exclusion:
        """Clues in the rival's transcript that match nobody: ordinary clues plus one impossible one."""
        db, rng, tier = self.db, self.rng, self.tier
        n = rng.randint(*tier.preds_per_edge)
        ctx = self.ctx(known, rid)
        kinds = sample_kinds(rng, ctx, max(1, n - 1), tier.hard_kinds, allow_exclusive=False)
        kinds = [k for k in kinds if k.kind not in ("plate_fragment", "gym_status_prefix")]
        subject = rng.choice(self.all_ids)
        clues = [Clue(rid, p, p.text(rng, ctx)) for p in (k.sample(db, rng, subject, ctx) for k in kinds)]
        impossible = self._impossible_predicate(ctx)
        clues.insert(rng.randrange(len(clues) + 1), Clue(rid, impossible, impossible.text(rng, ctx)))
        sql = conjunction_sql([c.predicate for c in clues], ctx)
        if db.ids(sql):
            raise ChainError("dead end matched someone")
        return Exclusion("dead_end", rid, sql, clues=clues)

    def _impossible_predicate(self, ctx: Ctx) -> Predicate:
        db, rng = self.db, self.rng
        for _ in range(50):
            if rng.random() < 0.5:
                frag = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(5))
                p = REGISTRY["plate_fragment"]({"fragment": frag})
            else:
                prefix = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(4))
                p = REGISTRY["gym_status_prefix"]({"status": rng.choice(self.info.gym_statuses), "prefix": prefix})
            if not db.ids(p.sql(ctx)):
                return p
        raise ChainError("could not find an impossible predicate")

    def _pick(self, used: set[int], k: int) -> list[int]:
        out: list[int] = []
        tries = 0
        while len(out) < k and tries < k * 20:
            pid = self.rng.choice(self.all_ids)
            tries += 1
            if pid in used:
                continue
            used.add(pid)
            out.append(pid)
        return out

    def resolve_collisions(self, ctx: Ctx, hop: Hop, hops: list[Hop]) -> None:
        db, rng = self.db, self.rng
        hop_groups = {h.person_id: {p.group for p in h.predicates} for h in hops}
        for r in [r for h in hops for r in h.rivals]:
            hop_groups[r.person_id] = hop_groups.get(next(h.person_id for h in hops if r in h.rivals), set())
        expected = hop.expected_ids
        for _ in range(10):
            matched = db.ids(conjunction_sql(hop.predicates, ctx))
            missing = [pid for pid in expected if pid not in matched]
            colliders = [pid for pid in matched if pid not in expected]
            if not missing and not colliders:
                return
            for pid in missing:
                for p in hop.predicates:
                    p.plant(db, rng, ctx, pid)
            for pid in colliders:
                choices = hop.predicates
                if pid in self.protected:
                    choices = [p for p in hop.predicates if p.group not in hop_groups.get(pid, set())]
                    if not choices:
                        raise ChainError("collision with a protected person on a shared predicate group")
                rng.choice(choices).unplant(db, rng, ctx, pid)
        raise ChainError("could not resolve collisions")
