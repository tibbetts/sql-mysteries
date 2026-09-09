import random

import pytest

from sqlmystery.config import TIERS
from sqlmystery.db import Db
from sqlmystery.world import populate


@pytest.fixture
def small_world():
    """A 2000-person world with schema, data, and WorldInfo."""
    db = Db(":memory:")
    db.create_schema()
    rng = random.Random(7)
    tier = TIERS["medium"].scaled(2000)
    info = populate(db, rng, tier, crime_date=20180115)
    return db, rng, tier, info
