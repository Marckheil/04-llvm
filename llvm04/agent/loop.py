"""One tuning episode: an LLM drives the tool layer until it submits (Phase 3).

A manual tool-use loop rather than the SDK's tool runner, because the harness owns
things the runner does not expose: a hard turn cap and dollar cap, a `submit` tool
that ends the episode and is validated against the episode history, a per-turn
transcript, and token/cost accounting. The conversation is append-only (assistant
content, thinking blocks included, is passed back exactly as returned), which keeps
the prompt cache warm and preserved thinking valid.

The model client and the tool backend are injected, so tests drive the loop with a
scripted client and a fake backend.
"""

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol, TextIO

from llvm04.agent import prompts

SUBMIT = {
    "name": "submit",
    "description": "End the episode with the build you believe is fastest. It must be a "
                   "build_id from this episode that passed check_correctness.",
    "input_schema": {
        "type": "object",
        "properties": {
            "build_id": {"type": "string", "description": "The build to submit."},
            "summary": {"type": "string",
                        "description": "What you found and why this build is the best."},
        },
        "required": ["build_id", "summary"],
        "additionalProperties": False,
    },
}
MALFORMED = ("INVALID_ARGUMENT", "INVALID_PIPELINE")


class Backend(Protocol):
    async def schemas(self) -> list[dict]: ...
    async def call(self, name: str, args: dict) -> dict: ...


@dataclass
class AgentConfig:
    model: str
    price: dict  # USD per million tokens: input, output, cache_write, cache_read
    effort: str = "high"
    max_tokens: int = 16000
    max_turns: int = 80
    max_usd: float = 5.0
    max_nudges: int = 2
    fallbacks: bool = True
    min_compiles: int = 0  # submit is refused until this much compile budget is spent (D15)


@dataclass
class EpisodeResult:
    benchmark: str
    stop: str = ""  # submitted, end_turn, max_turns, max_usd, refusal, api_error
    submitted: str | None = None
    summary: str = ""
    turns: int = 0
    tool_calls: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    usage: Counter = field(default_factory=Counter)
    cost_usd: float = 0.0
    wall_s: float = 0.0
    detail: str = ""

    @property
    def malformed(self) -> int:
        return sum(self.errors[c] for c in MALFORMED)


def _cost(usage: Any, price: dict) -> float:
    return (usage.input_tokens * price["input"]
            + (usage.cache_creation_input_tokens or 0) * price["cache_write"]
            + (usage.cache_read_input_tokens or 0) * price["cache_read"]
            + usage.output_tokens * price["output"]) / 1e6


def _dump(block: Any) -> Any:
    return block.model_dump(mode="json") if hasattr(block, "model_dump") else block


async def _check_submission(backend: Backend, benchmark: str, build_id: str,
                            min_compiles: int = 0) -> str | None:
    """None if `build_id` is a gated build of this episode, else the reason it is not."""
    hist = await backend.call("get_history", {"benchmark": benchmark})
    used = hist.get("budget_used", 0)
    if used < min_compiles:
        return (f"submit is accepted only after {min_compiles} compiles; you have used {used}, "
                f"so {min_compiles - used} remain. Keep exploring.")
    entry = next((c for c in hist.get("candidates", []) if c["build_id"] == build_id), None)
    if entry is None:
        return f"{build_id!r} was not compiled in this episode"
    if entry.get("gate") != "PASS":
        return f"{build_id!r} has not passed check_correctness (gate: {entry.get('gate')})"
    return None


async def run_episode(client: Any, backend: Backend, benchmark: str, budget: int,
                      cfg: AgentConfig, transcript: TextIO | None = None) -> EpisodeResult:
    res = EpisodeResult(benchmark)
    t0 = time.perf_counter()
    tools = [*await backend.schemas(), SUBMIT]
    messages: list[dict] = [{"role": "user",
                             "content": prompts.task(benchmark, budget, cfg.min_compiles)}]
    nudges = 0

    def log(rec: dict) -> None:
        if transcript:
            transcript.write(json.dumps(rec, default=str) + "\n")
            transcript.flush()

    while True:
        if res.turns >= cfg.max_turns:
            res.stop = "max_turns"
            break
        if res.cost_usd >= cfg.max_usd:
            res.stop = "max_usd"
            break
        req = dict(model=cfg.model, max_tokens=cfg.max_tokens, system=prompts.SYSTEM,
                   tools=tools, messages=messages, thinking={"type": "adaptive"},
                   output_config={"effort": cfg.effort}, cache_control={"type": "ephemeral"})
        if cfg.fallbacks:
            req.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            resp = await client.beta.messages.create(**req)
        except Exception as e:  # retries are the SDK's job; anything left ends the episode
            res.stop, res.detail = "api_error", f"{type(e).__name__}: {str(e)[:300]}"
            break
        res.turns += 1
        u = resp.usage
        res.usage.update(input=u.input_tokens, output=u.output_tokens,
                         cache_write=u.cache_creation_input_tokens or 0,
                         cache_read=u.cache_read_input_tokens or 0)
        res.cost_usd += _cost(u, cfg.price)
        log({"turn": res.turns, "stop_reason": resp.stop_reason, "model": resp.model,
             "content": [_dump(b) for b in resp.content], "usage": _dump(u),
             "cost_usd": res.cost_usd})
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            res.stop = "refusal"
            res.detail = str(getattr(resp, "stop_details", "") or "")[:300]
            break
        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            if nudges >= cfg.max_nudges:
                res.stop = "end_turn"
                break
            nudges += 1
            messages.append({"role": "user", "content": prompts.NUDGE})
            log({"turn": res.turns, "user": prompts.NUDGE})
            continue

        results, submitted = [], False
        for call in calls:
            res.tool_calls[call.name] += 1
            if resp.stop_reason == "max_tokens":
                out = {"ok": False, "error": {"code": "INVALID_ARGUMENT",
                                              "message": prompts.TRUNCATED}}
            elif call.name == "submit":
                args = call.input if isinstance(call.input, dict) else {}
                why = await _check_submission(backend, benchmark, str(args.get("build_id")),
                                              cfg.min_compiles)
                if why:
                    out = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": why}}
                else:
                    res.submitted, res.summary = args["build_id"], str(args.get("summary", ""))
                    out, submitted = {"ok": True, "message": "Submitted. Episode over."}, True
            else:
                out = await backend.call(call.name, call.input)
            if not out.get("ok", False):
                res.errors[out["error"]["code"]] += 1
            log({"turn": res.turns, "tool": call.name, "args": call.input, "result": out})
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": json.dumps(out), "is_error": not out.get("ok", False)})
        messages.append({"role": "user", "content": results})
        if submitted:
            res.stop = "submitted"
            break

    res.wall_s = time.perf_counter() - t0
    log({"end": res.stop, "submitted": res.submitted, "turns": res.turns,
         "cost_usd": res.cost_usd, "detail": res.detail})
    return res
