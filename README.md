# Model Checker & OpenCode Config Manager

*[Bahasa Indonesia](#model-checker--opencode-config-manager-1) | [English](#model-checker--opencode-config-manager-2)*

## Model Checker & OpenCode Config Manager (Indonesia)

Alat berbasis web yang ringan dan berjalan secara lokal, dirancang untuk memeriksa kompatibilitas dan status endpoint LLM yang kompatibel dengan OpenAI, serta menyinkronkan model yang aktif ke konfigurasi OpenCode (`~/.config/opencode/opencode.json`) dengan mudah.

### Fitur

- **Pengujian Endpoint Bersamaan**: Mengambil daftar model dari `/v1/models` standar dan menguji chat completion (`/v1/chat/completions`) secara bersamaan (hingga 5 model sekaligus).
- **Output Real-time via SSE**: Menampilkan progres, latency, dan status secara real-time.
- **Integrasi Konfigurasi OpenCode**:
  - Otomatis membaca dan mem-parsing `~/.config/opencode/opencode.json`.
  - Menampilkan daftar konfigurasi provider yang sudah ada.
  - Mendukung penambahan, penghapusan, dan sinkronisasi model yang sudah diuji ke provider tertentu (misalnya `9router`).
- **UI Web Interaktif**: Tampilan modern bertema gelap untuk menjalankan pengecekan, melihat payload respons, dan mengelola konfigurasi.

### Stack

- **Backend**: Python 3.10+, FastAPI, `httpx`, `sse-starlette`, `uvicorn`
- **Frontend**: HTML5, CSS3, JavaScript murni (klien berbasis SSE)

### Instalasi

1. **Pasang Dependensi**:

   Opsi A: menggunakan `pip`
   ```bash
   pip install -r requirements.txt
   ```

   Opsi B: menggunakan [`uv`](https://github.com/astral-sh/uv) (lebih cepat)
   ```bash
   uv venv
   uv pip install -r requirements.txt
   ```

2. **Jalankan Aplikasi**:
   ```bash
   uvicorn app:app --reload
   ```
   Atau, jika memakai `uv`:
   ```bash
   uv run uvicorn app:app --reload
   ```

3. **Buka Web UI**:
   Buka `http://127.0.0.1:8000` di browser.

### Referensi API

#### Endpoint Utama

##### `GET /`
Menyajikan tampilan HTML frontend.

##### `POST /api/check`
Memicu pengecekan model untuk sebuah endpoint.
- **Request Body**:
  ```json
  {
    "endpoint": "https://api.example.com",
    "api_key": "sk-...",
    "prompt": "hi",
    "max_tokens": 10
  }
  ```
- **Response**: Stream Server-Sent Events (SSE) berisi progres dan hasil pengujian.

#### Endpoint Konfigurasi

##### `GET /api/config`
Mengambil daftar provider dan endpoint yang terkonfigurasi dari file konfigurasi OpenCode.

##### `POST /api/config/models`
Menambah atau menghapus satu model ID dari provider tertentu.
- **Request Body**:
  ```json
  {
    "action": "add" | "remove",
    "model_id": "model-name",
    "provider": "9router"
  }
  ```

##### `POST /api/config/sync`
Menyinkronkan model aktif dan/atau tidak aktif secara massal ke konfigurasi OpenCode.
- **Request Body**:
  ```json
  {
    "active": ["model-a", "model-b"],
    "inactive": ["model-c"],
    "provider": "9router",
    "mode": "merge" | "replace"
  }
  ```

---

## Model Checker & OpenCode Config Manager (English)

A lightweight, local web-based tool designed to check compatibility and status of OpenAI-compatible LLM endpoints, and seamlessly sync active models into your OpenCode (`~/.config/opencode/opencode.json`) configuration.

### Features

- **Concurrent Endpoint Testing**: Fetches models from standard `/v1/models` and tests chat completion (`/v1/chat/completions`) concurrently (up to 5 models at a time).
- **SSE Real-time Output**: Streams progress, latency, and status in real-time.
- **OpenCode Config Integration**:
  - Automatically reads and parses `~/.config/opencode/opencode.json`.
  - Lists existing provider configurations.
  - Allows adding, deleting, and syncing tested models back to specific providers (e.g., `9router`).
- **Interactive Web UI**: Modern, dark-themed GUI for launching checks, viewing response payloads, and managing configurations.

### Stack

- **Backend**: Python 3.10+, FastAPI, `httpx`, `sse-starlette`, `uvicorn`
- **Frontend**: Vanilla HTML5, CSS3, JavaScript (SSE-based client)

### Setup

1. **Install Dependencies**:

   Option A: using `pip`
   ```bash
   pip install -r requirements.txt
   ```

   Option B: using [`uv`](https://github.com/astral-sh/uv) (faster)
   ```bash
   uv venv
   uv pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   uvicorn app:app --reload
   ```
   Or, if using `uv`:
   ```bash
   uv run uvicorn app:app --reload
   ```

3. **Open the Web UI**:
   Navigate to `http://127.0.0.1:8000` in your browser.

### API Reference

#### Core Endpoints

##### `GET /`
Serves the HTML frontend interface.

##### `POST /api/check`
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

#### Config Endpoints

##### `GET /api/config`
Retrieves providers and configured endpoints from the OpenCode config file.

##### `POST /api/config/models`
Adds or removes a single model ID from a specific provider.
- **Request Body**:
  ```json
  {
    "action": "add" | "remove",
    "model_id": "model-name",
    "provider": "9router"
  }
  ```

##### `POST /api/config/sync`
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
