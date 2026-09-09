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
