from sqlmystery.db import Db, TABLES
from sqlmystery.config import TIERS


def test_schema_creates_all_tables_and_no_solution_table():
    db = Db(":memory:")
    db.create_schema()
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == set(TABLES)
    assert "solution" not in names
    assert db.ids("SELECT id FROM person") == []
    triggers = db.conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
    assert triggers == 0


def test_tiers_scale_population():
    hard = TIERS["hard"]
    small = hard.scaled(500)
    assert small.persons == 500
    assert small.chain_length == hard.chain_length
    assert hard.persons == 200_000


def test_tier_knobs_and_named_variants():
    from sqlmystery.config import TIERS, Tier
    base = TIERS["hard"]
    assert base.branching == 0 and base.fuzzy is False
    full = TIERS["hard-full"]
    assert full.branching >= 2 and full.fuzzy is True
    assert full.persons == base.persons and full.chain_length == base.chain_length
    assert TIERS["hard-branching"].fuzzy is False and TIERS["hard-branching"].branching >= 2
    assert TIERS["hard-fuzzy"].branching == 0 and TIERS["hard-fuzzy"].fuzzy
    assert base.with_knobs(branching=3, fuzzy=True).name == "hard-b3-fuzzy"
