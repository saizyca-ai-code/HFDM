# HFDM

HFDM is a LAN-first Hugging Face Model and Dataset download manager. It provides a React/shadcn web interface backed by FastAPI, `huggingface_hub`, SQLite, and isolated per-file download workers.

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

## V2 capabilities under development

- Accepts Hugging Face Model and Dataset URLs, including `/tree/<revision>` references.
- Keeps Model and Dataset identity separate and stores them under `download/models/` and `download/datasets/`.
- Previews Dataset selections with comma- or newline-separated include/exclude glob patterns.
- Separates immutable transfer history from reconciled local availability and supports restoring moved or missing files.
- Includes an opt-in Model/Dataset benchmark harness under `app/tests/benchmarks/` for balanced, maximum, and HDD Xet profiles.
- Resolves Civitai model/version URLs and IDs, previews versions and file variants,
  and stores downloads by stable model/version identity.
- Keeps Hugging Face and Civitai URL workflows separate in the navigation; Civitai
  versions, precision variants, example images, and available generation metadata
  are selectable without a separate search index.
- Supports resumable segmented Civitai transfers with redirect refresh and SHA256
  verification; API tokens remain memory-only.
- Preserves Civitai model type, base model, tags, and per-file roles. The content
  library can search and filter this metadata and recommends the matching
  `ComfyUI/models/` folder for each downloaded model file.
- Keeps the resolved source, version, token, and file selection in memory after
  creating a task by default; optional independent preferences can clear the
  draft or open Transfers after creation.

## Requirements

- A Windows embedded Python distribution in `python_embed` for development
- Bun only when rebuilding the frontend
- Network access when resolving or installing dependencies and downloading from Hugging Face

## Portable development layout

```text
HFDM/
├─ app/                 source and built frontend
├─ python_embed/        interpreter and Python packaging tools
├─ packages/            replaceable third-party development dependencies
├─ data/                SQLite runtime state
├─ download/            completed repository files
├─ scripts/             tracked runtime/build implementation
├─ manage_runtime.bat
├─ build_portable.bat
└─ start_service.bat
```

The launcher always uses `python_embed\python.exe` and derives paths from the batch
file location. HFDM itself is loaded directly from `app\src`; do not install HFDM
into the interpreter. Third-party application and test dependencies are isolated in
`packages`, while pip/setuptools may remain inside `python_embed` as development
tools.

`python_embed`, `packages`, runtime data, frontend dependencies, local tools, and
portable build output are excluded from Git. The built `app\frontend\dist` is
versioned because the service serves it directly.

## Runtime management

After adding or changing a direct dependency in `requirements.txt` or
`requirements-dev.txt`, resolve fresh lock files and rebuild development packages:

```text
manage_runtime.bat lock
manage_runtime.bat dev
manage_runtime.bat check
```

For a temporary development installation or update:

```text
manage_runtime.bat pip PACKAGE
```

Record intentional dependencies in the appropriate requirements file and regenerate
the locks before release. When replacing the embedded Python version, replace
`python_embed`, ensure pip is available, then run `manage_runtime.bat dev`; the
manager regenerates the version-specific `pythonXY._pth` search paths.

`fix_pip.bat` repairs pip/setuptools/wheel inside the development interpreter and
then rebuilds `packages` from the development lock.

## Portable release build

`build_portable.bat` creates a clean release from interpreter core files, the runtime
lock, application source, and built frontend. It does not copy development
site-packages or test dependencies.

```text
build_portable.bat
```

Outputs:

```text
build/HFDM-portable/
build/HFDM-<version>-windows-x64.zip
```

The build runs `start_service.bat --check` inside staging before publishing the
folder and ZIP. For a non-starting check of the development folder:

```text
start_service.bat --check
```

For frontend development, place `bun.exe` in `tools`, or use Bun from `PATH`.

## Run

At the top of `start_service.bat`, edit the following settings when needed:

```bat
set "HFDM_DEFAULT_HOST=0.0.0.0"
set "HFDM_DEFAULT_PORT=8765"
set "HFDM_DEFAULT_OPEN_BROWSER=1"
set "HFDM_DEFAULT_BROWSER_URL="
```

- Use `0.0.0.0` for trusted-LAN access or `127.0.0.1` for local-only access.
- `HFDM_OPEN_BROWSER=1` opens the local page only after Uvicorn reports ready.
- Leave `HFDM_BROWSER_URL` empty to derive `http://127.0.0.1:<port>` when listening
  on all interfaces. Set it explicitly if a hostname or different page is preferred.
- Existing `HFDM_HOST`, `HFDM_PORT`, `HFDM_OPEN_BROWSER`, and `HFDM_BROWSER_URL`
  environment variables override these defaults for automation and verification.

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
.\manage_runtime.bat check
.\tools\bun.exe run --cwd .\app\frontend build
.\build_portable.bat
```

## Data layout

```text
data/hfdm.sqlite3
download/models/<owner>/<repo>/<commit>/<repo files>
download/datasets/<owner>/<repo>/<commit>/<repo files>
download/civitai/models/<model-id>/<version-id>/<files>
```

Both directories are runtime state and are ignored by Git. Task destinations are
stored relative to `download/`, so copying `data/` and `download/` together keeps
completed files usable at the new location.

## V1 database migration and rollback

The first V2 startup detects a V1 `data/hfdm.sqlite3`, creates the standalone backup
`data/hfdm.sqlite3.v1.bak`, and then performs the schema migration in one SQLite
transaction. Migration adds the V2 history, attempt, expected-file, and local
availability catalog without deleting the V1 operational tables or changing any
file under `download/`. If migration fails, the transaction is rolled back and the
service refuses to continue initialization.

To return to V1, stop HFDM completely, preserve the V2 database under a different
name for diagnosis, and copy `hfdm.sqlite3.v1.bak` back to `hfdm.sqlite3` before
starting the V1 application. Do not restore while the service is running. The
backup is never overwritten by later V2 startups.

## Security boundary

HFDM is for a trusted LAN and does not provide login or HTTPS. A user-supplied HF
or Civitai token crosses the LAN over plain HTTP, is kept only in process memory,
and is discarded after completion or cancellation. If the service restarts,
authenticated unfinished tasks enter `auth_required` and need the matching
provider token again.

Do not expose this version directly to the public internet.
