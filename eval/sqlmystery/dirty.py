"""Dirty-data transforms applied after planting. Chain persons are never touched, and the
predicate SQL is dirty-aware, so verification still proves solvability afterwards."""
from __future__ import annotations

import random

from .chain import Chain
from .db import Db
from .dates import iso
from .predicates import DirtyFlags


def apply_dirty(db: Db, rng: random.Random, chain: Chain, flags: DirtyFlags) -> None:
    protect = {h.person_id for h in chain.hops}
    if flags.event_date_text:
        _event_dates_to_text(db)
    if flags.checkin_time_text:
        _mix_checkin_time_formats(db, rng)
    _mangle_names(db, rng, protect, rate=0.05)
    _duplicate_persons(db, rng, protect, rate=0.03)
    _null_heights(db, rng, protect, rate=0.02)
    db.commit()


def _event_dates_to_text(db: Db) -> None:
    db.execute("UPDATE facebook_event_checkin SET date = substr(date,1,4) || '-' || substr(date,5,2) || '-' || substr(date,7,2)")


def _mix_checkin_time_formats(db: Db, rng: random.Random) -> None:
    # half the rows become 'HH:MM' text; the rest stay HHMM integers
    rows = db.execute("SELECT rowid, check_in_time, check_out_time FROM get_fit_now_check_in").fetchall()
    updates = []
    for rowid, t_in, t_out in rows:
        if rng.random() < 0.5:
            updates.append((f"{t_in // 100:02d}:{t_in % 100:02d}", f"{t_out // 100:02d}:{t_out % 100:02d}", rowid))
    db.executemany("UPDATE get_fit_now_check_in SET check_in_time=?, check_out_time=? WHERE rowid=?", updates)


def _mangle_names(db: Db, rng: random.Random, protect: set[int], rate: float) -> None:
    rows = db.execute("SELECT id, name FROM person").fetchall()
    updates = []
    for pid, name in rows:
        if pid in protect or rng.random() >= rate:
            continue
        style = rng.random()
        if style < 0.4:
            new = name + " "
        elif style < 0.7:
            new = name.lower()
        elif style < 0.85:
            new = name.upper()
        else:
            new = "  " + name
        updates.append((new, pid))
    db.executemany("UPDATE person SET name=? WHERE id=?", updates)


def _duplicate_persons(db: Db, rng: random.Random, protect: set[int], rate: float) -> None:
    rows = db.execute("SELECT id, name, address_number, address_street_name FROM person").fetchall()
    next_id = db.one("SELECT MAX(id) FROM person")[0] + 1
    inserts = []
    for pid, name, num, street in rows:
        if pid in protect or rng.random() >= rate:
            continue
        inserts.append((next_id, name, None, num, street, None))
        next_id += 1
    db.executemany("INSERT INTO person VALUES (?,?,?,?,?,?)", inserts)


def _null_heights(db: Db, rng: random.Random, protect: set[int], rate: float) -> None:
    rows = db.execute("SELECT p.license_id FROM person p WHERE p.license_id IS NOT NULL AND p.id NOT IN (%s)"
                      % ",".join(str(i) for i in protect)).fetchall()
    ids = [(r[0],) for r in rows if rng.random() < rate]
    db.executemany("UPDATE drivers_license SET height=NULL WHERE id=?", ids)
