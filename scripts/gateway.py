#!/usr/bin/env python3
"""A tiny OpenAI-compatible gateway that every harness in this experiment talks to.

Why this exists
---------------
The experiment holds the *model* fixed and varies only the *harness*. Pointing each
harness at its own provider SDK would undermine that: harnesses differ in which
provider they support, which API surface they call (chat completions vs. responses),
and what sampling defaults they inject. So instead every harness is pointed at this
one process, and it:

1. **Pins the model.** Whatever ``model`` string a harness sends is ignored and
   replaced with ``--upstream``. A harness cannot silently evaluate a different
   model, and harnesses that only speak "openai" can drive Gemini or Kimi.
2. **Meters usage independently.** Each harness gets its own API key
   (``sk-harness-<name>``). Usage is attributed by key and written to a JSONL, so
   the cost comparison comes from one consistent meter rather than from each
   harness's self-report (several report nothing at all).
3. **Centralises rate-limit handling.** A global concurrency semaphore plus
   exponential backoff on 429/5xx, so a provider rate limit degrades into latency
   for every harness equally instead of failing whichever harness ran last.
4. **Normalises sampling.** ``temperature`` is forced to a single value for every
   harness so decoding randomness is not a free variable.

Run:  python scripts/gateway.py --upstream gemini/gemini-3.6-flash --port 4010
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

import litellm
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

litellm.drop_params = True  # silently drop params the upstream provider rejects
litellm.suppress_debug_info = True

# Params that are meaningful to forward upstream. Anything else a harness sends
# (harness-specific extensions, provider hints) is dropped so one harness cannot
# change upstream behaviour in a way another cannot.
_FORWARD = {
    "messages", "tools", "tool_choice", "max_tokens", "max_completion_tokens",
    "stop", "response_format", "parallel_tool_calls", "reasoning_effort", "seed",
}

STATE: dict[str, Any] = {}


def _log(record: dict[str, Any]) -> None:
    record["ts"] = time.time()
    with open(STATE["log_path"], "a") as fh:
        fh.write(json.dumps(record) + "\n")


def _charge(u: dict[str, int], harness: str = "") -> float:
    """Bill a call against the global and per-harness budgets; return its cost."""
    uncached = max(0, u["prompt_tokens"] - u["cached_tokens"])
    cost = (
        uncached * STATE["price_in"] / 1e6
        + u["cached_tokens"] * STATE["price_cached"] / 1e6
        + u["completion_tokens"] * STATE["price_out"] / 1e6
    )
    STATE["spent"] += cost
    if harness:
        STATE["spent_by_harness"][harness] = STATE["spent_by_harness"].get(harness, 0.0) + cost
    return cost


def _usage_fields(u: dict[str, Any]) -> dict[str, int]:
    """Flatten a usage block. Cached prompt tokens are billed at a ~10x discount
    on Moonshot, so they are metered separately -- otherwise a harness that
    re-sends a long prefix every step looks far more expensive than it is."""
    if not isinstance(u, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0}
    ptd = u.get("prompt_tokens_details") or {}
    ctd = u.get("completion_tokens_details") or {}
    cached = u.get("cached_tokens")
    if cached is None:
        cached = (ptd.get("cached_tokens") if isinstance(ptd, dict) else 0) or 0
    return {
        "prompt_tokens": u.get("prompt_tokens") or 0,
        "completion_tokens": u.get("completion_tokens") or 0,
        "cached_tokens": cached or 0,
        "reasoning_tokens": (ctd.get("reasoning_tokens") if isinstance(ctd, dict) else 0) or 0,
    }


def _harness_from_auth(request: Request) -> str:
    """Attribute the call to a harness via its dedicated API key."""
    auth = request.headers.get("authorization", "") or request.headers.get("x-api-key", "")
    token = auth.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if token.startswith("sk-harness-"):
        return token[len("sk-harness-"):]
    return "unknown"


app = FastAPI()


@app.get("/v1/models")
@app.get("/models")
async def models() -> dict[str, Any]:
    # Advertise both the pinned upstream and a generic alias; some CLIs validate
    # the model they were configured with against this list before running.
    ids = {STATE["upstream"].split("/", 1)[-1], "bench-model", STATE["alias"]}
    return {
        "object": "list",
        "data": [
            {"id": i, "object": "model", "owned_by": "harbor-harness-spread", "created": 0}
            for i in sorted(ids)
        ],
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "upstream": STATE["upstream"], "calls": STATE["calls"],
            "spent_usd": round(STATE["spent"], 4), "budget_usd": STATE["budget_usd"],
            "by_harness": {k: round(v, 4) for k, v in STATE["spent_by_harness"].items()}}


async def _call_upstream(kwargs: dict[str, Any], harness: str, stream: bool) -> Any:
    """Call the upstream model with bounded concurrency and backoff on 429/5xx."""
    attempts = STATE["max_retries"] + 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            async with STATE["sem"]:
                return await asyncio.wait_for(
                    litellm.acompletion(**kwargs, stream=stream, timeout=STATE["upstream_timeout"]),
                    timeout=STATE["upstream_timeout"] + 15,
                )
        except Exception as exc:  # noqa: BLE001 - provider errors are heterogeneous
            last_exc = exc
            msg = str(exc)
            retryable = isinstance(exc, asyncio.TimeoutError) or any(
                s in msg for s in ("429", "RESOURCE_EXHAUSTED", "rate limit", "RateLimit",
                                   "overloaded", "503", "502", "500", "UNAVAILABLE", "timeout")
            )
            if not retryable or attempt == attempts - 1:
                raise
            # Full jitter backoff; rate limits here are account-wide spend-rate
            # limits, so every harness must back off, not just this request.
            delay = min(STATE["backoff_cap"], (2**attempt) * STATE["backoff_base"])
            delay = random.uniform(0, delay)
            _log({"event": "retry", "harness": harness, "attempt": attempt,
                  "delay_s": round(delay, 2), "error": msg[:300]})
            await asyncio.sleep(delay)
    raise last_exc  # pragma: no cover


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    harness = _harness_from_auth(request)
    requested_model = body.get("model")
    stream = bool(body.get("stream", False))
    req_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    # Hard budget guard. The account funding this experiment has a fixed balance;
    # a single runaway harness looping on a tool error could drain it. Past the
    # cap every harness is refused equally, and the refusal is recorded so the
    # write-up can say exactly where the money ran out.
    if STATE["budget_usd"] and STATE["spent"] >= STATE["budget_usd"]:
        _log({"event": "budget_exhausted", "scope": "global", "harness": harness,
              "spent_usd": round(STATE["spent"], 4), "budget_usd": STATE["budget_usd"],
            "by_harness": {k: round(v, 4) for k, v in STATE["spent_by_harness"].items()}})
        return JSONResponse(
            {"error": {"message": f"gateway budget of ${STATE['budget_usd']} exhausted "
                                  f"(spent ${STATE['spent']:.2f})", "type": "budget_exhausted"}},
            status_code=402,
        )

    # Per-harness slice. Without this the harnesses that happen to run first can
    # eat the whole pot and the later ones score 0 for lack of money rather than
    # lack of ability -- which would look exactly like a harness spread and be
    # completely spurious. Equal slice per harness keeps ordering irrelevant.
    per = STATE["budget_per_harness_usd"]
    if per and STATE["spent_by_harness"].get(harness, 0.0) >= per:
        _log({"event": "budget_exhausted", "scope": "harness", "harness": harness,
              "spent_usd": round(STATE["spent_by_harness"].get(harness, 0.0), 4),
              "budget_usd": per})
        return JSONResponse(
            {"error": {"message": f"per-harness budget of ${per} exhausted for {harness}",
                       "type": "budget_exhausted"}},
            status_code=402,
        )

    kwargs: dict[str, Any] = {k: v for k, v in body.items() if k in _FORWARD}
    kwargs["model"] = STATE["upstream"]          # pin the model
    # Pin decoding randomness when a temperature is configured. A negative value
    # means "send nothing and let the provider default apply" -- needed because
    # Kimi K2.7 Code rejects any temperature except 1, while Gemini 3.x warns that
    # explicit temperature is deprecated. Either way it is identical for every
    # harness on that model, which is what the comparison requires.
    if STATE["temperature"] is not None and STATE["temperature"] >= 0:
        kwargs["temperature"] = STATE["temperature"]
    if STATE["max_tokens"] and not kwargs.get("max_tokens"):
        kwargs["max_tokens"] = STATE["max_tokens"]

    STATE["calls"] += 1

    base = {
        "event": "call", "id": req_id, "harness": harness, "task": STATE["task"],
        "requested_model": requested_model, "upstream": STATE["upstream"],
        "stream": stream, "n_messages": len(body.get("messages") or []),
        "n_tools": len(body.get("tools") or []),
    }

    try:
        if not stream:
            resp = await _call_upstream(kwargs, harness, stream=False)
            d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
            u = _usage_fields(d.get("usage") or {})
            _log({**base, "status": "ok", **u, "cost_usd": round(_charge(u, harness), 6),
                  "spent_usd": round(STATE["spent"], 4),
                  "latency_s": round(time.perf_counter() - started, 3)})
            return JSONResponse(d)

        # Streaming: force usage accounting on so streamed calls are metered too.
        kwargs["stream_options"] = {"include_usage": True}
        upstream_iter = await _call_upstream(kwargs, harness, stream=True)

        async def gen():
            # Usage is metered in a finally block, not at the end of the happy
            # path: harnesses like Aider hold a single stream open for minutes,
            # and if the trial is killed (task timeout, budget stop) the client
            # disconnect raises CancelledError -- a BaseException. Billing only
            # on clean completion would silently undercount exactly the
            # longest, most expensive calls.
            usage_seen: dict[str, int] = {}
            status, err = "ok", None
            try:
                async for chunk in upstream_iter:
                    d = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
                    u = d.get("usage") or {}
                    if u:
                        usage_seen = _usage_fields(u)
                    yield f"data: {json.dumps(d)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:  # noqa: BLE001
                status, err = "error", str(exc)[:500]
                yield f'data: {json.dumps({"error": {"message": str(exc)[:500]}})}\n\n'
                yield "data: [DONE]\n\n"
            except BaseException as exc:  # client disconnect / cancellation
                status, err = "aborted", type(exc).__name__
                raise
            finally:
                u = usage_seen or _usage_fields({})
                rec = {**base, "status": status, **u,
                       "cost_usd": round(_charge(u, harness), 6),
                       "spent_usd": round(STATE["spent"], 4),
                       "latency_s": round(time.perf_counter() - started, 3)}
                if err:
                    rec["error"] = err
                _log(rec)

        return StreamingResponse(gen(), media_type="text/event-stream")

    except Exception as exc:  # noqa: BLE001
        _log({**base, "status": "error", "error": str(exc)[:500],
              "latency_s": round(time.perf_counter() - started, 3)})
        return JSONResponse(
            {"error": {"message": str(exc)[:500], "type": "upstream_error"}}, status_code=502
        )


# ---------------------------------------------------------------------------
# OpenAI Responses API shim.
#
# OpenCode's bundled provider calls `.responses()` and cannot be talked out of it
# (pinning @ai-sdk/openai-compatible instead fails deeper, with
# "Z.responses is not a function"). Without this endpoint OpenCode simply cannot
# be evaluated. Implementing it here rather than pointing OpenCode at the real
# OpenAI API is the whole point: it keeps OpenCode on the SAME pinned model as
# every other harness, so it stays inside the like-for-like comparison instead of
# becoming a separate experiment where model and harness vary together.
#
# This translates Responses-shaped requests down to chat completions, and
# translates the reply back up. It implements the subset the harness actually
# uses: input as string or message list, instructions, tools, and usage.
# ---------------------------------------------------------------------------

def _responses_input_to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": str(body["instructions"])})
    raw = body.get("input")
    if isinstance(raw, str):
        msgs.append({"role": "user", "content": raw})
        return msgs
    for item in raw or []:
        if not isinstance(item, dict):
            msgs.append({"role": "user", "content": str(item)})
            continue
        itype = item.get("type")
        if itype == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": item.get("call_id") or item.get("id") or "",
                         "content": str(item.get("output", ""))})
            continue
        if itype == "function_call":
            msgs.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": item.get("call_id") or item.get("id") or "",
                "type": "function",
                "function": {"name": item.get("name", ""),
                             "arguments": item.get("arguments", "") or "{}"},
            }]})
            continue
        role = item.get("role", "user")
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    parts.append(c.get("text") or c.get("input_text") or "")
                else:
                    parts.append(str(c))
            content = "\n".join(x for x in parts if x)
        msgs.append({"role": role, "content": content if content is not None else ""})
    return msgs


def _responses_tools(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    tools = []
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and "function" in t:
            tools.append(t)
        elif t.get("type") == "function" or "name" in t:
            tools.append({"type": "function", "function": {
                "name": t.get("name", ""),
                "description": t.get("description", "") or "",
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            }})
    return tools or None


def _chat_to_responses(d: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (d.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    output: list[dict[str, Any]] = []
    text = msg.get("content")
    if text:
        output.append({"type": "message", "id": f"msg_{uuid.uuid4().hex[:16]}", "status": "completed",
                       "role": "assistant",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        output.append({"type": "function_call", "id": f"fc_{uuid.uuid4().hex[:16]}",
                       "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                       "name": fn.get("name", ""), "arguments": fn.get("arguments", "") or "{}",
                       "status": "completed"})
    u = d.get("usage") or {}
    return {
        "id": d.get("id") or f"resp_{uuid.uuid4().hex[:16]}",
        "object": "response", "created_at": d.get("created") or int(time.time()),
        "status": "completed", "model": model, "output": output,
        "output_text": text or "",
        "usage": {"input_tokens": u.get("prompt_tokens", 0),
                  "output_tokens": u.get("completion_tokens", 0),
                  "total_tokens": u.get("total_tokens", 0)},
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": body_tools_echo(d),
    }


def body_tools_echo(_d: dict[str, Any]) -> list[Any]:
    return []


@app.post("/v1/responses")
@app.post("/responses")
async def responses(request: Request) -> Any:
    body = await request.json()
    harness = _harness_from_auth(request)
    req_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    STATE["calls"] += 1

    base = {"event": "call", "id": req_id, "harness": harness, "task": STATE["task"],
            "requested_model": body.get("model"), "upstream": STATE["upstream"],
            "api": "responses", "stream": bool(body.get("stream")),
            "n_messages": 0, "n_tools": len(body.get("tools") or [])}

    if STATE["budget_usd"] and STATE["spent"] >= STATE["budget_usd"]:
        _log({"event": "budget_exhausted", "scope": "global", "harness": harness,
              "spent_usd": round(STATE["spent"], 4), "budget_usd": STATE["budget_usd"]})
        return JSONResponse({"error": {"message": "gateway budget exhausted",
                                       "type": "budget_exhausted"}}, status_code=402)

    kwargs: dict[str, Any] = {"model": STATE["upstream"],
                              "messages": _responses_input_to_messages(body)}
    base["n_messages"] = len(kwargs["messages"])
    tools = _responses_tools(body)
    if tools:
        kwargs["tools"] = tools
    if body.get("max_output_tokens"):
        kwargs["max_tokens"] = body["max_output_tokens"]
    if STATE["temperature"] is not None and STATE["temperature"] >= 0:
        kwargs["temperature"] = STATE["temperature"]

    try:
        resp = await _call_upstream(kwargs, harness, stream=False)
        d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        u = _usage_fields(d.get("usage") or {})
        _log({**base, "status": "ok", **u, "cost_usd": round(_charge(u, harness), 6),
              "spent_usd": round(STATE["spent"], 4),
              "latency_s": round(time.perf_counter() - started, 3)})
        out = _chat_to_responses(d, body.get("model") or STATE["alias"])
        if body.get("stream"):
            # Minimal SSE: emit the finished response as one completed event.
            async def gen():
                yield f"event: response.completed\ndata: {json.dumps({'type':'response.completed','response':out})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse(out)
    except Exception as exc:  # noqa: BLE001
        _log({**base, "status": "error", "error": str(exc)[:500],
              "latency_s": round(time.perf_counter() - started, 3)})
        return JSONResponse({"error": {"message": str(exc)[:500], "type": "upstream_error"}},
                            status_code=502)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True, help="litellm model id, e.g. gemini/gemini-3.6-flash")
    ap.add_argument("--alias", default="bench-model")
    ap.add_argument("--port", type=int, default=4010)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--log", default="gateway.jsonl")
    ap.add_argument("--max-concurrency", type=int, default=4)
    ap.add_argument("--max-retries", type=int, default=6)
    ap.add_argument("--backoff-base", type=float, default=2.0)
    ap.add_argument("--backoff-cap", type=float, default=60.0)
    ap.add_argument("--temperature", type=float, default=-1.0,
                    help="negative means do not send a temperature at all")
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--upstream-timeout", type=float, default=300.0,
                    help="per-request upstream timeout; a hung call otherwise stalls a whole trial")
    ap.add_argument("--budget-per-harness-usd", type=float, default=0.0,
                    help="equal spend slice per harness; 0 disables")
    ap.add_argument("--budget-usd", type=float, default=0.0,
                    help="hard spend cap; 0 disables. Calls past it get HTTP 402.")
    ap.add_argument("--price-in", type=float, default=0.95, help="USD per 1M uncached input tokens")
    ap.add_argument("--price-cached", type=float, default=0.19, help="USD per 1M cached input tokens")
    ap.add_argument("--price-out", type=float, default=4.00, help="USD per 1M output tokens")
    args = ap.parse_args()

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    STATE.update(
        upstream=args.upstream, alias=args.alias, log_path=args.log,
        sem=asyncio.Semaphore(args.max_concurrency), max_retries=args.max_retries,
        backoff_base=args.backoff_base, backoff_cap=args.backoff_cap,
        temperature=args.temperature, max_tokens=args.max_tokens,
        calls=0, task=os.environ.get("BENCH_TASK", ""),
        budget_usd=args.budget_usd, spent=0.0, upstream_timeout=args.upstream_timeout,
        budget_per_harness_usd=args.budget_per_harness_usd, spent_by_harness={},
        price_in=args.price_in, price_cached=args.price_cached, price_out=args.price_out,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
