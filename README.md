# Model Checker & OpenCode Config Manager

A lightweight, local web-based tool designed to check compatibility and status of OpenAI-compatible LLM endpoints, and seamlessly sync active models into your OpenCode (`~/.config/opencode/opencode.json`) configuration.

## Features

- **Concurrent Endpoint Testing**: Fetches models from standard `/v1/models` and tests chat completion (`/v1/chat/completions`) concurrently.
- **SSE Real-time Output**: Streams progress, latency, and status in real-time.
- **OpenCode Config Integration**:
  - Automatically reads and parses `~/.config/opencode/opencode.json`.
  - Lists existing provider configurations.
  - Allows adding, deleting, and syncing tested models back to specific providers (e.g., `9router`).
- **Interactive Web UI**: Modern, dark-themed GUI for launching checks, viewing response payloads, and managing configurations.

## Stack

- **Backend**: Python 3.10+, FastAPI, `httpx`, `sse-starlette`, `uvicorn`
- **Frontend**: Vanilla HTML5, CSS3, JavaScript (SSE-based client)

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   uvicorn app:app --reload
   ```

3. **Open the Web UI**:
   Navigate to `http://127.0.0.1:8000` in your browser.

## API Reference

### Core Endpoints

#### `GET /`
Serves the HTML frontend interface.

#### `POST /api/check`
Triggers checking of models for an endpoint.
- **Request Body**:
  ```json
  {
    "endpoint": "https://api.example.com",
    "api_key": "sk-...",
    "prompt": "hi",
    "max_tokens": 10
  }
  ```
- **Response**: Server-Sent Events (SSE) stream of progress and test results.

### Config Endpoints

#### `GET /api/config`
Retrieves providers and configured endpoints from the OpenCode config file.

#### `POST /api/config/models`
Adds or removes a single model ID from a specific provider.
- **Request Body**:
  ```json
  {
    "action": "add" | "remove",
    "model_id": "model-name",
    "provider": "9router"
  }
  ```

#### `POST /api/config/sync`
Batch syncs active and/or inactive models into OpenCode config.
- **Request Body**:
  ```json
  {
    "active": ["model-a", "model-b"],
    "inactive": ["model-c"],
    "provider": "9router",
    "mode": "merge" | "replace"
  }
  ```
