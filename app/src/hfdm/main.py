from __future__ import annotations

import os
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import create_router
from .civitai_service import CivitaiService
from .config import AppPaths
from .database import Database
from .download_manager import DownloadManager
from .events import EventBroker
from .hf_service import HuggingFaceService


@dataclass(frozen=True, slots=True)
class ServerSettings:
    host: str
    port: int
    open_browser: bool
    browser_url: str

    @property
    def listen_url(self) -> str:
        return f"http://{_url_host(self.host)}:{self.port}"

    @classmethod
    def from_environment(cls) -> "ServerSettings":
        host = os.getenv("HFDM_HOST", "0.0.0.0").strip()
        if not host:
            raise ValueError("HFDM_HOST must not be empty")

        port_text = os.getenv("HFDM_PORT", "8765").strip()
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("HFDM_PORT must be an integer from 1 through 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("HFDM_PORT must be an integer from 1 through 65535")

        open_browser = _environment_bool("HFDM_OPEN_BROWSER", default=False)
        browser_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
        default_browser_url = f"http://{_url_host(browser_host)}:{port}"
        browser_url = os.getenv("HFDM_BROWSER_URL", "").strip() or default_browser_url
        return cls(host, port, open_browser, browser_url)


def _environment_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be 1/0, true/false, yes/no, or on/off")


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _open_browser_when_ready(server: uvicorn.Server, url: str) -> None:
    while not server.started and not server.should_exit:
        time.sleep(0.1)
    if server.started:
        webbrowser.open(url)


def create_app(paths: AppPaths | None = None) -> FastAPI:
    app_paths = paths or AppPaths.discover()
    app_paths.ensure()
    db = Database(app_paths.database)
    db.initialize()
    broker = EventBroker()
    hf = HuggingFaceService()
    civitai = CivitaiService()
    manager = DownloadManager(app_paths, db, broker)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="HFDM", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_router(db, hf, civitai, manager, broker))
    app.state.paths = app_paths
    app.state.db = db
    app.state.manager = manager

    if app_paths.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=app_paths.frontend_dist, html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    try:
        settings = ServerSettings.from_environment()
    except ValueError as exc:
        raise SystemExit(f"Invalid HFDM server setting: {exc}") from exc

    config = uvicorn.Config(
        "hfdm.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
    server = uvicorn.Server(config)
    if settings.open_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(server, settings.browser_url),
            daemon=True,
        ).start()
    server.run()
