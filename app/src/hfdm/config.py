from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_root() -> Path:
    """Find the portable runtime root without depending on package install paths."""
    package_root = Path(__file__).resolve().parents[2]

    # Source layout: <runtime>/app/src/hfdm or <project>/src/hfdm.
    if package_root.name.casefold() == "app":
        portable_root = package_root.parent
        if (portable_root / "python_embed").is_dir():
            return portable_root
    if (package_root / "frontend").is_dir():
        return package_root

    # Installed into the bundled interpreter: <runtime>/python_embed/Lib/site-packages.
    executable_dir = Path(sys.executable).resolve().parent
    if executable_dir.name.casefold() == "python_embed":
        return executable_dir.parent

    # A console-script install should keep runtime data beside the directory where
    # the user launched HFDM, never beside site-packages.
    return Path.cwd().resolve()


def _frontend_dist(root: Path) -> Path:
    portable_dist = root / "app" / "frontend" / "dist"
    if portable_dist.is_dir() or (root / "app").is_dir():
        return portable_dist
    return root / "frontend" / "dist"


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    data: Path
    downloads: Path
    database: Path
    frontend_dist: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        root_override = os.getenv("HFDM_ROOT")
        root = Path(root_override).resolve() if root_override else _default_root()
        data = Path(os.getenv("HFDM_DATA_DIR", root / "data")).resolve()
        downloads = Path(os.getenv("HFDM_DOWNLOAD_DIR", root / "download")).resolve()
        frontend_override = os.getenv("HFDM_FRONTEND_DIST")
        frontend_dist = (
            Path(frontend_override).resolve() if frontend_override else _frontend_dist(root).resolve()
        )
        return cls(
            root=root,
            data=data,
            downloads=downloads,
            database=data / "hfdm.sqlite3",
            frontend_dist=frontend_dist,
        )

    def ensure(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS: dict[str, int] = {
    "max_concurrent_files": 2,
    "max_storage_bytes": 0,
    "min_free_bytes": 10 * 1024**3,
    "retention_days": 0,
    "allow_delete_files": 1,
    "civitai_segments": 4,
}
