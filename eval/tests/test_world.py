from sqlmystery.world import WorldInfo


def count(db, table, where="1=1"):
    return db.one(f"SELECT count(*) FROM {table} WHERE {where}")[0]


def test_populate_row_counts(small_world):
    db, rng, tier, info = small_world
    assert isinstance(info, WorldInfo)
    assert count(db, "person") == 2000
    assert 1800 <= count(db, "drivers_license") <= 2000
    assert 1300 <= count(db, "income") <= 1600
    assert 20 <= count(db, "get_fit_now_member") <= 80
    assert count(db, "get_fit_now_check_in") > 0
    assert count(db, "facebook_event_checkin") > 2000
    assert count(db, "phone_call") > 2000
    assert count(db, "bank_transfer") > 500
    assert 800 <= count(db, "interview") <= 1200


def test_referential_integrity(small_world):
    db, *_ = small_world
    assert count(db, "person p", "license_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM drivers_license d WHERE d.id=p.license_id)") == 0
    assert count(db, "get_fit_now_check_in c", "NOT EXISTS (SELECT 1 FROM get_fit_now_member m WHERE m.id=c.membership_id)") == 0
    assert count(db, "facebook_event_checkin e", "NOT EXISTS (SELECT 1 FROM person p WHERE p.id=e.person_id)") == 0
    assert count(db, "phone_call c", "caller_id = callee_id") == 0
    assert count(db, "bank_transfer t", "NOT EXISTS (SELECT 1 FROM income i WHERE i.ssn=t.from_ssn)") == 0


def test_names_not_all_unique_and_streets_shared(small_world):
    db, *_ = small_world
    assert db.one("SELECT count(*) - count(DISTINCT name) FROM person")[0] > 0
    assert db.one("SELECT count(DISTINCT address_street_name) FROM person")[0] < 500


def test_crime_reports_on_crime_date_without_murder(small_world):
    db, rng, tier, info = small_world
    assert info.crime_date == 20180115
    n = count(db, "crime_scene_report", f"date=20180115 AND city='{info.crime_city}'")
    assert n >= 3
    assert count(db, "crime_scene_report", f"date=20180115 AND city='{info.crime_city}' AND type='murder'") == 0
    assert count(db, "crime_scene_report", "date=20180115") > n  # other cities same day


def test_populate_is_deterministic():
    import random
    from sqlmystery.config import TIERS
    from sqlmystery.db import Db
    from sqlmystery.world import populate

    def snapshot():
        db = Db(":memory:")
        db.create_schema()
        populate(db, random.Random(3), TIERS["easy"].scaled(300), crime_date=20180115)
        return list(db.conn.execute("SELECT * FROM person ORDER BY id")) + list(db.conn.execute("SELECT * FROM phone_call ORDER BY rowid"))

    assert snapshot() == snapshot()
