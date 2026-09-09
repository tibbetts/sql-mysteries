"""Run a Claude model against generated mysteries and report pass rates.

Usage:
  uv run python -m harness.run_eval --tier hard --seeds 0-19 --model claude-opus-5 --out results
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlmystery.__main__ import instance_dir, parse_seeds
from sqlmystery.config import TIERS
from sqlmystery.generate import make
from sqlmystery.grade import grade

from .tools import SqlTool

SYSTEM = """You are a detective who can only investigate by running SQL queries against a SQLite database using the run_sql tool. Start by learning the schema (sqlite_master, PRAGMA table_info). Follow the evidence step by step. Data may be messy: check formats before filtering. Do not guess; every person you name must be supported by query results. When you have identified everyone, call submit_answer exactly once with the full chain in the order you discovered them."""

TOOLS = [
    {
        "name": "run_sql",
        "description": "Run one read-only SQL query against the case database. Returns pipe-separated rows with a header line; output is capped at 200 rows and 20k characters.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "submit_answer",
        "description": "Submit the final answer. Call it once, when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chain": {"type": "array", "items": {"type": "string"},
                          "description": "Full names of every person identified, in the order discovered, witnesses first, mastermind last."},
                "murderer": {"type": "string", "description": "Full name of the murderer."},
                "mastermind": {"type": "string", "description": "Full name of the person ultimately behind the crime."},
            },
            "required": ["chain", "murderer", "mastermind"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _text(blocks) -> str:
    return "\n".join(b.text for b in blocks if getattr(b, "type", "") == "text")


def solve(client, model: str, instance: Path, max_turns: int = 60, thinking: bool = True,
          effort: str | None = None, max_tokens: int = 16000) -> dict:
    instance = Path(instance)
    answer = json.loads((instance / "answer.json").read_text())
    prompt = (instance / "prompt.txt").read_text()
    tool = SqlTool(instance / "mystery.db")
    messages = [{"role": "user", "content": prompt}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    transcript: list[dict] = []
    submission: dict | None = None
    stop = "max_turns"
    turns = 0
    t0 = time.time()
    try:
        while turns < max_turns:
            turns += 1
            kwargs = dict(model=model, max_tokens=max_tokens, system=SYSTEM, tools=TOOLS, messages=messages)
            if thinking:
                kwargs["thinking"] = {"type": "adaptive"}
            if effort:
                kwargs["output_config"] = {"effort": effort}
            response = client.messages.create(**kwargs)
            for k in usage:
                usage[k] += getattr(response.usage, k, 0) or 0
            transcript.append({"role": "assistant", "text": _text(response.content),
                               "tool_calls": [{"name": b.name, "input": b.input} for b in response.content if b.type == "tool_use"]})
            if response.stop_reason == "refusal":
                stop = "refusal"
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                stop = "end_turn_without_submit" if response.stop_reason == "end_turn" else response.stop_reason
                break
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for tu in tool_uses:
                if tu.name == "run_sql":
                    out = tool.run(tu.input.get("query", ""))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out,
                                    "is_error": out.startswith("Error:")})
                    transcript.append({"role": "tool", "name": "run_sql", "query": tu.input.get("query", ""), "result": out[:2000]})
                elif tu.name == "submit_answer":
                    submission = dict(tu.input)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "Answer recorded."})
                else:
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"Error: unknown tool {tu.name}", "is_error": True})
            messages.append({"role": "user", "content": results})
            if submission is not None:
                stop = "submitted"
                break
    finally:
        tool.close()
    g = grade(answer, submission or {})
    return {
        "tier": answer["tier"], "seed": answer["seed"], "model": model,
        "grade": g, "submission": submission, "stop": stop, "turns": turns,
        "queries": tool.query_count, "sql_errors": tool.error_count,
        "usage": usage, "seconds": round(time.time() - t0, 1),
        "expected": {"murderer": answer["murderer"], "mastermind": answer["mastermind"],
                     "chain": [h["name"] for h in answer["hops"]]},
        "transcript": transcript,
    }


def summarize(results: list[dict]) -> dict:
    n = len(results)
    mm = sum(r["grade"]["mastermind_correct"] for r in results)
    mu = sum(r["grade"]["murderer_correct"] for r in results)
    stops = {}
    for r in results:
        stops[r["stop"]] = stops.get(r["stop"], 0) + 1
    return {
        "n": n,
        "mastermind_pass_rate": mm / n if n else 0.0,
        "mastermind_ci95": wilson(mm, n),
        "murderer_pass_rate": mu / n if n else 0.0,
        "murderer_ci95": wilson(mu, n),
        "mean_chain_recall": statistics.fmean(r["grade"]["chain_recall"] for r in results) if n else 0.0,
        "median_queries": statistics.median(r["queries"] for r in results) if n else 0,
        "median_turns": statistics.median(r["turns"] for r in results) if n else 0,
        "mean_seconds": statistics.fmean(r["seconds"] for r in results) if n else 0.0,
        "total_input_tokens": sum(r["usage"]["input_tokens"] + r["usage"]["cache_read_input_tokens"] + r["usage"]["cache_creation_input_tokens"] for r in results),
        "total_output_tokens": sum(r["usage"]["output_tokens"] for r in results),
        "stops": stops,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=TIERS, default="medium")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--persons", type=int, help="override population size when generating")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--no-thinking", action="store_true", help="omit the thinking parameter (required for Haiku 4.5)")
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--instances", type=Path, default=Path("out"), help="root of generated instances; missing ones are generated")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    import anthropic  # imported here so the generator stays dependency-free

    client = anthropic.Anthropic()
    tier = TIERS[args.tier]
    if args.persons:
        tier = tier.scaled(args.persons)
    max_turns = args.max_turns or {"easy": 30, "medium": 50, "hard": 80, "extreme": 120}[args.tier]
    thinking = not args.no_thinking and "haiku-4-5" not in args.model
    out_dir = args.out / args.tier / args.model.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(seed: int) -> dict:
        inst = instance_dir(args.instances, args.tier, seed)
        if not (inst / "answer.json").exists():
            make(seed, tier, inst)
        res_path = out_dir / f"seed_{seed:04d}.json"
        if res_path.exists():
            return json.loads(res_path.read_text())
        r = solve(client, args.model, inst, max_turns=max_turns, thinking=thinking, effort=args.effort)
        res_path.write_text(json.dumps(r, indent=2))
        g = r["grade"]
        print(f"{args.tier} seed {seed}: mastermind={'PASS' if g['mastermind_correct'] else 'fail'} "
              f"murderer={'PASS' if g['murderer_correct'] else 'fail'} queries={r['queries']} turns={r['turns']} stop={r['stop']}", flush=True)
        return r

    seeds = parse_seeds(args.seeds)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(one, seeds))
    summary = {"tier": args.tier, "model": args.model, "effort": args.effort, **summarize(results)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
