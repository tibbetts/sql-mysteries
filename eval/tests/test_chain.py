import random

import pytest

from sqlmystery.chain import Chain, build_chain, conjunction_sql
from sqlmystery.config import TIERS
from sqlmystery.db import Db
from sqlmystery.narrative import write_narrative
from sqlmystery.predicates import Ctx
from sqlmystery.world import populate


def make(seed, tier_name="medium", persons=2000):
    db = Db(":memory:")
    db.create_schema()
    rng = random.Random(seed)
    tier = TIERS[tier_name].scaled(persons)
    info = populate(db, rng, tier, crime_date=20180115)
    chain = build_chain(db, rng, tier, info)
    write_narrative(db, rng, chain, info)
    return db, rng, tier, info, chain


@pytest.mark.parametrize("seed", range(5))
def test_every_hop_identifies_exactly_its_target(seed):
    db, rng, tier, info, chain = make(seed)
    assert len(chain.hops) == tier.chain_length
    assert [h.role for h in chain.hops][-1] == "mastermind"
    assert [h.role for h in chain.hops][:2] == ["witness", "witness"]
    for hop in chain.hops:
        ctx = Ctx(info=info, dirty=chain.dirty, known=[])
        assert db.ids(conjunction_sql(hop.predicates, ctx)) == [hop.person_id], hop


@pytest.mark.parametrize("tier_name", ["easy", "hard", "extreme"])
def test_tiers_build(tier_name):
    db, rng, tier, info, chain = make(11, tier_name, persons=3000)
    assert len(chain.hops) == tier.chain_length
    for hop in chain.hops:
        ctx = Ctx(info=info, dirty=chain.dirty, known=[])
        assert db.ids(conjunction_sql(hop.predicates, ctx)) == [hop.person_id], hop
    if tier.hard_kinds:
        assert any(p.hard for h in chain.hops[2:] for p in h.predicates)


def test_decoys_exist_for_single_predicates():
    db, rng, tier, info, chain = make(2)
    ctx = Ctx(info=info, dirty=chain.dirty, known=[])
    for hop in chain.hops:
        for p in hop.predicates:
            if not p.exclusive:
                assert len(db.ids(p.sql(ctx))) > 1, (hop, p)


def test_chain_names_unique_and_narrative_written():
    db, rng, tier, info, chain = make(3)
    for hop in chain.hops:
        assert db.one("SELECT count(*) FROM person WHERE name=?", (hop.name,))[0] == 1
    murders = db.execute("SELECT description FROM crime_scene_report WHERE date=? AND city=? AND type='murder'",
                         (info.crime_date, info.crime_city)).fetchall()
    assert len(murders) == 1
    report = murders[0][0]
    ctx = Ctx(info=info, dirty=chain.dirty, known=[])
    for hop in chain.hops:
        if hop.role == "witness":
            for clue in hop.clues:
                assert clue.rendered in report
        else:
            for clue in hop.clues:
                transcript = db.one("SELECT transcript FROM interview WHERE person_id=?", (clue.speaker,))[0]
                assert clue.rendered in transcript
    # mastermind has no clue-bearing transcript to a further person, but chain persons have one interview at most
    for hop in chain.hops:
        assert db.one("SELECT count(*) FROM interview WHERE person_id=?", (hop.person_id,))[0] <= 1


def test_chain_json_roundtrip():
    db, rng, tier, info, chain = make(4)
    d = chain.to_json()
    back = Chain.from_json(d)
    assert back.to_json() == d
    assert [h.person_id for h in back.hops] == [h.person_id for h in chain.hops]
