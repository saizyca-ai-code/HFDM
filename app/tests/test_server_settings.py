from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hfdm.main import ServerSettings, _open_browser_when_ready


class ServerSettingsTests(unittest.TestCase):
    def test_defaults_are_local_browser_safe(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = ServerSettings.from_environment()

        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8765)
        self.assertFalse(settings.open_browser)
        self.assertEqual(settings.listen_url, "http://0.0.0.0:8765")
        self.assertEqual(settings.browser_url, "http://127.0.0.1:8765")

    def test_batch_environment_overrides_all_settings(self) -> None:
        environment = {
            "HFDM_HOST": "127.0.0.1",
            "HFDM_PORT": "9876",
            "HFDM_OPEN_BROWSER": "yes",
            "HFDM_BROWSER_URL": "http://localhost:9876/tasks",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = ServerSettings.from_environment()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 9876)
        self.assertTrue(settings.open_browser)
        self.assertEqual(settings.browser_url, "http://localhost:9876/tasks")

    def test_invalid_port_is_rejected(self) -> None:
        with patch.dict(os.environ, {"HFDM_PORT": "70000"}, clear=True):
            with self.assertRaisesRegex(ValueError, "HFDM_PORT"):
                ServerSettings.from_environment()

    def test_invalid_browser_flag_is_rejected(self) -> None:
        with patch.dict(os.environ, {"HFDM_OPEN_BROWSER": "sometimes"}, clear=True):
            with self.assertRaisesRegex(ValueError, "HFDM_OPEN_BROWSER"):
                ServerSettings.from_environment()

    def test_browser_opens_after_server_is_ready(self) -> None:
        server = SimpleNamespace(started=True, should_exit=False)
        with patch("hfdm.main.webbrowser.open") as open_browser:
            _open_browser_when_ready(server, "http://127.0.0.1:9876")

        open_browser.assert_called_once_with("http://127.0.0.1:9876")


if __name__ == "__main__":
    unittest.main()
