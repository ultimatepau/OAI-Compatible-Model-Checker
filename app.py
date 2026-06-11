import asyncio
import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pathlib import Path
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

HTML_PATH = Path(__file__).parent / "index.html"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PATH.read_text(encoding="utf-8")


@app.post("/api/check")
async def check_models(request: Request):
    body = await request.json()
    endpoint = body["endpoint"].rstrip("/")
    api_key = body.get("api_key", "")
    prompt = body.get("prompt", "hi")
    max_tokens = body.get("max_tokens", 10)

    headers = {"Authorization": f"Bearer {api_key}"}
    semaphore = asyncio.Semaphore(5)

    async def event_generator():
        # 1. Fetch model list
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(f"{endpoint}/v1/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                yield {"event": "message", "data": f'{{"type":"error","error":"Failed to fetch models: {str(e)}"}}'}
                return

        models = [m["id"] for m in data.get("data", [])]
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
                resp_body = None
                start = time.monotonic()
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.post(
                            f"{endpoint}/v1/chat/completions",
                            headers={**headers, "Content-Type": "application/json"},
                            json=payload,
                        )
                        resp_body = resp.text[:2000]
                        latency = int((time.monotonic() - start) * 1000)
                        resp.raise_for_status()
                        done_count += 1
                        return {"type": "result", "model": model_id, "status": "active", "latency_ms": latency, "error": None, "progress": f"{done_count}/{total}", "request": payload, "response": resp_body}
                except Exception as e:
                    latency = int((time.monotonic() - start) * 1000)
                    done_count += 1
                    err_msg = str(e)[:200]
                    return {"type": "result", "model": model_id, "status": "inactive", "latency_ms": latency, "error": err_msg, "progress": f"{done_count}/{total}", "request": payload, "response": resp_body}

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
    """Sync active models from a check into the opencode config (add active, remove inactive)."""
    body = await request.json()
    active_models = body.get("active", [])
    inactive_models = body.get("inactive", [])
    provider = body.get("provider", "9router")
    mode = body.get("mode", "merge")  # "merge" = add active only, "replace" = set to exactly active list

    try:
        config = read_opencode_config()
        models = config.setdefault("provider", {}).setdefault(provider, {}).setdefault("models", {})

        added = []
        removed = []

        if mode == "replace":
            for mid in list(models.keys()):
                if mid not in active_models:
                    del models[mid]
                    removed.append(mid)

        for mid in active_models:
            if mid not in models:
                models[mid] = {"name": mid}
                added.append(mid)

        write_opencode_config(config)
        return {"ok": True, "added": added, "removed": removed, "total": len(models)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
