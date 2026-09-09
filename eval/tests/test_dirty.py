import random

import pytest

from sqlmystery.chain import build_chain, conjunction_sql
from sqlmystery.config import TIERS
from sqlmystery.db import Db
from sqlmystery.dirty import apply_dirty
from sqlmystery.narrative import write_narrative
from sqlmystery.predicates import Ctx, DirtyFlags
from sqlmystery.world import populate


def build(seed, tier_name="hard", persons=3000):
    db = Db(":memory:")
    db.create_schema()
    rng = random.Random(seed)
    tier = TIERS[tier_name].scaled(persons)
    info = populate(db, rng, tier, crime_date=20180115)
    chain = build_chain(db, rng, tier, info)
    write_narrative(db, rng, chain, info)
    return db, rng, tier, info, chain


@pytest.mark.parametrize("seed", range(3))
def test_dirty_preserves_solvability(seed):
    db, rng, tier, info, chain = build(seed)
    flags = DirtyFlags(event_date_text=True, checkin_time_text=True)
    chain.dirty = flags
    apply_dirty(db, rng, chain, flags)
    ctx = Ctx(info=info, dirty=flags, known=[])
    for hop in chain.hops:
        assert db.ids(conjunction_sql(hop.predicates, ctx)) == [hop.person_id], hop


def test_dirty_actually_dirties():
    db, rng, tier, info, chain = build(5)
    before = db.one("SELECT count(*) FROM person")[0]
    flags = DirtyFlags(event_date_text=True, checkin_time_text=True)
    apply_dirty(db, rng, chain, flags)
    assert db.one("SELECT typeof(date) FROM facebook_event_checkin LIMIT 1")[0] == "text"
    assert db.one("SELECT date FROM facebook_event_checkin LIMIT 1")[0].count("-") == 2
    types = {r[0] for r in db.execute("SELECT DISTINCT typeof(check_in_time) FROM get_fit_now_check_in")}
    assert types == {"integer", "text"}
    assert db.one("SELECT count(*) FROM person WHERE name <> trim(name) OR name <> (upper(substr(name,1,1)) || substr(name,2))")[0] > 0
    assert db.one("SELECT count(*) FROM person")[0] > before
    assert db.one("SELECT count(*) FROM drivers_license WHERE height IS NULL")[0] > 0


def test_chain_persons_are_protected():
    db, rng, tier, info, chain = build(6)
    flags = DirtyFlags(event_date_text=True, checkin_time_text=False)
    apply_dirty(db, rng, chain, flags)
    for hop in chain.hops:
        assert db.one("SELECT count(*) FROM person WHERE name=?", (hop.name,))[0] == 1
        assert db.one("SELECT name FROM person WHERE id=?", (hop.person_id,))[0] == hop.name
