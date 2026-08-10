# HFDM

HFDM is a LAN-first Hugging Face model download manager. It provides a React/shadcn web interface backed by FastAPI, `huggingface_hub`, SQLite, and isolated per-file download workers.

## V1 capabilities

- Accepts `owner/repo`, model URLs, and `/tree/<revision>` URLs.
- Shows a searchable, selectable repository file tree with byte totals.
- Downloads selected files while preserving the repository structure.
- Keeps durable task and file state in SQLite.
- Supports queueing, progress, pause, resume, cancel, retry, and service restart recovery.
- Terminates active file workers when pausing; incomplete Hugging Face data is reused when possible.
- Uses a task-scoped HF token in memory only. Tokens are never written to SQLite or the Hugging Face login cache.
- Shares completed public, gated, and private downloads with the trusted LAN.
- Deduplicates active work by repository, commit, and file path.
- Serves completed files with HTTP Range support for browser resume.
- Restricts task control, deletion, and settings changes to loopback administrators.
- Supports global capacity, free-space reserve, retention, and physical-delete policy settings.

## Requirements

- Python 3.12 embedded distribution in `python_embed` for the portable folder
- Bun only when rebuilding the frontend
- Network access to Hugging Face

## Portable embedded-Python layout

Copy the complete HFDM folder, then run `start_service.bat` from its new location.
The launcher always uses `python_embed\python.exe` and derives every path from the
batch file's location. It never selects a machine-wide Python installation.

HFDM itself is loaded directly from `app\src`, so do not run `pip install .` or
`pip install -e .` for the application. Only third-party dependencies belong in the
embedded interpreter:

```text
python_embed\python.exe -m pip install -r requirements.txt
```

If pip or the bundled dependencies need repair, run `fix_pip.bat`.

`python_embed`, runtime data, frontend dependencies, and local tools are deliberately
excluded from Git. They belong to a machine-local portable folder, not the source
history. The built `app\frontend\dist` is versioned because HFDM serves it directly.

For a non-starting verification that does not keep the server running:

```text
start_service.bat --check
```

## Setup

The portable folder contains its runtime dependencies and built web interface. Bun
is a local tool and is not versioned; place `bun.exe` in `tools`, or use a Bun
installation available on `PATH`.

To install development-only Python test dependencies:

```powershell
.\python_embed\python.exe -m pip install -r .\requirements-dev.txt
```

## Run

At the top of `start_service.bat`, edit the following settings when needed:

```bat
set "HFDM_HOST=0.0.0.0"
set "HFDM_PORT=8765"
set "HFDM_OPEN_BROWSER=1"
set "HFDM_BROWSER_URL="
```

- Use `0.0.0.0` for trusted-LAN access or `127.0.0.1` for local-only access.
- `HFDM_OPEN_BROWSER=1` opens the local page only after Uvicorn reports ready.
- Leave `HFDM_BROWSER_URL` empty to derive `http://127.0.0.1:<port>` when listening
  on all interfaces. Set it explicitly if a hostname or different page is preferred.

Then run:

```text
start_service.bat
```

Or start the service directly:

```powershell
.\python_embed\python.exe .\app\app.py
```

Open the configured local URL. Other trusted LAN users can connect with the server's
LAN address and configured port when `HFDM_HOST=0.0.0.0`.

The local connection is the administrator. LAN connections are visitors: they can resolve repositories, create tasks, view progress, and download completed files, but cannot pause, cancel, delete, or change settings.

## Development

Run the API and Vite separately:

```powershell
.\python_embed\python.exe .\app\app.py
.\tools\bun.exe run --cwd .\app\frontend dev
```

Run verification:

```powershell
.\python_embed\python.exe -m pytest .\app\tests
.\tools\bun.exe run --cwd .\app\frontend build
```

## Data layout

```text
data/hfdm.sqlite3
download/models/<owner>/<repo>/<commit>/<repo files>
```

Both directories are runtime state and are ignored by Git. Task destinations are
stored relative to `download/`, so copying `data/` and `download/` together keeps
completed files usable at the new location.

## Security boundary

V1 is for a trusted LAN and does not provide login or HTTPS. A user-supplied HF token crosses the LAN over plain HTTP, is kept only in process memory, and is discarded after completion or cancellation. If the service restarts, authenticated unfinished tasks enter `auth_required` and need a token again.

Do not expose this version directly to the public internet.
