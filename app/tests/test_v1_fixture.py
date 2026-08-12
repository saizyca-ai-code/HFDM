from __future__ import annotations

import sqlite3
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v1"


def test_v1_fixture_materializes_representative_database(tmp_path: Path) -> None:
    database = tmp_path / "hfdm.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript((FIXTURE_ROOT / "hfdm-v1.sql").read_text(encoding="utf-8"))
        task_states = dict(conn.execute("SELECT id, status FROM tasks"))
        file_count = conn.execute("SELECT COUNT(*) FROM task_files").fetchone()[0]

    assert task_states == {
        "v1-available": "completed",
        "v1-partial": "completed",
        "v1-moved": "completed",
        "v1-changed": "completed",
        "v1-failed": "failed",
    }
    assert file_count == 6
    assert (FIXTURE_ROOT / "download" / "models" / "fixture" / "available" / ("a" * 40) / "model.bin").read_bytes() == b"V1AA\n"
    assert not (FIXTURE_ROOT / "download" / "models" / "fixture" / "moved").exists()
