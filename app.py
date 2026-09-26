import asyncio
import json
import time

import httpx
import tiktoken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pathlib import Path
from sse_starlette.sse import EventSourceResponse

import db

app = FastAPI()

enc = tiktoken.get_encoding("cl100k_base")

HTML_PATH = Path(__file__).parent / "index.html"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
LOG_FILE = Path(__file__).parent / "checker.log"


def log_call(model, request_payload, response_text, error=None):
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "request": request_payload,
        "response": response_text,
        "error": error,
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_completion_response(raw: str):
    """Parse a chat completion response that may be plain JSON or an SSE stream.

    Returns (data, content): the last decoded JSON chunk (or full body) and the
    concatenated streamed text (None when the response was not streamed).
    """
    try:
        return json.loads(raw), None
    except Exception:
        pass
    # Some proxies send plain JSON and glue "data: [DONE]" on the same line.
    lead = raw.lstrip()
    if lead.startswith("{"):
        try:
            return json.JSONDecoder().raw_decode(lead)[0], None
        except Exception:
            pass
    data = None
    parts = []
    usage = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except Exception:
            continue
        data = chunk
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if choices:
            piece = (choices[0].get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
        else:
            ctype = chunk.get("type")
            delta = chunk.get("delta") or {}
            if ctype == "content_block_delta" and delta.get("type") == "text_delta":
                piece = delta.get("text")
                if piece:
                    parts.append(piece)
            elif ctype == "message_delta" and chunk.get("usage"):
                usage = chunk["usage"]
    content = "".join(parts) or None
    if usage and data is not None:
        data = {**data, "usage": usage}
    return data, content

def _num(x):
    """Numbers only: upstream usage values are untrusted (a string here would reach the UI)."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _text_of(data):
    """Reply text from a plain (non-SSE) OpenAI or Anthropic completion body."""
    if not isinstance(data, dict):
        return None
    choice = (data.get("choices") or [{}])[0]
    text = ((choice if isinstance(choice, dict) else {}).get("message") or {}).get("content")
    if isinstance(text, str) and text:
        return text
    blocks = data.get("content")
    if isinstance(blocks, list):
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict)) or None
    return None


def compute_tps(tokens, latency_ms, ttft_ms, streamed, floor_ms=50):
    """Tokens/sec over the generation window; whole latency when not streamed or the window is degenerate."""
    if tokens is None:
        return None
    latency_ms = max(latency_ms or 0, 1)
    gen_ms = latency_ms - (ttft_ms or 0) if streamed else latency_ms
    if gen_ms < floor_ms:  # proxy buffered the stream / endpoint ignored it
        gen_ms = latency_ms
    return round(tokens / (gen_ms / 1000), 1)


TIMEOUT = httpx.Timeout(10.0, read=60.0)  # read = max silence between chunks


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PATH.read_text(encoding="utf-8")


async def fetch_models(endpoint: str, api_key: str) -> list[str]:
    """Fetch the list of model ids from an OpenAI-compatible endpoint."""
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.get(f"{endpoint}/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return [m["id"] for m in data.get("data", [])]


@app.post("/api/models")
async def list_models(request: Request):
    body = await request.json()
    endpoint = body["endpoint"].rstrip("/")
    api_key = body.get("api_key", "")

    try:
        models = await fetch_models(endpoint, api_key)
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "error": f"Failed to fetch models: {str(e)}"}


@app.post("/api/check")
async def check_models(request: Request):
    body = await request.json()
    endpoint = body["endpoint"].rstrip("/")
    api_key = body.get("api_key", "")
    prompt = body.get("prompt", "hi")
    max_tokens = body.get("max_tokens", 10)
    system = body.get("system", "")
    selected_models = body.get("models")
    try:
        runs = max(1, min(int(body.get("runs", 1) or 1), 5))
    except (TypeError, ValueError):
        runs = 1

    headers = {"Authorization": f"Bearer {api_key}"}
    semaphore = asyncio.Semaphore(5)

    async def event_generator():
        # 1. Use the caller's model selection, or fetch the full list
        if selected_models:
            models = selected_models
        else:
            try:
                models = await fetch_models(endpoint, api_key)
            except Exception as e:
                yield {"event": "message", "data": f'{{"type":"error","error":"Failed to fetch models: {str(e)}"}}'}
                return

        yield {"event": "message", "data": f'{{"type":"models","data":{__import__("json").dumps(models)}}}'}

        # 2. Test each model
        total = len(models)
        done_count = 0
        all_results = []

        async def _run_model(model_id: str):
            async with semaphore:
                probes = body.get("capabilities") or []
                attempts = []
                payload = {}
                for _ in range(runs):
                    payload = {
                        "model": model_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                    if system:
                        payload["system"] = system
                    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                        r = await stream_completion(
                            client, f"{endpoint}/v1/chat/completions", headers, payload)
                        if not r["ok"] and r["retry_stream"]:
                            plain = {k: v for k, v in payload.items()
                                     if k not in ("stream", "stream_options")}
                            r = await stream_completion(
                                client, f"{endpoint}/v1/chat/completions", headers, plain)
                    attempts.append(r)

                ok_runs = [r for r in attempts if r["ok"]]
                status = "active" if ok_runs else "inactive"
                flaky = 0 < len(ok_runs) < runs
                lats = sorted(r["latency_ms"] for r in ok_runs)
                lat = lats[len(lats) // 2] if lats else (attempts[-1]["latency_ms"] if attempts else 0)
                usage = ok_runs[-1]["usage"] if ok_runs else {}
                prompt_tokens = _num(usage.get("prompt_tokens"))
                if prompt_tokens is None:
                    prompt_tokens = _num(usage.get("input_tokens"))
                if prompt_tokens is None:
                    prompt_tokens = len(enc.encode(prompt))
                content = ok_runs[-1]["content"] if ok_runs else None
                completion_tokens = _num(usage.get("completion_tokens"))
                if completion_tokens is None:
                    completion_tokens = _num(usage.get("output_tokens"))
                if completion_tokens is None and content:
                    completion_tokens = len(enc.encode(content))
                ttft = ok_runs[-1]["ttft_ms"] if ok_runs else None
                if ttft is None:
                    ttft = lat
                tps = None
                if ok_runs:
                    last = ok_runs[-1]
                    tps = compute_tps(completion_tokens, last["latency_ms"], last["ttft_ms"], last["streamed"])
                reasoning = any(r["reasoning"] for r in ok_runs)
                caps = {}
                if reasoning:
                    caps["reasoning"] = True
                probe_details = {}

                def log_probe(kind, probe_payload, text, reason):
                    try:
                        log_call(f"{model_id} [probe:{kind}]", probe_payload, text, error=reason)
                    except Exception:
                        pass
                if probes:
                    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                        caps.update(await run_probes(
                            client, f"{endpoint}/v1/chat/completions", headers, model_id, probes,
                            probe_details, log_probe))
                error = None if ok_runs else (attempts[-1]["error"] if attempts else "no attempts")
                response = content if ok_runs else (attempts[-1].get("body") or error)
                try:
                    log_call(model_id, payload, response or "", error=error)
                except Exception:
                    pass  # a full disk must not take the check down
                return {
                    "type": "result", "model": model_id, "status": status,
                    "flaky": flaky, "runs_ok": len(ok_runs), "runs_total": runs,
                    "latency_ms": lat, "ttft_ms": ttft, "tokens_per_sec": tps,
                    "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                    "capabilities": caps, "probe_details": probe_details, "error": error,
                    "request": {"model": model_id, "messages": payload["messages"],
                                "max_tokens": max_tokens},
                    "response": response,
                }

        async def test_model(model_id: str):
            nonlocal done_count
            try:
                result = await _run_model(model_id)
            except Exception as e:  # one bad model must not kill the whole stream
                result = {"type": "result", "model": model_id, "status": "inactive",
                          "flaky": False, "runs_ok": 0, "runs_total": runs, "latency_ms": 0,
                          "ttft_ms": None, "tokens_per_sec": None, "prompt_tokens": None,
                          "completion_tokens": None, "capabilities": {},
                          "error": f"internal error: {str(e)[:150]}", "request": None,
                          "response": None}
            done_count += 1
            result["progress"] = f"{done_count}/{total}"
            return result

        # Run tests concurrently, yield results as they complete
        tasks = {asyncio.create_task(test_model(m)): m for m in models}
        for coro in asyncio.as_completed(tasks):
            result = await coro
            all_results.append(result)
            yield {"event": "message", "data": __import__("json").dumps(result)}

        try:
            db.record_run(endpoint, prompt, max_tokens, all_results)
        except Exception:
            pass

        yield {"event": "message", "data": '{"type":"done"}'}

    return EventSourceResponse(event_generator())


# ── OpenCode Config Management ──────────────────────────────────────────

def read_opencode_config() -> dict:
    return json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))


def write_opencode_config(config: dict):
    OPENCODE_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@app.get("/api/config")
async def get_config():
    try:
        config = read_opencode_config()
        providers = config.get("provider", {})
        # Build a list of {name, baseURL} for providers that have a baseURL
        endpoints = []
        for name, pconf in providers.items():
            base = pconf.get("options", {}).get("baseURL", "")
            endpoints.append({"name": name, "baseURL": base})
        return {"ok": True, "config": config, "providers": list(providers.keys()), "endpoints": endpoints}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/config/models")
async def update_config_models(request: Request):
    """Add or remove models from the opencode config's 9router provider."""
    body = await request.json()
    action = body["action"]  # "add" or "remove"
    model_id = body["model_id"]
    provider = body.get("provider", "9router")

    try:
        config = read_opencode_config()
        models = config.setdefault("provider", {}).setdefault(provider, {}).setdefault("models", {})

        if action == "add":
            models[model_id] = {"name": model_id}
            msg = f"Added {model_id}"
        elif action == "remove":
            if model_id in models:
                del models[model_id]
                msg = f"Removed {model_id}"
            else:
                return {"ok": False, "error": f"Model {model_id} not found"}
        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

        write_opencode_config(config)
        return {"ok": True, "message": msg}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/config/sync")
async def sync_models_to_config(request: Request):
    """Sync models from a check into the opencode config."""
    body = await request.json()
    provider = body.get("provider", "9router")
    mode = body.get("mode", "merge")

    try:
        config = read_opencode_config()
        models = config.setdefault("provider", {}).setdefault(provider, {}).setdefault("models", {})

        added = []
        removed = []

        if mode == "all":
            endpoint = body.get("endpoint", "").rstrip("/")
            api_key = body.get("api_key", "")
            
            if not endpoint:
                return {"ok": False, "error": "Endpoint URL is required for 'all' mode"}

            try:
                all_models = await fetch_models(endpoint, api_key)
            except Exception as e:
                return {"ok": False, "error": f"Failed to fetch models: {str(e)}"}

            for mid in all_models:
                if mid not in models:
                    models[mid] = {"name": mid}
                    added.append(mid)
        else:
            active_models = body.get("active", [])
            inactive_models = body.get("inactive", [])

            if mode == "replace":
                for mid in list(models.keys()):
                    if mid not in active_models:
                        del models[mid]
                        removed.append(mid)

            models_to_add = active_models
            for mid in models_to_add:
                if mid not in models:
                    models[mid] = {"name": mid}
                    added.append(mid)

        write_opencode_config(config)
        return {"ok": True, "added": added, "removed": removed, "total": len(models)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Streaming helper ─────────────────────────────────────────────────────

async def stream_completion(client, url, headers, payload):
    """POST streaming chat completion, ukur TTFT. Fallback non-stream bila endpoint tolak stream.

    Return dict: ok, status, ttft_ms, latency_ms, content, usage, streamed,
    reasoning, error, retry_stream.
    """
    start = time.monotonic()
    ttft = None
    parts = []
    usage = {}
    reasoning = False
    data = None
    raw = ""
    got_sse = False
    err = None
    stream_req = bool(payload.get("stream"))
    try:
        async with client.stream(
            "POST", url,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:200000]
                retry = stream_req and (resp.status_code == 400 or "stream" in body.lower())
                return {"ok": False, "status": resp.status_code,
                        "latency_ms": int((time.monotonic() - start) * 1000),
                        "ttft_ms": None, "content": None, "usage": {},
                        "streamed": False, "reasoning": False,
                        "error": body[:200], "body": body, "retry_stream": retry}
            buf = ""
            async for chunk in resp.aiter_text():
                raw = (raw + chunk)[:200000]
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    got_sse = True
                    s = line[5:].strip()
                    if not s or s == "[DONE]":
                        continue
                    try:
                        cj = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(cj, dict):
                        continue
                    data = cj
                    if cj.get("error"):
                        err = cj["error"]
                    if cj.get("usage"):
                        usage = {**usage, **cj["usage"]}
                    ch = cj.get("choices") or []
                    if ch:
                        d = ch[0].get("delta") or {}
                        if d.get("reasoning_content"):
                            reasoning = True
                        if d.get("content"):
                            if ttft is None:
                                ttft = (time.monotonic() - start) * 1000
                            parts.append(d["content"])
                    else:
                        d = cj.get("delta") or {}
                        if cj.get("type") == "content_block_delta":
                            if d.get("type") == "thinking_delta":
                                reasoning = True
                            if d.get("text"):
                                if ttft is None:
                                    ttft = (time.monotonic() - start) * 1000
                                parts.append(d["text"])
                        elif cj.get("type") == "message_delta" and cj.get("usage"):
                            usage = {**usage, **cj["usage"]}
        latency = int((time.monotonic() - start) * 1000)
        content = "".join(parts) or None
        if not got_sse:
            # Endpoint abaikan stream param dan balas JSON polos.
            data, content = parse_completion_response(raw)
            content = content or _text_of(data)
            ttft = latency
            if isinstance(data, dict):
                err = data.get("error")
                if data.get("usage"):
                    usage = {**usage, **data["usage"]}
        if err:
            msg = err.get("message") if isinstance(err, dict) else err
            return {"ok": False, "status": 200, "latency_ms": latency, "ttft_ms": None,
                    "content": None, "usage": {}, "streamed": got_sse, "reasoning": False,
                    "error": str(msg or err)[:200], "body": raw, "retry_stream": False}
        return {"ok": True, "status": 200, "latency_ms": latency, "ttft_ms": ttft,
                "content": content, "usage": usage, "streamed": got_sse,
                "reasoning": reasoning, "error": None, "retry_stream": False}
    except Exception as e:
        return {"ok": False, "status": None,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "ttft_ms": None, "content": None, "usage": {},
                "streamed": False, "reasoning": False,
                "error": str(e)[:200], "retry_stream": False}


TINY_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _judge_capability(kind, status, data, content):
    if status >= 400 or not data:
        return False
    if kind == "tools":
        msg = (data.get("choices") or [{}])[0].get("message", {})
        if msg.get("tool_calls"):
            return True
        blocks = data.get("content") or []
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks)
    if kind == "json":
        if content is None:  # plain (non-SSE) body: parse_completion_response gives no text
            content = _text_of(data)
        try:
            json.loads(content or "")
            return True
        except Exception:
            return False
    return True  # vision: cukup HTTP 200


def _probe_reason(kind, status, text, data):
    if status >= 400:
        return f"HTTP {status}: {text[:200]}"
    if not data:
        return "empty or unparseable response body"
    if kind == "tools":
        return "reply contained no tool call"
    if kind == "json":
        return "reply is not valid JSON"
    return "unexpected response"


async def run_probes(client, url, headers, model_id, kinds, details=None, log=None):
    """Return {kind: bool}. Optionally fill details[kind] = {status, reason, body} and call
    log(kind, payload, body, reason) so a red capability can be explained afterwards."""
    base = {"model": model_id, "max_tokens": 100}
    jobs = {
        "tools": {**base,
                  "messages": [{"role": "user",
                                "content": "What is the weather in Jakarta? Use the get_weather tool."}],
                  "tools": [{"type": "function",
                             "function": {"name": "get_weather",
                                          "description": "Get current weather for a city",
                                          "parameters": {"type": "object",
                                                         "properties": {"city": {"type": "string"}},
                                                         "required": ["city"]}}}]},
        "json": {**base,
                 "messages": [{"role": "user",
                               "content": 'Return exactly {"ok": true} as JSON.'}],
                 "response_format": {"type": "json_object"}},
        "vision": {**base, "max_tokens": 20,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": "Describe this image in one word."},
                       {"type": "image_url", "image_url": {"url": TINY_PNG}}]}]},
    }
    out = {}
    for kind in kinds:
        payload = jobs.get(kind)
        if not payload:
            continue
        status, text, reason, ok = None, "", None, False
        try:
            resp = await client.post(url, headers={**headers, "Content-Type": "application/json"},
                                     json=payload)
            status, text = resp.status_code, resp.text[:2000]
            data, content = parse_completion_response(resp.text)
            ok = _judge_capability(kind, status, data, content)
            if not ok:
                reason = _probe_reason(kind, status, text, data)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:300]
        out[kind] = ok
        if details is not None:
            details[kind] = {"status": status, "reason": reason, "body": text}
        if log:
            log(kind, payload, text, reason)
    return out


# ── History API ──────────────────────────────────────────────────────────

@app.get("/api/history")
async def api_history(limit: int = 20):
    return {"ok": True, "runs": db.list_runs(limit)}


@app.get("/api/history/{run_id}")
async def api_history_run(run_id: int):
    run, rows = db.get_run(run_id)
    if run is None:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "run": run, "results": rows}


@app.delete("/api/history/{run_id}")
async def api_history_delete(run_id: int):
    db.delete_run(run_id)
    return {"ok": True}
