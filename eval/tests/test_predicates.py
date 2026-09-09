import json

import pytest

from sqlmystery.predicates import REGISTRY, Ctx, DirtyFlags, from_json, sample_kinds


def make_ctx(info, known=None):
    return Ctx(info=info, dirty=DirtyFlags(), known=known or [])


def sample_people(db, rng, k):
    # include people with and without license/ssn so plant must cope with both
    ids = db.ids("SELECT id FROM person ORDER BY id")
    return rng.sample(ids, k)


@pytest.mark.parametrize("kind", sorted(REGISTRY))
def test_plant_makes_sql_match_and_unplant_removes(small_world, kind):
    db, rng, tier, info = small_world
    cls = REGISTRY[kind]
    target, speaker, *decoys = sample_people(db, rng, 7)
    ctx = make_ctx(info, known=[("murderer", speaker)])
    p = cls.sample(db, rng, target, ctx)
    p.plant(db, rng, ctx, target)
    if cls.exclusive:
        decoys = []
    for d in decoys:
        p.plant(db, rng, ctx, d)
    matched = set(db.ids(p.sql(ctx)))
    assert {target, *decoys} <= matched, f"{kind}: planted ids missing from sql result"
    if decoys:
        p.unplant(db, rng, ctx, decoys[0])
        after = set(db.ids(p.sql(ctx)))
        assert decoys[0] not in after
        assert target in after
    p.unplant(db, rng, ctx, target)
    assert target not in set(db.ids(p.sql(ctx)))


@pytest.mark.parametrize("kind", sorted(REGISTRY))
def test_text_and_json_roundtrip(small_world, kind):
    db, rng, tier, info = small_world
    cls = REGISTRY[kind]
    target, speaker = sample_people(db, rng, 2)
    ctx = make_ctx(info, known=[("murderer", speaker)])
    p = cls.sample(db, rng, target, ctx)
    text = p.text(rng, ctx)
    assert isinstance(text, str) and len(text) > 5
    d = json.loads(json.dumps(p.to_json()))
    q = from_json(d)
    assert q.kind == kind
    assert q.sql(ctx) == p.sql(ctx)


def test_sample_kinds_respects_known_and_hard(small_world):
    db, rng, tier, info = small_world
    ctx = make_ctx(info)
    for _ in range(50):
        kinds = sample_kinds(rng, ctx, 3, allow_hard=False)
        assert len(kinds) == 3 and len(set(kinds)) == 3
        assert not any(k.needs_known for k in kinds)
        assert not any(k.hard for k in kinds)
        assert len({k.group for k in kinds}) == 3
    ctx = make_ctx(info, known=[("murderer", 1)])
    saw_known = False
    for _ in range(50):
        kinds = sample_kinds(rng, ctx, 3, allow_hard=True)
        assert any(k.hard for k in kinds)
        saw_known |= any(k.needs_known for k in kinds)
    assert saw_known


def test_dirty_flags_change_sql_for_event_and_checkin_kinds(small_world):
    db, rng, tier, info = small_world
    target, = sample_people(db, rng, 1)
    clean = make_ctx(info)
    dirty = Ctx(info=info, dirty=DirtyFlags(event_date_text=True, checkin_time_text=True), known=[])
    for kind in ["event_count", "gym_checkin_window"]:
        p = REGISTRY[kind].sample(db, rng, target, clean)
        assert p.sql(clean) != p.sql(dirty)


def test_gym_prefix_can_plant_many_decoys(small_world):
    db, rng, tier, info = small_world
    ctx = make_ctx(info)
    target, *decoys = sample_people(db, rng, 200)
    p = REGISTRY["gym_status_prefix"].sample(db, rng, target, ctx)
    p.params["prefix"] = p.params["prefix"][:3].ljust(3, "Z")
    for pid in [target, *decoys]:
        p.plant(db, rng, ctx, pid)
    assert len(set(db.ids(p.sql(ctx)))) >= 200
