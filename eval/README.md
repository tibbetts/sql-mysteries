# SQL Mystery Eval

A procedural generator for SQL murder mysteries, plus a harness for benchmarking language models on them.

## Why the original is not a benchmark

The original SQL Murder Mystery in this repo is a good teaching puzzle and a poor model eval:

- **It is memorized.** Haiku 4.5, asked cold with no database access, named Jeremy Bowers as the murderer.
- **The answer is in the database.** The `check_solution` trigger stores both names as hex. `.schema` leaks the solution.
- **It is shallow.** Six hops, all equality filters and single joins. Haiku 4.5 solved it in 16 queries and 81 seconds.
- **The clues barely need combining.** Each clue leaves two or three candidates.
- **It is small.** About 10k rows per table fits in a long context window without any SQL.

## What this generates

Each instance is a fresh SQLite database with no answer key inside, a `prompt.txt`, and a hidden `answer.json`. The database has the original nine tables plus `phone_call` and `bank_transfer`. A chain of persons (witnesses, murderer, accomplices, mastermind) is planted so that each hop is identified only by the conjunction of several clues given in the previous person's interview transcript. Decoys satisfy each clue alone and near-miss decoys satisfy all but one. Every instance is verified before it is written: the reference SQL for every hop must return exactly the planted person.

Clue kinds and the SQL skill each exercises:

| kind | skill |
|---|---|
| hair_color, eye_color, gender, car, street | equality and joins |
| height_range, age_range | BETWEEN, unit conversion from feet and inches |
| plate_fragment, gym_status_prefix | LIKE |
| last_house_on_street | MAX subquery |
| income_rank_on_street | correlated max over a join |
| gym_checkin_window | date plus time window |
| event_count | GROUP BY HAVING |
| event_and_never | set difference |
| called_person, transfer_from_known | join back to a person already identified |
| longest_call_on_date | ranking |
| received_transfer_over | join plus comparison over a window |

Tiers:

| tier | persons | chain | clues per hop | decoys per clue | hard kinds | dirty data | gen time | db size |
|---|---|---|---|---|---|---|---|---|
| easy | 10k | 3 | 2 | 3 | no | no | 0.3 s | 6 MB |
| medium | 50k | 5 | 3 | 20 | no | no | 2 s | 25 MB |
| hard | 200k | 8 | 3 to 4 | 50 | yes | yes | 13 s | 102 MB |
| extreme | 1M | 12 | 4 | 100 | yes | yes | 153 s | 512 MB |

Dirty data (hard and extreme): event dates stored as `YYYY-MM-DD` text while other tables use integer `YYYYMMDD`, mixed `HHMM` integers and `HH:MM` text in gym check-in times (extreme only), mangled name casing and whitespace, duplicate persons with NULL license and SSN, NULL heights. Chain persons are never mangled, so grading by name stays sound.

Random rumour transcripts repeat clue-shaped facts about unrelated people, so text search over interviews is not a shortcut.

## Usage

```bash
cd eval
uv sync --extra harness

# one instance
uv run python -m sqlmystery make --tier hard --seed 0 --out out/hard/seed_0000

# many
uv run python -m sqlmystery batch --tier medium --seeds 0-99 --out out

# re-check an instance
uv run python -m sqlmystery verify out/hard/seed_0000

# grade a submission {"chain": [...], "murderer": "...", "mastermind": "..."}
uv run python -m sqlmystery grade out/hard/seed_0000/answer.json submission.json
```

Run a model (needs `ANTHROPIC_API_KEY` or an `ant auth login` profile):

```bash
uv run python -m harness.run_eval --tier hard --seeds 0-49 --model claude-opus-5 --effort high --out results
uv run python -m harness.run_eval --tier hard --seeds 0-49 --model claude-haiku-4-5 --no-thinking
```

The harness gives the model two tools: `run_sql` (read-only connection, 200 rows and 20k characters per result, 30 s timeout) and `submit_answer`. Turn caps are 30/50/80/120 by tier. Per-instance JSON includes the grade, query count, turns, token usage, and a transcript. `summary.json` reports pass rates with Wilson 95% intervals. Refusal fallbacks are deliberately off so a refusal is recorded as a failure rather than answered by a different model.

Metrics:

- `mastermind_correct` is the primary metric.
- `murderer_correct` and `chain_recall` give partial credit and show where chains break.
- `queries` and `turns` measure efficiency.

## Smoke results

Subagents driving `sqlite3` directly (not the API harness, which needs credentials), one seed each:

| model | tier | seed | result | queries | wall time |
|---|---|---|---|---|---|
| Haiku 4.5 | easy | 0 | full chain correct | 8 | 53 s |
| Sonnet 5 | hard | 0 | full chain of 8 correct | 44 | 138 s |
| Haiku 4.5 | hard | 0 | full chain of 8 correct | 36 | 212 s |

Read this as a floor, not a ceiling: with one seed each, even the smallest model clears the hard tier. Every clue is precise and maps to one SQL predicate, so the chain is long but each hop is mechanical. What separates models at this point is efficiency (queries, turns, tokens) and robustness across many seeds. To make the top tiers discriminate on correctness, the next additions should be clue kinds that need reasoning rather than translation: relative clues (taller than the murderer, lives within a few house numbers of the witness), two-hop relations (called someone who called the victim), contradictions between witnesses with a hint about which one is reliable, and clue text with less templated phrasing.

## Layout

```
sqlmystery/
  config.py      tiers
  world.py       base population
  predicates.py  clue kinds: plant, unplant, sql, text
  chain.py       chain construction, decoys, collision resolution
  narrative.py   crime report and transcripts
  dirty.py       dirty-data transforms
  generate.py    make one instance
  verify.py      re-run reference SQL, assert uniqueness
  grade.py       score a submission
harness/
  tools.py       read-only SqlTool
  run_eval.py    agent loop, aggregation
tests/
```

## Extending

Add a clue kind by subclassing `Predicate` in `predicates.py` and registering it. Implement `sample`, `plant`, `unplant`, `sql`, `text`. Set `hard`, `exclusive` (at most one person can satisfy it, so no decoys), `needs_known` (references the speaker), and `group` (at most one predicate per group per hop). The parametrized tests in `tests/test_predicates.py` cover every registered kind automatically.

## Future work

- An unreliable witness whose statement is flagged as uncertain and partly wrong.
- Multiple murders in one database so the date and city filter matters more.
- Renamed columns per seed to defeat template overfitting.
