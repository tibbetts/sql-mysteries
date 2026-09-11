"""Prove an emitted instance has exactly one answer by re-running the stored reference SQL."""
from __future__ import annotations

import json
from pathlib import Path

from .chain import Chain, alibi_sql, conjunction_sql
from .db import Db
from .predicates import Ctx


class VerificationError(AssertionError):
    pass


def verify(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    answer = json.loads((out_dir / "answer.json").read_text())
    chain = Chain.from_json(answer)
    db = Db(str(out_dir / "mystery.db"), readonly=True)
    try:
        ctx = Ctx(info=None, dirty=chain.dirty, known=[])
        report = {"hops": []}
        for i, hop in enumerate(chain.hops):
            got = db.ids(conjunction_sql(hop.predicates, ctx))
            report["hops"].append({"role": hop.role, "expected": hop.expected_ids, "got": got})
            if got != hop.expected_ids:
                raise VerificationError(f"hop {i} ({hop.role}) resolves to {got}, expected {hop.expected_ids}: {hop.predicates}")
            for name in [hop.name] + [r.name for r in hop.rivals]:
                n = db.one("SELECT count(*) FROM person WHERE lower(trim(name))=lower(trim(?))", (name,))[0]
                if n != 1:
                    raise VerificationError(f"hop {i} name {name!r} matches {n} persons")
            for r in hop.rivals:
                ex = r.exclusion
                got = db.ids(ex.sql)
                if ex.kind == "dead_end" and got != []:
                    raise VerificationError(f"hop {i} rival {r.name}: dead-end clues match {got}")
                if ex.kind == "alibi" and got != [r.person_id]:
                    raise VerificationError(f"hop {i} rival {r.name}: alibi not found in phone_call")
            if hop.role == "murderer" and any(r.exclusion.kind == "alibi" for r in hop.rivals):
                if db.ids(alibi_sql(hop.person_id, answer["crime_date"], chain.crime_time)):
                    raise VerificationError("the murderer has an alibi-shaped call")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' OR name='solution'").fetchone():
            raise VerificationError("database contains a trigger or solution table")
        return report
    finally:
        db.close()
