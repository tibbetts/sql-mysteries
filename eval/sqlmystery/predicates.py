"""Predicate library.

A predicate is a fact about a person that can be planted, unplanted, expressed
in SQL (returning matching person ids) and expressed in English. Chains are
conjunctions of predicates; the generator plants the target and decoys, then
verification re-runs the SQL.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import ClassVar

from .db import Db
from .dates import add_days, human, human_time, month_bounds, month_name, rand_date, rand_time, add_minutes
from .world import WorldInfo


@dataclass(frozen=True)
class DirtyFlags:
    event_date_text: bool = False      # facebook_event_checkin.date is 'YYYY-MM-DD' text
    checkin_time_text: bool = False    # get_fit_now_check_in times mix HHMM ints and 'HH:MM' text

    def to_json(self) -> dict:
        return {"event_date_text": self.event_date_text, "checkin_time_text": self.checkin_time_text}

    @classmethod
    def from_json(cls, d: dict) -> "DirtyFlags":
        return cls(**d)


@dataclass
class Ctx:
    info: WorldInfo | None      # None is allowed for sql()-only use, e.g. verification
    dirty: DirtyFlags
    known: list[tuple[str, int]] = field(default_factory=list)  # (role, person_id); last is the speaker
    reserved: set[str] = field(default_factory=set)
    fuzzy: bool = False

    @property
    def speaker(self) -> int:
        return self.known[-1][1]


# ---------- helpers ----------

def _license(db: Db, pid: int):
    return db.execute(
        "SELECT d.id, d.age, d.height, d.eye_color, d.hair_color, d.gender, d.plate_number, d.car_make, d.car_model "
        "FROM person p JOIN drivers_license d ON p.license_id=d.id WHERE p.id=?", (pid,)).fetchone()


def _ensure_license(db: Db, rng: random.Random, info: WorldInfo, pid: int):
    row = _license(db, pid)
    if row:
        return row
    lic = db.one("SELECT COALESCE(MAX(id),100000)+1 FROM drivers_license")[0]
    make, model = rng.choice(info.car_models)
    db.execute("INSERT INTO drivers_license VALUES (?,?,?,?,?,?,?,?,?)", (
        lic, rng.randint(16, 85), rng.randint(55, 80), rng.choice(info.eye_colors),
        rng.choice(info.hair_colors), rng.choice(info.genders), _random_plate(rng), make, model))
    db.execute("UPDATE person SET license_id=? WHERE id=?", (lic, pid))
    return _license(db, pid)


def _ensure_ssn(db: Db, rng: random.Random, pid: int) -> str:
    ssn = db.one("SELECT ssn FROM person WHERE id=?", (pid,))[0]
    if ssn is None:
        while True:
            ssn = str(rng.randint(100_000_000, 999_999_999))
            if not db.execute("SELECT 1 FROM income WHERE ssn=?", (ssn,)).fetchone():
                break
        db.execute("UPDATE person SET ssn=? WHERE id=?", (ssn, pid))
    if not db.execute("SELECT 1 FROM income WHERE ssn=?", (ssn,)).fetchone():
        db.execute("INSERT INTO income VALUES (?,?)", (ssn, int(rng.lognormvariate(10.8, 0.7) // 100 * 100)))
    return ssn


def _ensure_member(db: Db, rng: random.Random, info: WorldInfo, pid: int) -> tuple[str, str]:
    row = db.execute("SELECT id, membership_status FROM get_fit_now_member WHERE person_id=?", (pid,)).fetchone()
    if row:
        return row
    name = db.one("SELECT name FROM person WHERE id=?", (pid,))[0]
    gid = _new_gym_id(db, rng, "")
    status = rng.choice(info.gym_statuses)
    db.execute("INSERT INTO get_fit_now_member VALUES (?,?,?,?,?)",
               (gid, pid, name, rand_date(rng, info.date_start, info.crime_date), status))
    return gid, status


_ALNUM = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _new_gym_id(db: Db, rng: random.Random, prefix: str) -> str:
    """Membership ids are 5 or 6 characters. With a prefix, fill to 6 so the id space stays large."""
    while True:
        if prefix:
            gid = prefix + "".join(rng.choice(_ALNUM) for _ in range(6 - len(prefix)))
        else:
            gid = f"{rng.randint(10, 99)}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.choice(_ALNUM)}{rng.choice(_ALNUM)}"
        if not db.execute("SELECT 1 FROM get_fit_now_member WHERE id=?", (gid,)).fetchone():
            return gid


def _random_plate(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(6))


def _feet(inches: int) -> str:
    return f"{inches // 12}'{inches % 12}\""


def _event_date_expr(ctx: Ctx) -> str:
    return "CAST(replace(date,'-','') AS INTEGER)" if ctx.dirty.event_date_text else "date"


def _checkin_time_expr(ctx: Ctx, col: str) -> str:
    return f"CAST(replace({col},':','') AS INTEGER)" if ctx.dirty.checkin_time_text else col


CAR_COUNTRY = {
    "BMW": "German", "Mercedes-Benz": "German", "Audi": "German", "Volkswagen": "German", "Porsche": "German",
    "Toyota": "Japanese", "Honda": "Japanese", "Nissan": "Japanese", "Subaru": "Japanese", "Mazda": "Japanese",
    "Lexus": "Japanese", "Acura": "Japanese", "Infiniti": "Japanese",
    "Ford": "American", "Chevrolet": "American", "Jeep": "American", "Dodge": "American", "GMC": "American",
    "Buick": "American", "Cadillac": "American", "Tesla": "American",
    "Hyundai": "Korean", "Kia": "Korean", "Volvo": "Swedish", "Mini": "British",
}
PERIODS = {"morning": (600, 1159), "afternoon": (1200, 1759), "evening": (1800, 2159)}
EVENT_COUNT_WORDS = {"a few": (2, 4, 3), "several": (3, 5, 4), "many": (5, 8, 6)}
CALL_OFFSET_WORDS = {"the day before, or maybe the day of the murder": (0, 1), "a couple of days before the murder": (1, 3), "about a week before the murder": (5, 9)}


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


# ---------- base ----------

class Predicate:
    kind: ClassVar[str] = ""
    hard: ClassVar[bool] = False
    needs_known: ClassVar[bool] = False
    exclusive: ClassVar[bool] = False   # at most one person can satisfy it; no decoys
    group: ClassVar[str] = ""           # at most one predicate per group per hop

    def __init__(self, params: dict):
        self.params = params

    @classmethod
    def sample(cls, db: Db, rng: random.Random, target_id: int, ctx: Ctx) -> "Predicate":
        raise NotImplementedError

    def plant(self, db: Db, rng: random.Random, ctx: Ctx, pid: int) -> None:
        raise NotImplementedError

    def unplant(self, db: Db, rng: random.Random, ctx: Ctx, pid: int) -> None:
        raise NotImplementedError

    def sql(self, ctx: Ctx) -> str:
        raise NotImplementedError

    def text(self, rng: random.Random, ctx: Ctx) -> str:
        raise NotImplementedError

    def to_json(self) -> dict:
        return {"kind": self.kind, "params": dict(self.params)}

    def __repr__(self) -> str:
        return f"{self.kind}({self.params})"


REGISTRY: dict[str, type[Predicate]] = {}


def register(cls):
    REGISTRY[cls.kind] = cls
    return cls


def from_json(d: dict) -> Predicate:
    return REGISTRY[d["kind"]](d["params"])


# ---------- license attribute predicates ----------

class _LicenseAttr(Predicate):
    column: ClassVar[str] = ""
    choices_attr: ClassVar[str] = ""
    group = "license_attr"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        row = _ensure_license(db, rng, ctx.info, target_id)
        cols = ["id", "age", "height", "eye_color", "hair_color", "gender", "plate_number", "car_make", "car_model"]
        val = row[cols.index(cls.column)]
        return cls({"value": val})

    def plant(self, db, rng, ctx, pid):
        _ensure_license(db, rng, ctx.info, pid)
        db.execute(f"UPDATE drivers_license SET {self.column}=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (self.params["value"], pid))

    def unplant(self, db, rng, ctx, pid):
        choices = [c for c in getattr(ctx.info, self.choices_attr) if c != self.params["value"]]
        db.execute(f"UPDATE drivers_license SET {self.column}=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (rng.choice(choices), pid))

    def sql(self, ctx):
        return (f"SELECT p.id FROM person p JOIN drivers_license d ON p.license_id=d.id "
                f"WHERE d.{self.column}={_q(self.params['value'])}")




@register
class HairColor(_LicenseAttr):
    kind = "hair_color"
    column = "hair_color"
    choices_attr = "hair_colors"
    group = "hair"

    def text(self, rng, ctx):
        v = self.params["value"]
        return rng.choice([f"had {v} hair", f"has {v} hair"])


@register
class EyeColor(_LicenseAttr):
    kind = "eye_color"
    column = "eye_color"
    choices_attr = "eye_colors"
    group = "eyes"

    def text(self, rng, ctx):
        v = self.params["value"]
        return rng.choice([f"had {v} eyes", f"has {v} eyes"])


@register
class Gender(_LicenseAttr):
    kind = "gender"
    column = "gender"
    choices_attr = "genders"
    group = "gender"

    def text(self, rng, ctx):
        v = self.params["value"]
        noun = "man" if v == "male" else "woman"
        return rng.choice([f"was a {noun}", f"is a {noun}"])


@register
class Car(Predicate):
    kind = "car"
    group = "car"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        row = _ensure_license(db, rng, ctx.info, target_id)
        if ctx.fuzzy:
            mode = rng.choice(["make", "country"])
            return cls({"fuzzy": True, "mode": mode, "make": row[7], "model": row[8], "country": CAR_COUNTRY[row[7]]})
        return cls({"make": row[7], "model": row[8]})

    def _matching(self, ctx) -> list[tuple[str, str]]:
        mode = self.params.get("mode")
        if mode == "country":
            return [m for m in ctx.info.car_models if CAR_COUNTRY[m[0]] == self.params["country"]]
        if mode == "make":
            return [m for m in ctx.info.car_models if m[0] == self.params["make"]]
        return [(self.params["make"], self.params["model"])]

    def plant(self, db, rng, ctx, pid):
        _ensure_license(db, rng, ctx.info, pid)
        make, model = rng.choice(self._matching(ctx))
        db.execute("UPDATE drivers_license SET car_make=?, car_model=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (make, model, pid))

    def unplant(self, db, rng, ctx, pid):
        matching = set(self._matching(ctx))
        make, model = rng.choice([m for m in ctx.info.car_models if m not in matching])
        db.execute("UPDATE drivers_license SET car_make=?, car_model=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (make, model, pid))

    def sql(self, ctx):
        mode = self.params.get("mode")
        if mode == "country":
            makes = sorted({m for m, c in CAR_COUNTRY.items() if c == self.params["country"]})
            where = "d.car_make IN (" + ",".join(_q(m) for m in makes) + ")"
        elif mode == "make":
            where = f"d.car_make={_q(self.params['make'])}"
        else:
            where = f"d.car_make={_q(self.params['make'])} AND d.car_model={_q(self.params['model'])}"
        return f"SELECT p.id FROM person p JOIN drivers_license d ON p.license_id=d.id WHERE {where}"

    def text(self, rng, ctx):
        mode = self.params.get("mode")
        if mode == "country":
            return rng.choice([f"drives something {self.params['country']}", f"got into a {self.params['country']} car, I could not tell which"])
        if mode == "make":
            return rng.choice([f"drives a {self.params['make']}, I did not catch the model", f"got into some kind of {self.params['make']}"])
        c = f"{self.params['make']} {self.params['model']}"
        return rng.choice([f"drives a {c}", f"got into a {c}"])


@register
class HeightRange(Predicate):
    kind = "height_range"
    group = "height"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        row = _ensure_license(db, rng, ctx.info, target_id)
        h = row[2]
        if ctx.fuzzy:
            return cls({"fuzzy": True, "center": h, "lo": h - 2, "hi": h + 2})
        lo = h - rng.randint(0, 2)
        hi = lo + rng.randint(2, 3)
        return cls({"lo": lo, "hi": hi})

    def plant(self, db, rng, ctx, pid):
        _ensure_license(db, rng, ctx.info, pid)
        lo, hi = self.params["lo"], self.params["hi"]
        if self.params.get("fuzzy"):
            lo, hi = self.params["center"] - 1, self.params["center"] + 1
        db.execute("UPDATE drivers_license SET height=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (rng.randint(lo, hi), pid))

    def unplant(self, db, rng, ctx, pid):
        h = self.params["lo"] - rng.randint(2, 6) if rng.random() < 0.5 else self.params["hi"] + rng.randint(2, 6)
        db.execute("UPDATE drivers_license SET height=? WHERE id=(SELECT license_id FROM person WHERE id=?)", (h, pid))

    def sql(self, ctx):
        return (f"SELECT p.id FROM person p JOIN drivers_license d ON p.license_id=d.id "
                f"WHERE d.height BETWEEN {self.params['lo']} AND {self.params['hi']}")

    def text(self, rng, ctx):
        lo, hi = self.params["lo"], self.params["hi"]
        if self.params.get("fuzzy"):
            c = _feet(self.params["center"])
            return rng.choice([f"was about {c} tall, give or take a couple of inches", f"looked roughly {c}, hard to say exactly"])
        return rng.choice([
            f"was between {_feet(lo)} and {_feet(hi)} tall",
            f"stood somewhere from {lo} to {hi} inches",
            f"height around {_feet(lo)} to {_feet(hi)}, I would say",
        ])


@register
class AgeRange(Predicate):
    kind = "age_range"
    group = "age"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        row = _ensure_license(db, rng, ctx.info, target_id)
        a = row[1]
        if ctx.fuzzy:
            return cls({"fuzzy": True, "center": a, "lo": a - 3, "hi": a + 3})
        lo = a - rng.randint(0, 4)
        hi = lo + rng.randint(4, 6)
        return cls({"lo": lo, "hi": hi})

    def plant(self, db, rng, ctx, pid):
        _ensure_license(db, rng, ctx.info, pid)
        lo, hi = self.params["lo"], self.params["hi"]
        if self.params.get("fuzzy"):
            lo, hi = self.params["center"] - 1, self.params["center"] + 1
        db.execute("UPDATE drivers_license SET age=? WHERE id=(SELECT license_id FROM person WHERE id=?)",
                   (rng.randint(lo, hi), pid))

    def unplant(self, db, rng, ctx, pid):
        a = self.params["lo"] - rng.randint(2, 10) if self.params["lo"] > 20 else self.params["hi"] + rng.randint(2, 10)
        db.execute("UPDATE drivers_license SET age=? WHERE id=(SELECT license_id FROM person WHERE id=?)", (a, pid))

    def sql(self, ctx):
        return (f"SELECT p.id FROM person p JOIN drivers_license d ON p.license_id=d.id "
                f"WHERE d.age BETWEEN {self.params['lo']} AND {self.params['hi']}")

    def text(self, rng, ctx):
        lo, hi = self.params["lo"], self.params["hi"]
        if self.params.get("fuzzy"):
            c = self.params["center"]
            return rng.choice([f"looked around {c}, could be a few years either way", f"seemed to be roughly {c} years old"])
        return rng.choice([f"looked between {lo} and {hi} years old", f"aged somewhere from {lo} to {hi}"])


@register
class PlateFragment(Predicate):
    kind = "plate_fragment"
    group = "plate"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        row = _ensure_license(db, rng, ctx.info, target_id)
        plate = row[6]
        n = rng.randint(3, 4)
        start = rng.randint(0, len(plate) - n)
        return cls({"fragment": plate[start:start + n]})

    def plant(self, db, rng, ctx, pid):
        _ensure_license(db, rng, ctx.info, pid)
        frag = self.params["fragment"]
        plate = db.one("SELECT d.plate_number FROM person p JOIN drivers_license d ON p.license_id=d.id WHERE p.id=?", (pid,))[0]
        if frag not in plate:
            rest = 6 - len(frag)
            pos = rng.randint(0, rest)
            filler = _random_plate(rng)
            plate = filler[:pos] + frag + filler[pos:rest]
            db.execute("UPDATE drivers_license SET plate_number=? WHERE id=(SELECT license_id FROM person WHERE id=?)", (plate, pid))

    def unplant(self, db, rng, ctx, pid):
        frag = self.params["fragment"]
        plate = _random_plate(rng)
        while frag in plate:
            plate = _random_plate(rng)
        db.execute("UPDATE drivers_license SET plate_number=? WHERE id=(SELECT license_id FROM person WHERE id=?)", (plate, pid))

    def sql(self, ctx):
        return (f"SELECT p.id FROM person p JOIN drivers_license d ON p.license_id=d.id "
                f"WHERE d.plate_number LIKE '%{self.params['fragment']}%'")

    def text(self, rng, ctx):
        f = self.params["fragment"]
        return rng.choice([f'drove a car whose license plate had "{f}" in it', f'has a license plate containing "{f}"'])


# ---------- address predicates ----------

@register
class Street(Predicate):
    kind = "street"
    group = "street"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        return cls({"street": db.one("SELECT address_street_name FROM person WHERE id=?", (target_id,))[0]})

    def plant(self, db, rng, ctx, pid):
        db.execute("UPDATE person SET address_street_name=? WHERE id=?", (self.params["street"], pid))

    def unplant(self, db, rng, ctx, pid):
        others = [s for s in ctx.info.streets if s != self.params["street"]]
        db.execute("UPDATE person SET address_street_name=? WHERE id=?", (rng.choice(others), pid))

    def sql(self, ctx):
        return f"SELECT id FROM person WHERE address_street_name={_q(self.params['street'])}"

    def text(self, rng, ctx):
        s = self.params["street"]
        return rng.choice([f"lives on {s}", f"has a place somewhere on {s}"])


@register
class LastHouseOnStreet(Predicate):
    kind = "last_house_on_street"
    group = "street"
    exclusive = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        free = [s for s in ctx.info.streets if f"street:{s}" not in ctx.reserved]
        street = rng.choice(free)
        ctx.reserved.add(f"street:{street}")
        return cls({"street": street})

    def plant(self, db, rng, ctx, pid):
        mx = db.one("SELECT COALESCE(MAX(address_number),0) FROM person WHERE address_street_name=?", (self.params["street"],))[0]
        db.execute("UPDATE person SET address_street_name=?, address_number=? WHERE id=?", (self.params["street"], mx + 1, pid))

    def unplant(self, db, rng, ctx, pid):
        db.execute("UPDATE person SET address_number=1 WHERE id=?", (pid,))

    def sql(self, ctx):
        s = _q(self.params["street"])
        return (f"SELECT id FROM person WHERE address_street_name={s} AND address_number="
                f"(SELECT MAX(address_number) FROM person WHERE address_street_name={s})")

    def text(self, rng, ctx):
        s = self.params["street"]
        return rng.choice([f"lives in the last house on {s}", f"has the highest house number on {s}"])


@register
class IncomeRankOnStreet(Predicate):
    kind = "income_rank_on_street"
    group = "street"
    exclusive = True
    hard = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        street = db.one("SELECT address_street_name FROM person WHERE id=?", (target_id,))[0]
        if f"street:{street}" in ctx.reserved:
            street = rng.choice([s for s in ctx.info.streets if f"street:{s}" not in ctx.reserved])
        ctx.reserved.add(f"street:{street}")
        return cls({"street": street})

    def plant(self, db, rng, ctx, pid):
        ssn = _ensure_ssn(db, rng, pid)
        db.execute("UPDATE person SET address_street_name=? WHERE id=?", (self.params["street"], pid))
        mx = db.one("SELECT COALESCE(MAX(i.annual_income),0) FROM person p JOIN income i ON p.ssn=i.ssn "
                    "WHERE p.address_street_name=?", (self.params["street"],))[0]
        db.execute("UPDATE income SET annual_income=? WHERE ssn=?", (mx + rng.randint(1000, 50000), ssn))

    def unplant(self, db, rng, ctx, pid):
        db.execute("UPDATE income SET annual_income=? WHERE ssn=(SELECT ssn FROM person WHERE id=?)", (rng.randint(10000, 30000), pid))

    def sql(self, ctx):
        s = _q(self.params["street"])
        return (f"SELECT p.id FROM person p JOIN income i ON p.ssn=i.ssn WHERE p.address_street_name={s} "
                f"AND i.annual_income=(SELECT MAX(i2.annual_income) FROM person p2 JOIN income i2 ON p2.ssn=i2.ssn "
                f"WHERE p2.address_street_name={s})")

    def text(self, rng, ctx):
        s = self.params["street"]
        return rng.choice([f"makes more money than anyone else living on {s}", f"is the highest earner on {s}"])


# ---------- gym predicates ----------

@register
class GymStatusPrefix(Predicate):
    kind = "gym_status_prefix"
    group = "gym"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        gid, status = _ensure_member(db, rng, ctx.info, target_id)
        return cls({"status": status, "prefix": gid[:rng.randint(2, 3)]})

    def plant(self, db, rng, ctx, pid):
        gid, _status = _ensure_member(db, rng, ctx.info, pid)
        prefix = self.params["prefix"]
        if not gid.startswith(prefix):
            new = _new_gym_id(db, rng, prefix)
            db.execute("UPDATE get_fit_now_check_in SET membership_id=? WHERE membership_id=?", (new, gid))
            db.execute("UPDATE get_fit_now_member SET id=? WHERE id=?", (new, gid))
        db.execute("UPDATE get_fit_now_member SET membership_status=? WHERE person_id=?", (self.params["status"], pid))

    def unplant(self, db, rng, ctx, pid):
        others = [s for s in ctx.info.gym_statuses if s != self.params["status"]]
        db.execute("UPDATE get_fit_now_member SET membership_status=? WHERE person_id=?", (rng.choice(others), pid))

    def sql(self, ctx):
        return (f"SELECT person_id FROM get_fit_now_member WHERE membership_status={_q(self.params['status'])} "
                f"AND id LIKE '{self.params['prefix']}%'")

    def text(self, rng, ctx):
        s, p = self.params["status"], self.params["prefix"]
        return rng.choice([
            f'carried a Get Fit Now gym bag; the membership number on it started with "{p}" and only {s} members get those bags',
            f'is a {s} member at Get Fit Now with a membership id beginning "{p}"',
        ])


@register
class GymCheckinWindow(Predicate):
    kind = "gym_checkin_window"
    group = "gym_visit"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        date = add_days(ctx.info.crime_date, -rng.randint(1, 20))
        if ctx.fuzzy:
            period = rng.choice(list(PERIODS))
            lo, hi = PERIODS[period]
            return cls({"fuzzy": True, "period": period, "date": date, "t_lo": lo, "t_hi": hi})
        lo = rand_time(rng, 700, 1900)
        return cls({"date": date, "t_lo": lo, "t_hi": add_minutes(lo, 120)})

    def plant(self, db, rng, ctx, pid):
        gid, _ = _ensure_member(db, rng, ctx.info, pid)
        if self.params.get("fuzzy"):
            t = add_minutes(self.params["t_lo"], rng.randint(60, 240))   # well inside the period
        else:
            t = add_minutes(self.params["t_lo"], rng.randint(0, 100))
        db.execute("INSERT INTO get_fit_now_check_in VALUES (?,?,?,?)",
                   (gid, self.params["date"], t, add_minutes(t, rng.randint(30, 120))))

    def unplant(self, db, rng, ctx, pid):
        db.execute("DELETE FROM get_fit_now_check_in WHERE membership_id IN (SELECT id FROM get_fit_now_member WHERE person_id=?) "
                   "AND check_in_date=?", (pid, self.params["date"]))

    def sql(self, ctx):
        t = _checkin_time_expr(ctx, "c.check_in_time")
        return (f"SELECT DISTINCT m.person_id FROM get_fit_now_member m JOIN get_fit_now_check_in c ON c.membership_id=m.id "
                f"WHERE c.check_in_date={self.params['date']} AND {t} BETWEEN {self.params['t_lo']} AND {self.params['t_hi']}")

    def text(self, rng, ctx):
        d, lo, hi = human(self.params["date"]), human_time(self.params["t_lo"]), human_time(self.params["t_hi"])
        if self.params.get("fuzzy"):
            return rng.choice([f"was at the Get Fit Now gym on {d}, sometime in the {self.params['period']}",
                               f"checked in at Get Fit Now in the {self.params['period']} on {d}"])
        return rng.choice([
            f"checked in at the Get Fit Now gym on {d} sometime between {lo} and {hi}",
            f"was working out at Get Fit Now on {d}, arriving sometime between {lo} and {hi}",
        ])


# ---------- event predicates ----------

@register
class EventCount(Predicate):
    kind = "event_count"
    group = "event"

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        month = add_days(ctx.info.crime_date, -rng.randint(15, 120))
        lo, hi = month_bounds(month)
        if ctx.fuzzy:
            word = rng.choice(list(EVENT_COUNT_WORDS))
            c_lo, c_hi, c = EVENT_COUNT_WORDS[word]
            return cls({"fuzzy": True, "word": word, "event": rng.choice(ctx.info.event_names), "lo": lo, "hi": hi,
                        "count": c, "count_lo": c_lo, "count_hi": c_hi})
        return cls({"event": rng.choice(ctx.info.event_names), "lo": lo, "hi": hi, "count": rng.randint(2, 4)})

    def _delete(self, db, pid):
        db.execute("DELETE FROM facebook_event_checkin WHERE person_id=? AND event_name=? AND date BETWEEN ? AND ?",
                   (pid, self.params["event"], self.params["lo"], self.params["hi"]))

    def plant(self, db, rng, ctx, pid):
        self._delete(db, pid)
        eid = 1000 + ctx.info.event_names.index(self.params["event"])
        for _ in range(self.params["count"]):
            db.execute("INSERT INTO facebook_event_checkin VALUES (?,?,?,?)",
                       (pid, eid, self.params["event"], rand_date(rng, self.params["lo"], self.params["hi"])))

    def unplant(self, db, rng, ctx, pid):
        self._delete(db, pid)

    def sql(self, ctx):
        d = _event_date_expr(ctx)
        return (f"SELECT person_id FROM facebook_event_checkin WHERE event_name={_q(self.params['event'])} "
                f"AND {d} BETWEEN {self.params['lo']} AND {self.params['hi']} GROUP BY person_id HAVING COUNT(*) BETWEEN "
                f"{self.params.get('count_lo', self.params['count'])} AND {self.params.get('count_hi', self.params['count'])}")

    def text(self, rng, ctx):
        e, n, m = self.params["event"], self.params["count"], month_name(self.params["lo"])
        if self.params.get("fuzzy"):
            w = self.params["word"]
            return rng.choice([f"went to the {e} {w} times in {m}", f"kept showing up at the {e} in {m}, {w} times at least"])
        return rng.choice([f"went to the {e} exactly {n} times in {m}", f"checked in at the {e} {n} times during {m}"])


@register
class EventAndNever(Predicate):
    kind = "event_and_never"
    group = "event"
    hard = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        a, b = rng.sample(ctx.info.event_names, 2)
        return cls({"event": a, "never": b})

    def plant(self, db, rng, ctx, pid):
        info = ctx.info
        db.execute("DELETE FROM facebook_event_checkin WHERE person_id=? AND event_name=?", (pid, self.params["never"]))
        db.execute("INSERT INTO facebook_event_checkin VALUES (?,?,?,?)",
                   (pid, 1000 + info.event_names.index(self.params["event"]), self.params["event"],
                    rand_date(rng, info.date_start, info.crime_date)))

    def unplant(self, db, rng, ctx, pid):
        info = ctx.info
        db.execute("INSERT INTO facebook_event_checkin VALUES (?,?,?,?)",
                   (pid, 1000 + info.event_names.index(self.params["never"]), self.params["never"],
                    rand_date(rng, info.date_start, info.crime_date)))

    def sql(self, ctx):
        return (f"SELECT DISTINCT person_id FROM facebook_event_checkin WHERE event_name={_q(self.params['event'])} "
                f"AND person_id NOT IN (SELECT person_id FROM facebook_event_checkin WHERE event_name={_q(self.params['never'])})")

    def text(self, rng, ctx):
        a, b = self.params["event"], self.params["never"]
        return rng.choice([f"has been to the {a} but has never once been to the {b}",
                           f"always goes to the {a} and refuses to go to the {b}"])


# ---------- phone predicates ----------

@register
class CalledPerson(Predicate):
    kind = "called_person"
    group = "phone"
    hard = True
    needs_known = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        if ctx.fuzzy:
            word = rng.choice(list(CALL_OFFSET_WORDS))
            o_lo, o_hi = CALL_OFFSET_WORDS[word]
            return cls({"fuzzy": True, "word": word, "callee": ctx.speaker,
                        "date_lo": add_days(ctx.info.crime_date, -o_hi), "date_hi": add_days(ctx.info.crime_date, -o_lo)})
        d = add_days(ctx.info.crime_date, -rng.randint(0, 10))
        return cls({"callee": ctx.speaker, "date_lo": d, "date_hi": d})

    def plant(self, db, rng, ctx, pid):
        db.execute("INSERT INTO phone_call VALUES (?,?,?,?,?)",
                   (pid, self.params["callee"], rand_date(rng, self.params["date_lo"], self.params["date_hi"]),
                    rand_time(rng, 700, 2300), rng.randint(30, 1800)))

    def unplant(self, db, rng, ctx, pid):
        db.execute("DELETE FROM phone_call WHERE caller_id=? AND callee_id=? AND date BETWEEN ? AND ?",
                   (pid, self.params["callee"], self.params["date_lo"], self.params["date_hi"]))

    def sql(self, ctx):
        return (f"SELECT DISTINCT caller_id FROM phone_call WHERE callee_id={self.params['callee']} "
                f"AND date BETWEEN {self.params['date_lo']} AND {self.params['date_hi']}")

    def text(self, rng, ctx):
        if self.params.get("fuzzy"):
            return rng.choice([f"called me {self.params['word']}", f"phoned me {self.params['word']}; check my phone records"])
        d = human(self.params["date_lo"])
        return rng.choice([f"called me on {d}", f"phoned me on {d}; check my phone records"])


@register
class LongestCallOnDate(Predicate):
    kind = "longest_call_on_date"
    group = "phone"
    hard = True
    exclusive = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        while True:
            date = add_days(ctx.info.crime_date, -rng.randint(0, 30))
            if f"calldate:{date}" not in ctx.reserved:
                break
        ctx.reserved.add(f"calldate:{date}")
        return cls({"date": date})

    def plant(self, db, rng, ctx, pid):
        mx = db.one("SELECT COALESCE(MAX(duration_sec),0) FROM phone_call WHERE date=?", (self.params["date"],))[0]
        callee = db.one("SELECT id FROM person WHERE id<>? ORDER BY random() LIMIT 1", (pid,))[0]
        db.execute("INSERT INTO phone_call VALUES (?,?,?,?,?)",
                   (pid, callee, self.params["date"], rand_time(rng, 700, 2200), mx + rng.randint(60, 600)))

    def unplant(self, db, rng, ctx, pid):
        db.execute("DELETE FROM phone_call WHERE caller_id=? AND date=?", (pid, self.params["date"]))

    def sql(self, ctx):
        d = self.params["date"]
        return (f"SELECT caller_id FROM phone_call WHERE date={d} "
                f"AND duration_sec=(SELECT MAX(duration_sec) FROM phone_call WHERE date={d})")

    def text(self, rng, ctx):
        d = human(self.params["date"])
        return rng.choice([f"made the single longest phone call of anyone on {d}",
                           f"was on the phone longer than anybody else that day, {d}"])


# ---------- money predicates ----------

@register
class ReceivedTransferOver(Predicate):
    kind = "received_transfer_over"
    group = "money"
    hard = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        hi = add_days(ctx.info.crime_date, -rng.randint(0, 5))
        if ctx.fuzzy:
            return cls({"fuzzy": True, "amount": 9999, "lo": add_days(ctx.info.crime_date, -30), "hi": ctx.info.crime_date})
        return cls({"amount": rng.choice([10000, 20000, 25000, 50000]), "lo": add_days(hi, -rng.randint(7, 30)), "hi": hi})

    def plant(self, db, rng, ctx, pid):
        ssn = _ensure_ssn(db, rng, pid)
        src = db.one("SELECT ssn FROM income WHERE ssn<>? ORDER BY random() LIMIT 1", (ssn,))[0]
        db.execute("INSERT INTO bank_transfer VALUES (?,?,?,?)",
                   (src, ssn, rand_date(rng, self.params["lo"], self.params["hi"]), self.params["amount"] + rng.randint(1, 20000)))

    def unplant(self, db, rng, ctx, pid):
        db.execute("DELETE FROM bank_transfer WHERE to_ssn=(SELECT ssn FROM person WHERE id=?) AND amount>? AND date BETWEEN ? AND ?",
                   (pid, self.params["amount"], self.params["lo"], self.params["hi"]))

    def sql(self, ctx):
        return (f"SELECT DISTINCT p.id FROM person p JOIN bank_transfer t ON t.to_ssn=p.ssn "
                f"WHERE t.amount>{self.params['amount']} AND t.date BETWEEN {self.params['lo']} AND {self.params['hi']}")

    def text(self, rng, ctx):
        a, lo, hi = self.params["amount"], human(self.params["lo"]), human(self.params["hi"])
        if self.params.get("fuzzy"):
            return rng.choice(["was paid a five-figure sum by bank transfer in the month leading up to the murder",
                               "received a big wire, at least five figures, sometime in the month before the murder"])
        return rng.choice([f"received a bank transfer of more than ${a:,} sometime between {lo} and {hi}",
                           f"was paid over ${a:,} by wire between {lo} and {hi}"])


@register
class TransferFromKnown(Predicate):
    kind = "transfer_from_known"
    group = "money"
    hard = True
    needs_known = True

    @classmethod
    def sample(cls, db, rng, target_id, ctx):
        return cls({"to": ctx.speaker, "date": add_days(ctx.info.crime_date, -rng.randint(0, 10))})

    def plant(self, db, rng, ctx, pid):
        src = _ensure_ssn(db, rng, pid)
        dst = _ensure_ssn(db, rng, self.params["to"])
        db.execute("INSERT INTO bank_transfer VALUES (?,?,?,?)", (src, dst, self.params["date"], rng.randint(500, 40000)))

    def unplant(self, db, rng, ctx, pid):
        db.execute("DELETE FROM bank_transfer WHERE from_ssn=(SELECT ssn FROM person WHERE id=?) "
                   "AND to_ssn=(SELECT ssn FROM person WHERE id=?) AND date=?", (pid, self.params["to"], self.params["date"]))

    def sql(self, ctx):
        return (f"SELECT DISTINCT p.id FROM person p JOIN bank_transfer t ON t.from_ssn=p.ssn "
                f"JOIN person k ON k.ssn=t.to_ssn WHERE k.id={self.params['to']} AND t.date={self.params['date']}")

    def text(self, rng, ctx):
        d = human(self.params["date"])
        return rng.choice([f"wired money to my bank account on {d}", f"paid me by bank transfer on {d}"])


# ---------- sampling ----------

def sample_kinds(rng: random.Random, ctx: Ctx, n: int, allow_hard: bool, allow_exclusive: bool = True) -> list[type[Predicate]]:
    pool = [c for c in REGISTRY.values() if allow_hard or not c.hard]
    if not allow_exclusive:
        pool = [c for c in pool if not c.exclusive]
    if not ctx.known:
        pool = [c for c in pool if not c.needs_known]
    for _ in range(1000):
        rng.shuffle(pool)
        chosen: list[type[Predicate]] = []
        groups: set[str] = set()
        for c in pool:
            if c.group in groups:
                continue
            chosen.append(c)
            groups.add(c.group)
            if len(chosen) == n:
                break
        if len(chosen) == n and (not allow_hard or any(c.hard for c in chosen)):
            return chosen
    raise RuntimeError("could not sample predicate kinds")
