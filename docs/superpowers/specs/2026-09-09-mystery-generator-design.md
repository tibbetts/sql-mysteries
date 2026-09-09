# SQL Mystery Generator and Eval Harness

Date: 2026-09-09
Status: approved in chat, implementing

## Why

The original SQL Murder Mystery is not a useful model eval:

- The answer (Jeremy Bowers, Miranda Priestly) is memorized. Haiku 4.5 names the murderer with no database access.
- The `check_solution` trigger stores both names as hex inside the DB, so `.schema` leaks the answer.
- The clue chain is six shallow hops; Haiku 4.5 solves it in 16 queries in 81 seconds.
- Each clue leaves two or three candidates; there is no noise or dirty data.
- Tables are ~10k rows, small enough to dump into context.

We need a generator that produces fresh, unmemorizable mysteries with tunable difficulty, a self-check that proves each instance has exactly one answer, an external grader, and a harness that runs a model over many seeds.

## Scope

In scope: `eval/` directory containing a Python package `sqlmystery` (generator, verifier, grader), a CLI, a Claude API harness, tests. Out of scope: changes to the web game, the original DB, unreliable-witness red herrings (noted as future work).

## Layout

```
eval/
  pyproject.toml            # uv project, deps: anthropic (harness only), pytest (dev)
  sqlmystery/
    __init__.py
    __main__.py             # CLI: make, verify, grade, batch
    config.py               # Difficulty dataclass + named tiers
    names.py                # first/last name, street, city, event, car word lists
    world.py                # base population: persons, licenses, income, gyms, events, phones, transfers
    predicates.py           # predicate library
    chain.py                # builds the clue chain, plants target + decoys, writes narrative
    dirty.py                # dirty-data transforms
    db.py                   # write world to sqlite
    verify.py               # execute reference SQL, assert uniqueness of every hop
    grade.py                # score a submitted answer
  harness/
    run_eval.py             # run a Claude model against N instances, aggregate
  tests/
```

## Output of one instance

`make --seed S --tier T --out DIR` writes:

- `DIR/mystery.db`: sqlite, no solution table, no trigger.
- `DIR/prompt.txt`: the text given to the solver (crime type, date, city, task).
- `DIR/answer.json`: hidden ground truth. Contains tier, seed, the chain as an ordered list of `{role, person_id, name, predicates: [{kind, params, sql, text}]}`, and the final answer name.

The DB never contains the answer.

## World model

Schema is a superset of the original so the original prompt style still applies:

- `person(id, name, license_id, address_number, address_street_name, ssn)`
- `drivers_license(id, age, height, eye_color, hair_color, gender, plate_number, car_make, car_model)`
- `income(ssn, annual_income)`
- `crime_scene_report(date, type, description, city)`
- `interview(person_id, transcript)`
- `get_fit_now_member(id, person_id, name, membership_start_date, membership_status)`
- `get_fit_now_check_in(membership_id, check_in_date, check_in_time, check_out_time)`
- `facebook_event_checkin(person_id, event_id, event_name, date)`
- `phone_call(caller_id, callee_id, date, start_time, duration_sec)`  (new: self-join, temporal)
- `bank_transfer(from_ssn, to_ssn, date, amount)`  (new: ranking, set reasoning)

Population size is a tier parameter. Row generation is streaming with `executemany` so 1M persons is feasible.

## Chain model

A chain is an ordered list of persons: `witness+ -> murderer -> accomplice* -> mastermind`. Each edge is a **clue set**: a conjunction of k predicates, expressed in the source's interview transcript (or in the crime scene report for witnesses), that identifies the target.

Predicate contract (`predicates.py`):

```python
class Predicate:
    kind: str
    params: dict
    def sql(self) -> str          # SELECT person.id ... WHERE <predicate>; returns matching person ids
    def text(self) -> str         # natural-language clue sentence
    def plant(self, world, target_id): ...   # make target satisfy it
    def plant_decoy(self, world, decoy_id): ...  # make decoy satisfy it
```

Predicate kinds, grouped by the SQL skill they exercise:

| kind | skill | example text |
|---|---|---|
| hair_color, eye_color, gender | equality | "a woman with red hair" |
| height_range, age_range | BETWEEN, unit conversion | "between 5'5\" and 5'7\"" |
| car | join | "drives a Tesla Model S" |
| plate_fragment | LIKE | "plate included H42W" |
| street | equality | "lives on Franklin Ave" |
| last_house_on_street | MAX subquery | "the last house on Northwestern Dr" |
| income_rank_on_street | window/subquery | "richest person on their street" |
| gym_status_prefix | LIKE + equality | "gold member, id starts with 48Z" |
| gym_checkin_window | date + time range | "at the gym on Jan 9 between 3pm and 5pm" |
| event_count | GROUP BY HAVING | "went to the SQL Symphony 3 times in December" |
| event_and_never | set difference | "was at the Symphony but never the Opera" |
| called_person | self-join | "called the murderer the night before" |
| longest_call_on_date | ranking | "the longest call made on Jan 14" |
| received_transfer_over | join + comparison | "received more than $50,000 from someone" |
| transfer_from_chain_person | join to known person | "was paid by the accomplice" |

