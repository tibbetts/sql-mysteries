"""Prove an emitted instance has exactly one answer by re-running the stored reference SQL."""
from __future__ import annotations

import json
from pathlib import Path

from .chain import Chain, conjunction_sql
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
            report["hops"].append({"role": hop.role, "expected": hop.person_id, "got": got})
            if got != [hop.person_id]:
                raise VerificationError(f"hop {i} ({hop.role}) resolves to {got}, expected [{hop.person_id}]: {hop.predicates}")
            n = db.one("SELECT count(*) FROM person WHERE lower(trim(name))=lower(trim(?))", (hop.name,))[0]
            if n != 1:
                raise VerificationError(f"hop {i} name {hop.name!r} matches {n} persons")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' OR name='solution'").fetchone():
            raise VerificationError("database contains a trigger or solution table")
        return report
    finally:
        db.close()
