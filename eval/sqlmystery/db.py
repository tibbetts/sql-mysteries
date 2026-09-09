"""Thin sqlite wrapper. The database file is the single source of truth."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Sequence

SCHEMA = """
CREATE TABLE person (
    id integer PRIMARY KEY,
    name text,
    license_id integer,
    address_number integer,
    address_street_name text,
    ssn char,
    FOREIGN KEY (license_id) REFERENCES drivers_license(id)
);
CREATE TABLE drivers_license (
    id integer PRIMARY KEY,
    age integer,
    height integer,
    eye_color text,
    hair_color text,
    gender text,
    plate_number text,
    car_make text,
    car_model text
);
CREATE TABLE income (ssn char PRIMARY KEY, annual_income integer);
CREATE TABLE crime_scene_report (date integer, type text, description text, city text);
CREATE TABLE interview (person_id integer, transcript text, FOREIGN KEY (person_id) REFERENCES person(id));
CREATE TABLE get_fit_now_member (
    id text PRIMARY KEY,
    person_id integer,
    name text,
    membership_start_date integer,
    membership_status text,
    FOREIGN KEY (person_id) REFERENCES person(id)
);
CREATE TABLE get_fit_now_check_in (
    membership_id text,
    check_in_date integer,
    check_in_time integer,
    check_out_time integer,
    FOREIGN KEY (membership_id) REFERENCES get_fit_now_member(id)
);
CREATE TABLE facebook_event_checkin (
    person_id integer,
    event_id integer,
    event_name text,
    date integer,
    FOREIGN KEY (person_id) REFERENCES person(id)
);
CREATE TABLE phone_call (
    caller_id integer,
    callee_id integer,
    date integer,
    start_time integer,
    duration_sec integer,
    FOREIGN KEY (caller_id) REFERENCES person(id),
    FOREIGN KEY (callee_id) REFERENCES person(id)
);
CREATE TABLE bank_transfer (
    from_ssn char,
    to_ssn char,
    date integer,
    amount integer,
    FOREIGN KEY (from_ssn) REFERENCES income(ssn),
    FOREIGN KEY (to_ssn) REFERENCES income(ssn)
);
"""

TABLES = [
    "person", "drivers_license", "income", "crime_scene_report", "interview",
    "get_fit_now_member", "get_fit_now_check_in", "facebook_event_checkin",
    "phone_call", "bank_transfer",
]

INDEXES = """
CREATE INDEX idx_person_license ON person(license_id);
CREATE INDEX idx_person_street ON person(address_street_name);
CREATE INDEX idx_interview_pid ON interview(person_id);
CREATE INDEX idx_member_pid ON get_fit_now_member(person_id);
CREATE INDEX idx_checkin_mid ON get_fit_now_check_in(membership_id);
CREATE INDEX idx_event_pid ON facebook_event_checkin(person_id);
CREATE INDEX idx_call_caller ON phone_call(caller_id);
CREATE INDEX idx_call_callee ON phone_call(callee_id);
CREATE INDEX idx_transfer_to ON bank_transfer(to_ssn);
CREATE INDEX idx_transfer_from ON bank_transfer(from_ssn);
"""


class Db:
    def __init__(self, path: str = ":memory:", readonly: bool = False):
        if readonly:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA temp_store=MEMORY")

    def create_schema(self) -> None:
        self.conn.executescript(SCHEMA)

    def create_indexes(self) -> None:
        self.conn.executescript(INDEXES)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self.conn.executemany(sql, rows)

    def ids(self, sql: str, params: Sequence[Any] = ()) -> list[int]:
        return [r[0] for r in self.conn.execute(sql, params)]

    def one(self, sql: str, params: Sequence[Any] = ()) -> tuple:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            raise LookupError(f"no row for: {sql} {params}")
        return row

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
