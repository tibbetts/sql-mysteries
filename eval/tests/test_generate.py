import json
import sqlite3

import pytest

from sqlmystery.config import TIERS
from sqlmystery.generate import make
from sqlmystery.verify import VerificationError, verify


def test_make_writes_files_and_verifies(tmp_path):
    out = make(seed=1, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path / "a")
    assert (out / "mystery.db").exists()
    assert (out / "prompt.txt").exists()
    answer = json.loads((out / "answer.json").read_text())
    assert answer["tier"] == "easy" and answer["seed"] == 1
    assert answer["hops"][-1]["role"] == "mastermind"
    assert answer["mastermind"] == answer["hops"][-1]["name"]
    assert answer["murderer"] == next(h["name"] for h in answer["hops"] if h["role"] == "murderer")
    prompt = (out / "prompt.txt").read_text()
    from sqlmystery.dates import human
    assert human(answer["crime_date"]) in prompt
    assert answer["crime_city"] in prompt
    assert answer["mastermind"] not in prompt
    verify(out)


def test_db_has_no_solution_or_trigger(tmp_path):
    out = make(seed=2, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path / "b")
    conn = sqlite3.connect(out / "mystery.db")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "solution" not in names
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0
    # answer names do not appear anywhere in the text of the schema
    schema = " ".join(r[0] or "" for r in conn.execute("SELECT sql FROM sqlite_master"))
    answer = json.loads((out / "answer.json").read_text())
    assert answer["mastermind"] not in schema


def test_make_is_deterministic(tmp_path):
    a = make(seed=3, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path / "x")
    b = make(seed=3, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path / "y")
    assert (a / "answer.json").read_text() == (b / "answer.json").read_text()
    assert (a / "prompt.txt").read_text() == (b / "prompt.txt").read_text()


def test_verify_fails_on_corrupted_db(tmp_path):
    out = make(seed=4, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path / "c")
    answer = json.loads((out / "answer.json").read_text())
    conn = sqlite3.connect(out / "mystery.db")
    conn.execute("UPDATE person SET address_street_name='Nowhere' WHERE id=?", (answer["hops"][-1]["person_id"],))
    conn.execute("UPDATE drivers_license SET hair_color='purple', height=1, eye_color='x', age=1 WHERE id=(SELECT license_id FROM person WHERE id=?)",
                 (answer["hops"][-1]["person_id"],))
    conn.commit()
    conn.close()
    with pytest.raises(VerificationError):
        verify(out)


def test_hard_tier_makes_with_dirty_flags(tmp_path):
    out = make(seed=5, tier=TIERS["hard"].scaled(3000), out_dir=tmp_path / "d")
    answer = json.loads((out / "answer.json").read_text())
    assert answer["dirty"]["event_date_text"] is True
    verify(out)


def test_branching_instance_verifies_and_corrupted_alibi_fails(tmp_path):
    out = make(seed=6, tier=TIERS["hard-full"].scaled(3000), out_dir=tmp_path / "e")
    answer = json.loads((out / "answer.json").read_text())
    assert answer["tier"] == "hard-full" and answer["crime_time"]
    rivals = [r for h in answer["hops"] for r in h["rivals"]]
    assert rivals
    verify(out)
    alibi = next(r for r in rivals if r["exclusion"]["kind"] == "alibi")
    conn = sqlite3.connect(out / "mystery.db")
    conn.execute("DELETE FROM phone_call WHERE caller_id=? AND date=?", (alibi["person_id"], answer["crime_date"]))
    conn.commit()
    conn.close()
    with pytest.raises(VerificationError):
        verify(out)


def test_cli_knob_overrides(tmp_path):
    from sqlmystery.__main__ import main
    main(["make", "--seed", "7", "--tier", "medium", "--persons", "3000", "--branching", "1", "--fuzzy",
          "--out", str(tmp_path / "f")])
    answer = json.loads((tmp_path / "f" / "answer.json").read_text())
    assert answer["tier"] == "medium-b1-fuzzy"
    assert any(h["rivals"] for h in answer["hops"])
