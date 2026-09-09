# Mystery Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A seeded generator that emits fresh SQL murder mysteries of tunable difficulty, a verifier proving each has one answer, a grader, and a Claude harness.

**Architecture:** The sqlite file is the single source of truth. `world.py` populates it; predicates plant and unplant facts on persons via SQL; `chain.py` composes predicates into a clue chain and writes narrative; `verify.py` re-executes the stored reference SQL. Dirty transforms run after planting and predicate SQL is dirty-aware, so verification proves solvability on the final file.

**Tech Stack:** Python 3.12, stdlib sqlite3, uv, pytest. `anthropic` only in the harness.

**Spec:** `docs/superpowers/specs/2026-09-09-mystery-generator-design.md`

## Global Constraints

- Everything under `eval/`. Package name `sqlmystery`. Run tests with `cd eval && uv run pytest`.
- The DB never contains the answer. No `solution` table, no trigger.
- Every emitted instance passes `verify` or `make` exits non-zero.
- Determinism: same seed and tier produce byte-identical `answer.json`.
- Dates in integer `YYYYMMDD` unless a dirty flag says otherwise; times as integer `HHMM`.

---

### Task 1: Project scaffold, config, DB schema

**Files:** `eval/pyproject.toml`, `eval/sqlmystery/__init__.py`, `eval/sqlmystery/config.py`, `eval/sqlmystery/db.py`, `eval/tests/test_db.py`

**Produces:**
- `Tier` dataclass: `name, persons, chain_length, preds_per_edge: tuple[int,int], decoys_per_pred, hard_kinds: bool, dirty: bool`. `TIERS: dict[str, Tier]` with easy/medium/hard/extreme per spec table. `Tier.scaled(persons)` returns a copy with a smaller population for tests.
- `Db(path)`: wraps `sqlite3.connect`, `create_schema()`, `ids(sql, params=()) -> list[int]`, `one(sql, params=()) -> tuple`, `execute`, `executemany`, `close()`. Schema is the spec's 10 tables.

- [ ] Test: `create_schema` yields the 10 table names, and `ids("SELECT id FROM person")` is empty.
- [ ] Implement, run, commit.

### Task 2: World population

**Files:** `eval/sqlmystery/names.py`, `eval/sqlmystery/world.py`, `eval/tests/test_world.py`

**Produces:** `populate(db, rng, tier, crime_date: int) -> WorldInfo` where `WorldInfo` has `streets: list[str]`, `cities: list[str]`, `crime_city: str`, `crime_date: int`, `event_names: list[str]`, `car_models: list[tuple[str,str]]`, `hair_colors`, `eye_colors`. Populates persons (unique ids, names from name lists, 1 in 30 names repeated to make names non-unique), licenses for 95% of persons, income for 75%, gym members (2%), gym check-ins, event check-ins, phone calls (avg 2 per person), transfers (avg 0.5 per person), filler interviews for 50% of persons, crime reports (several per day across cities, including the crime date/city with non-murder types). Uses `executemany` in batches of 10k.

- [ ] Test: populate 2000 persons; row counts in expected ranges; every `person.license_id` references a license; every gym check-in references a member; crime date/city has at least 3 reports of which none are `murder` yet.
- [ ] Implement, run, commit.

### Task 3: Predicate library

**Files:** `eval/sqlmystery/predicates.py`, `eval/tests/test_predicates.py`

**Produces:**
```python
@dataclass
class Ctx:  # what predicates may reference
    info: WorldInfo
    dirty: DirtyFlags
    known: list[tuple[str,int]]   # (role, person_id) already identified in the chain
class Predicate:
    kind: ClassVar[str]; hard: ClassVar[bool] = False; needs_known: ClassVar[bool] = False
    params: dict
    @classmethod
    def sample(cls, db, rng, target_id, ctx) -> "Predicate"   # choose params, reading target when sensible
    def plant(self, db, rng, pid) -> None      # make pid satisfy
    def unplant(self, db, rng, pid) -> None    # make pid not satisfy
    def sql(self, ctx) -> str                  # SELECT id FROM person ... matching ids
    def text(self, rng, ctx) -> str            # clue sentence
    def to_json(self) -> dict
REGISTRY: dict[str, type[Predicate]]
def sample_kinds(rng, ctx, n, allow_hard) -> list[type[Predicate]]
```
`DirtyFlags` dataclass lives here: `event_date_text: bool, checkin_time_text: bool` (name/dup dirt does not affect SQL).
Kinds implemented: hair_color, eye_color, gender, height_range, age_range, car, plate_fragment, street, last_house_on_street, income_rank_on_street, gym_status_prefix, gym_checkin_window, event_count, event_and_never, called_person (needs_known), longest_call_on_date, received_transfer_over, transfer_from_known (needs_known).

- [ ] Test (parametrized over every kind): on a 2000-person world, sample for a target, plant on target and on 5 decoys, `sql()` returns a set containing all six; `unplant` on one decoy removes it; `text()` non-empty; `to_json` round-trips through `from_json`.
- [ ] Test: `sample_kinds` never returns `needs_known` kinds when `ctx.known` is empty, and returns a hard kind when `allow_hard`.
- [ ] Implement, run, commit.

