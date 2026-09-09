"""Populate a fresh database with a plausible population. No clues are planted here."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import names as N
from .config import Tier
from .db import Db
from .dates import add_days, days_between, rand_date, rand_time, add_minutes

BATCH = 10_000


@dataclass
class WorldInfo:
    crime_date: int
    crime_city: str
    date_start: int
    date_end: int
    streets: list[str]
    cities: list[str]
    event_names: list[str]
    car_models: list[tuple[str, str]]
    hair_colors: list[str] = field(default_factory=lambda: list(N.HAIR_COLORS))
    eye_colors: list[str] = field(default_factory=lambda: list(N.EYE_COLORS))
    genders: list[str] = field(default_factory=lambda: list(N.GENDERS))
    gym_statuses: list[str] = field(default_factory=lambda: list(N.GYM_STATUSES))


def _batched(db: Db, sql: str, rows) -> None:
    buf = []
    for r in rows:
        buf.append(r)
        if len(buf) >= BATCH:
            db.executemany(sql, buf)
            buf = []
    if buf:
        db.executemany(sql, buf)


def _plate(rng: random.Random) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
    return "".join(rng.choice(alphabet) for _ in range(6))


def _gym_id(rng: random.Random) -> str:
    return f"{rng.randint(10, 99)}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.choice('0123456789ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.choice('0123456789ABCDEFGHJKLMNPQRSTUVWXYZ')}"


def _fill(rng: random.Random, template: str, info: WorldInfo) -> str:
    make, model = rng.choice(info.car_models)
    return template.format(
        street=rng.choice(info.streets),
        car=f"{make} {model}",
        event=rng.choice(info.event_names),
        hair=rng.choice(info.hair_colors),
    )


def populate(db: Db, rng: random.Random, tier: Tier, crime_date: int, crime_city: str = "SQL City") -> WorldInfo:
    n = tier.persons
    n_streets = max(20, min(400, n // 100))
    streets = sorted({f"{b} {rng.choice(N.STREET_SUFFIXES)}" for b in rng.sample(N.STREET_BASES, min(len(N.STREET_BASES), n_streets))})
    cities = list(N.CITIES)
    info = WorldInfo(
        crime_date=crime_date,
        crime_city=crime_city,
        date_start=add_days(crime_date, -400),
        date_end=add_days(crime_date, 100),
        streets=streets,
        cities=cities,
        event_names=list(N.EVENT_NAMES),
        car_models=list(N.CAR_MODELS),
    )

    # --- persons and licenses ---
    person_ids = rng.sample(range(10_000, 10_000 + n * 10), n)
    person_ids.sort()
    license_ids = rng.sample(range(100_000, 100_000 + n * 10), n)
    ssns = rng.sample(range(100_000_000, 999_999_999), n)

    people: list[tuple] = []
    licenses: list[tuple] = []
    incomes: list[tuple] = []
    used_names: list[str] = []
    for i, pid in enumerate(person_ids):
        if used_names and rng.random() < 1 / 30:
            name = rng.choice(used_names)
        else:
            name = f"{rng.choice(N.FIRST_NAMES)} {rng.choice(N.LAST_NAMES)}"
            used_names.append(name)
        lic = None
        if rng.random() < 0.95:
            lic = license_ids[i]
            make, model = rng.choice(info.car_models)
            licenses.append((
                lic, rng.randint(16, 85), rng.randint(55, 80), rng.choice(info.eye_colors),
                rng.choice(info.hair_colors), rng.choice(info.genders), _plate(rng), make, model,
            ))
        ssn = None
        if rng.random() < 0.75:
            ssn = str(ssns[i])
            incomes.append((ssn, int(rng.lognormvariate(10.8, 0.7) // 100 * 100)))
        people.append((pid, name, lic, rng.randint(1, 4000), rng.choice(streets), ssn))
    _batched(db, "INSERT INTO person VALUES (?,?,?,?,?,?)", people)
    _batched(db, "INSERT INTO drivers_license VALUES (?,?,?,?,?,?,?,?,?)", licenses)
    _batched(db, "INSERT INTO income VALUES (?,?)", incomes)
    ssn_list = [r[0] for r in incomes]

    # --- gym ---
    members = []
    gym_ids: set[str] = set()
    for pid, name, *_ in people:
        if rng.random() < 0.02:
            gid = _gym_id(rng)
            while gid in gym_ids:
                gid = _gym_id(rng)
            gym_ids.add(gid)
            members.append((gid, pid, name, rand_date(rng, info.date_start, crime_date), rng.choice(info.gym_statuses)))
    _batched(db, "INSERT INTO get_fit_now_member VALUES (?,?,?,?,?)", members)

    def checkins():
        for gid, _pid, _name, start, _status in members:
            for _ in range(rng.randint(0, 30)):
                d = rand_date(rng, start, info.date_end)
                t = rand_time(rng, 600, 2100)
                yield (gid, d, t, add_minutes(t, rng.randint(30, 180)))
    _batched(db, "INSERT INTO get_fit_now_check_in VALUES (?,?,?,?)", checkins())

    # --- events ---
    event_ids = {name: 1000 + i for i, name in enumerate(info.event_names)}

    def events():
        for pid, *_ in people:
            for _ in range(rng.randint(0, 5)):
                ev = rng.choice(info.event_names)
                yield (pid, event_ids[ev], ev, rand_date(rng, info.date_start, info.date_end))
    _batched(db, "INSERT INTO facebook_event_checkin VALUES (?,?,?,?)", events())

    # --- phone calls ---
    def calls():
        for _ in range(n * 2):
            a = rng.choice(person_ids)
            b = rng.choice(person_ids)
            while b == a:
                b = rng.choice(person_ids)
            yield (a, b, rand_date(rng, info.date_start, info.date_end), rand_time(rng, 700, 2300), rng.randint(10, 3600))
    _batched(db, "INSERT INTO phone_call VALUES (?,?,?,?,?)", calls())

    # --- transfers ---
    def transfers():
        for _ in range(n // 2):
            a = rng.choice(ssn_list)
            b = rng.choice(ssn_list)
            while b == a:
                b = rng.choice(ssn_list)
            yield (a, b, rand_date(rng, info.date_start, info.date_end), int(rng.lognormvariate(6.5, 1.2)))
    _batched(db, "INSERT INTO bank_transfer VALUES (?,?,?,?)", transfers())

    # --- filler interviews ---
    def interviews():
        for pid, *_ in people:
            if rng.random() < 0.5:
                k = rng.randint(1, 3)
                yield (pid, " ".join(_fill(rng, t, info) for t in rng.sample(N.FILLER_TRANSCRIPTS, k)))
    _batched(db, "INSERT INTO interview VALUES (?,?)", interviews())

    # --- crime scene reports ---
    def reports():
        ndays = days_between(info.date_start, info.date_end)
        for k in range(ndays + 1):
            d = add_days(info.date_start, k)
            for city in rng.sample(cities, rng.randint(8, 16)):
                for _ in range(rng.randint(1, 3)):
                    ctype = rng.choice(N.CRIME_TYPES + ["murder"])
                    if d == crime_date and city == info.crime_city and ctype == "murder":
                        ctype = "assault"
                    yield (d, ctype, _fill(rng, rng.choice(N.CRIME_DESCRIPTIONS), info), city)
        # guarantee noise at the crime date and city, plus murders elsewhere that day
        for _ in range(3):
            yield (crime_date, rng.choice(N.CRIME_TYPES), _fill(rng, rng.choice(N.CRIME_DESCRIPTIONS), info), info.crime_city)
        for city in rng.sample([c for c in cities if c != info.crime_city], 2):
            yield (crime_date, "murder", _fill(rng, rng.choice(N.CRIME_DESCRIPTIONS), info), city)
    _batched(db, "INSERT INTO crime_scene_report VALUES (?,?,?,?)", reports())

    db.commit()
    return info
