"""Make one complete mystery instance: database, prompt, hidden answer."""
from __future__ import annotations

import json
import random
from pathlib import Path

from .chain import build_chain
from .config import Tier
from .db import Db
from .dates import human, rand_date
from .dirty import apply_dirty
from .narrative import write_narrative
from .verify import verify
from .world import populate
from . import names as N

PROMPT = """A crime has taken place and the detective needs your help. The detective gave you the crime scene report, but you somehow lost it. You vaguely remember that the crime was a murder that occurred sometime on {date} and that it took place in {city}. Start by retrieving the corresponding crime scene report from the police department's database.

Follow the clues in the report and in the interview transcripts to identify the murderer. The murderer did not act alone: keep following the chain of interviews until you find the person ultimately behind the crime.

When you are done, report every person you identified along the way, in order, and name the murderer and the mastermind.
"""


def make(seed: int, tier: Tier, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "mystery.db"
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(f"sqlmystery-{tier.name}-{seed}")
    crime_date = rand_date(rng, 20170301, 20191130)
    crime_city = rng.choice(N.CITIES)

    db = Db(str(db_path))
    db.create_schema()
    info = populate(db, rng, tier, crime_date, crime_city)
    chain = build_chain(db, rng, tier, info)
    write_narrative(db, rng, chain, info)
    if tier.dirty:
        apply_dirty(db, rng, chain, chain.dirty)
    db.create_indexes()
    db.commit()
    db.execute("VACUUM")
    db.close()

    answer = {
        "tier": tier.name,
        "seed": seed,
        "crime_date": crime_date,
        "crime_city": crime_city,
        "persons": tier.persons,
        "murderer": chain.murderer.name,
        "mastermind": chain.mastermind.name,
        **chain.to_json(),
    }
    (out_dir / "answer.json").write_text(json.dumps(answer, indent=2) + "\n")
    (out_dir / "prompt.txt").write_text(PROMPT.format(date=human(crime_date), city=crime_city))
    verify(out_dir)
    return out_dir