### Task 4: Chain builder and narrative

**Files:** `eval/sqlmystery/chain.py`, `eval/sqlmystery/narrative.py`, `eval/tests/test_chain.py`

**Produces:** `build_chain(db, rng, tier, info) -> Chain` where `Chain` is a list of `Hop(role, person_id, name, predicates: list[Predicate])` plus `dirty: DirtyFlags`. Roles: `witness`, `murderer`, `accomplice`, `mastermind`. Algorithm per spec: choose distinct chain persons with globally unique names, per hop sample predicates, plant target, plant `decoys_per_pred` disjoint single-predicate decoys, `decoys_per_pred // 4` near-miss decoys, resolve collisions by `unplant` of a random predicate, retry sampling after 10 failed attempts. `narrative.write(db, rng, chain, info)` writes the murder crime scene report (witness clues), witness transcripts (murderer clues split across witnesses), and each chain person's transcript (next hop clues); it also adds themed decoy transcripts. `conjunction_sql(preds, ctx) -> str`.

- [ ] Test: for seeds 0-4 on a scaled medium tier, each hop's conjunction returns exactly `[target_id]`; chain names are unique in `person`; the crime report row exists with type murder at the crime date/city; every chain person except the mastermind has a transcript mentioning each predicate text of the next hop.
- [ ] Implement, run, commit.

### Task 5: Dirty transforms

**Files:** `eval/sqlmystery/dirty.py`, `eval/tests/test_dirty.py`

**Produces:** `apply(db, rng, chain, flags, protect: set[int])`: converts `facebook_event_checkin.date` to `YYYY-MM-DD` text if `flags.event_date_text`; mixes `check_in_time` to `HH:MM` text for half the rows if `flags.checkin_time_text`; mangles 5% of non-protected names (trailing space or casefold); duplicates 3% of non-protected persons with new ids and NULL license; NULLs height for 2% of non-protected licenses.

- [ ] Test: after apply on a built chain, every hop conjunction still returns exactly the target; some names now differ from their trimmed form; duplicate count > 0.
- [ ] Implement, run, commit.

### Task 6: make, verify, grade, CLI

**Files:** `eval/sqlmystery/generate.py`, `eval/sqlmystery/verify.py`, `eval/sqlmystery/grade.py`, `eval/sqlmystery/__main__.py`, `eval/tests/test_generate.py`, `eval/tests/test_grade.py`

**Produces:**
- `generate.make(seed, tier: Tier, out_dir) -> Path`: runs populate, build_chain, narrative, dirty, VACUUM, writes `prompt.txt` and `answer.json`, runs `verify.verify(out_dir)`, raises `VerificationError` on failure.
- `verify.verify(out_dir) -> None`: reopens DB read-only (`file:...?mode=ro`), rebuilds predicates from JSON, checks each hop, checks final name uniqueness.
- `grade.grade(answer: dict, submission: dict) -> dict` per spec.
- CLI `python -m sqlmystery make|verify|grade|batch`. `batch --tier T --seeds 0-9 --out DIR` makes one subdir per seed.

- [ ] Test: `make(seed=1, easy scaled to 3000)` produces the three files; `answer.json` identical across two runs; verify passes; DB has no `solution` table and no triggers; `make` with a deliberately corrupted DB fails verify.
- [ ] Test grade: exact, case/space-insensitive, wrong mastermind, partial chain, empty submission.
- [ ] Implement, run, commit.

### Task 7: Claude harness

**Files:** `eval/harness/run_eval.py`, `eval/harness/tools.py`, `eval/tests/test_harness.py`

**Produces:** `tools.SqlTool(db_path, max_rows=200, max_chars=20000)` with `run(query) -> str` on a read-only connection, rejects statements that are not SELECT/WITH/EXPLAIN/PRAGMA table_info, and enforces a 30 s timeout via `conn.interrupt`. `run_eval.solve(client, model, instance_dir, max_turns, tier) -> dict` runs the tool loop with `run_sql` and `submit_answer` tools and returns grade plus counters. `run_eval.main()` parses args, generates/reuses instances, runs seeds sequentially or with a thread pool, writes `results/<tier>/<seed>.json` and `summary.json` with Wilson interval.

- [ ] Test: SqlTool rejects `INSERT`, caps rows, returns column headers; `solve` with a fake client that emits a scripted tool sequence yields the expected grade and query count.
- [ ] Implement, run, commit.

### Task 8: Docs and smoke run

**Files:** `eval/README.md`, `README.md` (add a short pointer section)

- [ ] Write README with usage, tiers, metrics, and the contamination findings from the assessment.
- [ ] Run `make` for easy/medium/hard at full size; record generation times in README.
- [ ] Run the harness with Haiku 4.5 on 5 seeds of easy and 5 of hard; record results in README.
- [ ] Commit.
