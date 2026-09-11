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


def make_knobs(seed, branching, fuzzy, persons=3000, base="hard"):
    db = Db(":memory:")
    db.create_schema()
    rng = random.Random(seed)
    tier = TIERS[base].scaled(persons).with_knobs(branching=branching, fuzzy=fuzzy)
    info = populate(db, rng, tier, crime_date=20180115)
    chain = build_chain(db, rng, tier, info)
    write_narrative(db, rng, chain, info)
    return db, rng, tier, info, chain


@pytest.mark.parametrize("seed", range(4))
def test_branching_hops_resolve_to_target_plus_rivals(seed):
    db, rng, tier, info, chain = make_knobs(seed, branching=2, fuzzy=False)
    ctx = Ctx(info=info, dirty=chain.dirty, known=[])
    for i, hop in enumerate(chain.hops):
        got = db.ids(conjunction_sql(hop.predicates, ctx))
        expected = sorted([hop.person_id] + [r.person_id for r in hop.rivals])
        assert got == expected, (i, hop.role, hop)
        if hop.role in ("witness", "mastermind"):
            assert hop.rivals == []
        else:
            assert len(hop.rivals) == 2
            assert not any(p.exclusive for p in hop.predicates)
        for r in hop.rivals:
            assert db.one("SELECT count(*) FROM person WHERE name=?", (r.name,))[0] == 1
            if r.exclusion.kind == "dead_end":
                assert db.ids(r.exclusion.sql) == []
            elif r.exclusion.kind == "alibi":
                assert db.ids(r.exclusion.sql) == [r.person_id]
            else:
                raise AssertionError(r.exclusion.kind)
    # the true murderer has no alibi
    murderer = chain.murderer
    alibi = next(r.exclusion for r in murderer.rivals if r.exclusion.kind == "alibi")
    assert murderer.person_id not in db.ids(alibi.sql.replace(str(alibi.person_id), str(murderer.person_id)))


def test_branching_narrative_and_report_time():
    db, rng, tier, info, chain = make_knobs(1, branching=2, fuzzy=False)
    report = db.one("SELECT description FROM crime_scene_report WHERE type='murder' AND date=? AND city=?",
                    (info.crime_date, info.crime_city))[0]
    from sqlmystery.dates import human_time
    assert human_time(chain.crime_time) in report
    for hop in chain.hops:
        for r in hop.rivals:
            t = db.one("SELECT transcript FROM interview WHERE person_id=?", (r.person_id,))[0]
            assert db.one("SELECT count(*) FROM interview WHERE person_id=?", (r.person_id,))[0] == 1
            if r.exclusion.kind == "dead_end":
                for c in r.exclusion.clues:
                    assert c.rendered in t
            else:
                assert "phone" in t.lower()


def test_fuzzy_chain_builds_and_verifies():
    db, rng, tier, info, chain = make_knobs(2, branching=0, fuzzy=True)
    ctx = Ctx(info=info, dirty=chain.dirty, known=[])
    assert any(p.params.get("fuzzy") for h in chain.hops for p in h.predicates)
    for hop in chain.hops:
        assert db.ids(conjunction_sql(hop.predicates, ctx)) == [hop.person_id]


def test_branching_json_roundtrip():
    db, rng, tier, info, chain = make_knobs(3, branching=2, fuzzy=True)
    d = chain.to_json()
    back = Chain.from_json(d)
    assert back.to_json() == d
    assert sum(len(h.rivals) for h in back.hops) == sum(len(h.rivals) for h in chain.hops) > 0
