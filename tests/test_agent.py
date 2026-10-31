import asyncio
import io
import json
from dataclasses import dataclass, field

from llvm04.agent import prompts
from llvm04.agent.loop import AgentConfig, run_episode
from llvm04.agent.mcp_backend import McpBackend

PRICE = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}


@dataclass
class Usage:
    input_tokens: int = 1000
    output_tokens: int = 100
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class Resp:
    content: list
    stop_reason: str = "tool_use"
    usage: Usage = field(default_factory=Usage)
    model: str = "fake"


def tool(name, i, **args):
    return Block("tool_use", id=f"t{i}", name=name, input=args)


class FakeClient:
    """Plays back scripted responses and records every request."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        outer = self

        class Messages:
            async def create(self, **req):
                # Snapshot: the SDK serializes at call time, the loop keeps appending.
                outer.requests.append({**req, "messages": list(req["messages"])})
                r = outer.script.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r

        class Beta:
            messages = Messages()

        self.beta = Beta()


class FakeBackend:
    def __init__(self):
        self.calls, self.history = [], []

    async def schemas(self):
        return [{"name": "compile", "description": "", "input_schema": {"type": "object"}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == "compile":
            if args.get("passes") == ["bogus"]:
                return {"ok": False, "error": {"code": "INVALID_PIPELINE", "message": "x"}}
            self.history.append({"build_id": "b1", "gate": "PASS"})
            return {"ok": True, "build_id": "b1"}
        if name == "get_history":
            return {"ok": True, "candidates": self.history}
        return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "unknown"}}


def cfg(**kw):
    return AgentConfig(model="fake", price=PRICE, **kw)


def run(client, backend, c=None, budget=30):
    return asyncio.run(run_episode(client, backend, "gemm", budget, c or cfg(), io.StringIO()))


def test_submit_ends_episode_and_history_is_append_only():
    first = Resp([Block("text", "plan"), tool("compile", 1, passes=["licm"], base="O3")])
    client = FakeClient([first, Resp([tool("submit", 2, build_id="b1", summary="ok")])])
    res = run(client, FakeBackend())
    assert (res.stop, res.submitted, res.turns) == ("submitted", "b1", 2)
    req = client.requests[1]
    assert req["messages"][1] == {"role": "assistant", "content": first.content}
    results = req["messages"][2]["content"]
    assert results[0]["tool_use_id"] == "t1" and json.loads(results[0]["content"])["ok"]
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["thinking"] == {"type": "adaptive"} and req["fallbacks"] == "default"
    assert req["tools"][-1]["name"] == "submit" and req["system"] == prompts.SYSTEM
    assert abs(res.cost_usd - 2 * (1000 * 4 + 100 * 20) / 1e6) < 1e-12


def test_submission_must_be_a_gated_build_of_this_episode():
    client = FakeClient([Resp([tool("submit", 1, build_id="nope", summary="")]),
                         Resp([tool("compile", 2, passes=[])]),
                         Resp([tool("submit", 3, build_id="b1", summary="")])])
    res = run(client, FakeBackend())
    rejected = client.requests[1]["messages"][2]["content"][0]
    assert rejected["is_error"] and "not compiled" in rejected["content"]
    assert res.stop == "submitted" and res.errors["INVALID_ARGUMENT"] == 1


def test_errors_are_counted_and_passed_back():
    client = FakeClient([Resp([tool("compile", 1, passes=["bogus"]), tool("nosuch", 2)]),
                         Resp([Block("text", "done")], "end_turn")] * 1
                        + [Resp([Block("text", "done")], "end_turn")] * 2)
    res = run(client, FakeBackend())
    assert res.errors == {"INVALID_PIPELINE": 1, "INVALID_ARGUMENT": 1} and res.malformed == 2
    both = client.requests[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in both] == ["t1", "t2"] and all(r["is_error"] for r in both)


def test_no_tool_call_is_nudged_then_stops():
    client = FakeClient([Resp([Block("text", "hmm")], "end_turn")] * 3)
    res = run(client, FakeBackend())
    assert res.stop == "end_turn" and res.turns == 3
    assert client.requests[1]["messages"][-1] == {"role": "user", "content": prompts.NUDGE}


def test_hard_stops():
    loop = [Resp([tool("compile", i, passes=[])]) for i in range(10)]
    assert run(FakeClient(loop), FakeBackend(), cfg(max_turns=3)).stop == "max_turns"
    pricey = [Resp([tool("compile", i, passes=[])], usage=Usage(input_tokens=10**6))
              for i in range(10)]
    res = run(FakeClient(pricey), FakeBackend(), cfg(max_usd=5.0))
    assert res.stop == "max_usd" and res.turns == 2  # $4 per turn: stops before the third
    assert run(FakeClient([RuntimeError("boom")]), FakeBackend()).stop == "api_error"
    assert run(FakeClient([Resp([], "refusal")]), FakeBackend()).stop == "refusal"


def test_truncated_tool_call_is_not_executed():
    backend = FakeBackend()
    client = FakeClient([Resp([tool("compile", 1, passes=["licm"])], "max_tokens"),
                         Resp([tool("compile", 2, passes=["licm"])]),
                         Resp([tool("submit", 3, build_id="b1", summary="")])])
    res = run(client, backend)
    assert res.stop == "submitted" and backend.calls[0][0] == "compile"
    assert len([c for c in backend.calls if c[0] == "compile"]) == 1


def test_episode_through_the_real_mcp_server():
    async def go():
        async with McpBackend(budget=2) as be:
            names = {t["name"] for t in await be.schemas()}
            assert {"compile", "run_benchmark", "get_history"} <= names
            client = FakeClient([
                Resp([tool("compile", 1, benchmark="jacobi-2d", passes=[], base="O3",
                           rationale="baseline")]),
                Resp([tool("submit", 2, build_id="PLACEHOLDER", summary="")]),
            ])
            # Fill in the real build id once compile has run.
            orig = be.call

            async def call(name, args):
                out = await orig(name, args)
                if name == "compile":
                    client.script[0].content[0].input["build_id"] = out["build_id"]
                    gate = await orig("check_correctness", {"build_id": out["build_id"]})
                    assert gate["status"] == "PASS"
                return out

            be.call = call
            return await run_episode(client, be, "jacobi-2d", 2, cfg(), io.StringIO())

    res = asyncio.run(go())
    assert res.stop == "submitted" and res.tool_calls["compile"] == 1
