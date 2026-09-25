import asyncio
import json
import time

import httpx
import tiktoken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pathlib import Path
from sse_starlette.sse import EventSourceResponse

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

        async def test_model(model_id: str):
            nonlocal done_count
            async with semaphore:
                payload = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                }
                if system:
                    payload["system"] = system
                resp_body = None
                prompt_tokens = None
                completion_tokens = None
                start = time.monotonic()
                try:
                    async with httpx.AsyncClient(timeout=None) as client:
                        resp = await client.post(
                            f"{endpoint}/v1/chat/completions",
                            headers={**headers, "Content-Type": "application/json"},
                            json=payload,
                        )
                        resp_body = resp.text[:200000]
                        latency = int((time.monotonic() - start) * 1000)
                        prompt_tokens = len(enc.encode(prompt))
                        data = None
                        try:
                            data, streamed = parse_completion_response(resp.text)
                        except Exception:
                            data, streamed = None, None
                        if data:
                            try:
                                usage = data.get("usage") or {}
                                if usage.get("prompt_tokens") is not None:
                                    prompt_tokens = usage["prompt_tokens"]
                                elif usage.get("input_tokens") is not None:
                                    prompt_tokens = usage["input_tokens"]
                                if usage.get("completion_tokens") is not None:
                                    completion_tokens = usage["completion_tokens"]
                                elif usage.get("output_tokens") is not None:
                                    completion_tokens = usage["output_tokens"]
                                else:
                                    content = streamed if streamed is not None else (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                                    completion_tokens = len(enc.encode(content))
                            except Exception:
                                pass
                        resp.raise_for_status()
                        done_count += 1
                        tps = round(completion_tokens / (latency / 1000), 1) if completion_tokens is not None and latency else None
                        log_call(model_id, payload, resp_body)
                        return {"type": "result", "model": model_id, "status": "active", "latency_ms": latency, "tokens_per_sec": tps, "error": None, "progress": f"{done_count}/{total}", "request": payload, "response": resp_body, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
                except Exception as e:
                    latency = int((time.monotonic() - start) * 1000)
                    done_count += 1
                    err_msg = str(e)[:200]
                    log_call(model_id, payload, resp_body, error=err_msg)
                    return {"type": "result", "model": model_id, "status": "inactive", "latency_ms": latency, "tokens_per_sec": None, "error": err_msg, "progress": f"{done_count}/{total}", "request": payload, "response": resp_body, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

        # Run tests concurrently, yield results as they complete
        tasks = {asyncio.create_task(test_model(m)): m for m in models}
        for coro in asyncio.as_completed(tasks):
            result = await coro
            yield {"event": "message", "data": __import__("json").dumps(result)}

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
