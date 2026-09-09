import json
from types import SimpleNamespace

import pytest

from harness.tools import SqlTool
from harness.run_eval import solve, wilson
from sqlmystery.config import TIERS
from sqlmystery.generate import make


@pytest.fixture(scope="module")
def instance(tmp_path_factory):
    return make(seed=9, tier=TIERS["easy"].scaled(3000), out_dir=tmp_path_factory.mktemp("inst"))


def test_sql_tool_rejects_writes(instance):
    tool = SqlTool(instance / "mystery.db")
    for q in ["INSERT INTO person VALUES (1,'x',null,1,'y',null)", "DELETE FROM person", "DROP TABLE person",
              "UPDATE person SET name='x'", "ATTACH DATABASE '/tmp/x' AS y", "PRAGMA writable_schema=1"]:
        out = tool.run(q)
        assert out.startswith("Error:"), q
    assert tool.query_count == 0


def test_sql_tool_caps_rows_and_returns_headers(instance):
    tool = SqlTool(instance / "mystery.db", max_rows=5)
    out = tool.run("SELECT id, name FROM person ORDER BY id")
    lines = out.splitlines()
    assert lines[0].split("|")[:2] == ["id", "name"]
    assert "truncated" in out
    assert len([l for l in lines if l and l[0].isdigit()]) == 5
    assert tool.query_count == 1
    assert tool.run("SELECT count(*) FROM person").splitlines()[1].strip().isdigit()
    assert "Error:" in tool.run("SELECT * FROM nope")


def test_sql_tool_allows_schema_introspection(instance):
    tool = SqlTool(instance / "mystery.db")
    assert "person" in tool.run("SELECT name FROM sqlite_master WHERE type='table'")
    assert "hair_color" in tool.run("PRAGMA table_info(drivers_license)")


class FakeClient:
    """Scripted stand-in for anthropic.Anthropic with a .messages.create method."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0) if self.script else self.script_default()
        return step

    def script_default(self):
        return tool_use_msg("run_sql", {"query": "SELECT 1"})


def tool_use_msg(name, inp, text=None):
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    blocks.append(SimpleNamespace(type="tool_use", name=name, input=inp, id=f"tu_{len(inp)}_{name}"))
    return SimpleNamespace(content=blocks, stop_reason="tool_use", stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_solve_scores_submission_and_counts_queries(instance):
    answer = json.loads((instance / "answer.json").read_text())
    client = FakeClient([
        tool_use_msg("run_sql", {"query": "SELECT * FROM crime_scene_report LIMIT 1"}, text="Looking"),
        tool_use_msg("run_sql", {"query": "SELECT * FROM interview LIMIT 1"}),
        tool_use_msg("submit_answer", {"chain": [h["name"] for h in answer["hops"]],
                                       "murderer": answer["murderer"], "mastermind": answer["mastermind"]}),
    ])
    result = solve(client, "fake-model", instance, max_turns=10, thinking=False)
    assert result["grade"]["mastermind_correct"] is True
    assert result["grade"]["murderer_correct"] is True
    assert result["queries"] == 2
    assert result["turns"] == 3
    assert result["stop"] == "submitted"
    assert result["usage"]["input_tokens"] == 300
    # the prompt reached the model and the tools were declared
    first = client.calls[0]
    assert {t["name"] for t in first["tools"]} == {"run_sql", "submit_answer"}
    assert answer["crime_city"] in first["messages"][0]["content"]
    assert "thinking" not in first


def test_solve_stops_at_max_turns(instance):
    client = FakeClient([])
    result = solve(client, "fake-model", instance, max_turns=4, thinking=False)
    assert result["stop"] == "max_turns"
    assert result["turns"] == 4
    assert result["grade"]["mastermind_correct"] is False


def test_solve_passes_thinking_when_enabled(instance):
    client = FakeClient([tool_use_msg("submit_answer", {"chain": [], "murderer": "x", "mastermind": "y"})])
    solve(client, "claude-opus-5", instance, max_turns=2, thinking=True)
    assert client.calls[0]["thinking"] == {"type": "adaptive"}


def test_wilson_interval():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.35
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 0.8
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