Predicates that reference an earlier chain person (`called_person`, `transfer_from_chain_person`) are only used once that person is known, which forces sequential solving.

## Planting algorithm

For each edge (source -> target):

1. Sample k predicate kinds allowed by the tier. At least one must be a "hard" kind for tiers >= hard.
2. Instantiate each predicate from the target's attributes (or plant attributes on the target if the kind needs specific values, e.g. event attendance).
3. For each predicate independently, plant `d` decoys: random other persons mutated to satisfy that one predicate. Decoys for different predicates are disjoint sets, so no decoy satisfies the full conjunction by construction.
4. Also plant `d/4` "near miss" decoys that satisfy all but one predicate of the conjunction. These are the ones that punish sloppy reasoning.
5. Run the conjunction SQL against the in-memory world. If more than one row matches (random collision), re-mutate the colliders and retry; after 10 retries, resample predicates.

Chain persons have globally unique names in the DB. Witnesses are identified from the crime scene report using the same predicate machinery.

## Narrative

Transcripts are built from predicate `text()` joined with light templating and randomized framing sentences. Non-chain interviews are filler drawn from public-domain text, as in the original. Decoy persons also get transcripts that mention chain-adjacent facts so `LIKE '%gym%'` over transcripts is not a shortcut. Crime scene reports for the target date/city include several non-murder reports and reports from other cities on the same date.

## Dirty data (tiers >= hard)

Applied after planting, before verification, so the reference SQL is written against the dirty form and verification proves solvability:

- `facebook_event_checkin.date` stored as text `YYYY-MM-DD` while other tables keep integer `YYYYMMDD`.
- 5% of `person.name` values get trailing whitespace or altered case. Chain persons excluded.
- 3% of persons duplicated with a new id and NULL `license_id`. Chain persons excluded.
- `drivers_license.height` occasionally NULL for non-chain rows.
- `get_fit_now_check_in.check_in_time` mixed `HHMM` int and `HH:MM` text at extreme tier.

The predicate SQL for affected columns normalizes appropriately. Duplicated persons make "unique name" answers ambiguous only for non-chain people, so grading by chain name stays sound.

## Tiers

| tier | persons | chain length | preds/edge | decoys/pred | hard kinds | dirty |
|---|---|---|---|---|---|---|
| easy | 10k | 3 | 2 | 3 | no | no |
| medium | 50k | 5 | 3 | 20 | no | no |
| hard | 200k | 8 | 3-4 | 50 | yes | yes |
| extreme | 1M | 12 | 4 | 100 | yes | yes |

Chain length counts identified persons, witnesses included.

## Verification

`verify DIR` opens the DB read-only, and for every edge runs the conjunction of the stored predicate SQL and asserts exactly one row equal to the stored target id. Also asserts the final answer name is unique in `person` after trimming and lowercasing. `make` runs verify before returning, and fails loudly rather than emitting a broken instance.

## Grading

`grade answer.json submission.json` where submission is `{"chain": ["name", ...], "murderer": "name", "mastermind": "name"}`. Names compared after trim + casefold. Output:

```json
{"mastermind_correct": bool, "murderer_correct": bool, "chain_recall": float, "chain_precision": float}
```

Primary metric is `mastermind_correct`.

## Harness

`harness/run_eval.py --tier hard --seeds 0-99 --model claude-sonnet-5 --out results/`:

- Generates each instance to a temp dir (or reuses if present).
- Runs an agent loop with two tools: `run_sql(query)` (read-only connection, result capped at 200 rows and 20k characters, execution time capped) and `submit_answer(chain, murderer, mastermind)`.
- Caps turns and total tokens per instance (tier-dependent).
- Records per-instance: grade, query count, turns, input/output tokens, wall time, transcript.
- Aggregates: pass rate with Wilson 95% interval, median queries, and per-predicate-kind failure attribution (which edge the model got wrong first).

## Testing

- Unit: each predicate's `sql()` returns the planted target and every planted decoy; `text()` non-empty.
- Property: for seeds 0-19 across all tiers at reduced population, `verify` passes.
- Dirty transforms preserve verification.
- Grader edge cases: case and whitespace, partial chains, empty submission.
- Harness: tool executor rejects writes and caps rows; agent loop tested with a fake client.

## Future work

Unreliable witness whose statement is flagged as uncertain; multiple crimes in one DB; schema randomization (renamed columns) to prevent template overfitting.
