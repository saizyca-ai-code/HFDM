from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import hfdm.config as config_module
from hfdm.config import AppPaths


class AppPathsTests(unittest.TestCase):
    def test_portable_source_layout_uses_outer_runtime_root(self) -> None:
        runtime_root = Path(__file__).resolve().parents[2]

        with patch.dict(os.environ, {}, clear=True):
            paths = AppPaths.discover()

        self.assertEqual(paths.root, runtime_root)
        self.assertEqual(paths.data, runtime_root / "data")
        self.assertEqual(paths.downloads, runtime_root / "download")
        self.assertEqual(paths.frontend_dist, runtime_root / "app" / "frontend" / "dist")

    def test_explicit_runtime_paths_take_precedence(self) -> None:
        runtime_root = Path(__file__).resolve().parent / "runtime"
        frontend_dist = runtime_root / "web"
        environment = {
            "HFDM_ROOT": str(runtime_root),
            "HFDM_DATA_DIR": str(runtime_root / "state"),
            "HFDM_DOWNLOAD_DIR": str(runtime_root / "files"),
            "HFDM_FRONTEND_DIST": str(frontend_dist),
        }

        with patch.dict(os.environ, environment, clear=True):
            paths = AppPaths.discover()

        self.assertEqual(paths.root, runtime_root)
        self.assertEqual(paths.data, runtime_root / "state")
        self.assertEqual(paths.downloads, runtime_root / "files")
        self.assertEqual(paths.frontend_dist, frontend_dist)

    def test_installed_package_uses_embedded_python_runtime_root(self) -> None:
        runtime_root = Path(__file__).resolve().parent / "installed-runtime"
        installed_config = (
            runtime_root / "python_embed" / "Lib" / "site-packages" / "hfdm" / "config.py"
        )
        embedded_python = runtime_root / "python_embed" / "python.exe"

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(config_module, "__file__", str(installed_config)),
            patch.object(config_module.sys, "executable", str(embedded_python)),
        ):
            paths = AppPaths.discover()

        self.assertEqual(paths.root, runtime_root)
        self.assertEqual(paths.data, runtime_root / "data")
        self.assertNotIn("Lib", paths.data.parts)


if __name__ == "__main__":
    unittest.main()
