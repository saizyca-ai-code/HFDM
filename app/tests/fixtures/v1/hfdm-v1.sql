PRAGMA foreign_keys = ON;

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    requested_revision TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    speed_bps REAL NOT NULL DEFAULT 0,
    eta_seconds INTEGER,
    requires_token INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE task_files (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE(task_id, path)
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings(key, value) VALUES
    ('max_concurrent_files', '2'),
    ('max_storage_bytes', '0'),
    ('min_free_bytes', '0'),
    ('retention_days', '0'),
    ('allow_delete_files', '1');

INSERT INTO tasks VALUES
    ('v1-available', 'fixture/available', 'main', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'models/fixture/available/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'completed', 5, 5, 100.0, NULL, 0, NULL, '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:01+00:00'),
    ('v1-partial', 'fixture/partial', 'main', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'models/fixture/partial/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'completed', 14, 14, 80.0, NULL, 0, NULL, '2026-08-02T00:00:00+00:00', '2026-08-02T00:00:02+00:00'),
    ('v1-moved', 'fixture/moved', 'main', 'cccccccccccccccccccccccccccccccccccccccc', 'models/fixture/moved/cccccccccccccccccccccccccccccccccccccccc', 'completed', 5, 5, 60.0, NULL, 0, NULL, '2026-08-03T00:00:00+00:00', '2026-08-03T00:00:03+00:00'),
    ('v1-changed', 'fixture/changed', 'main', 'dddddddddddddddddddddddddddddddddddddddd', 'models/fixture/changed/dddddddddddddddddddddddddddddddddddddddd', 'completed', 5, 5, 40.0, NULL, 0, NULL, '2026-08-04T00:00:00+00:00', '2026-08-04T00:00:04+00:00'),
    ('v1-failed', 'fixture/failed', 'main', 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'models/fixture/failed/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'failed', 6, 0, 0.0, NULL, 0, 'fixture failure', '2026-08-05T00:00:00+00:00', '2026-08-05T00:00:05+00:00');

INSERT INTO task_files VALUES
    ('file-available', 'v1-available', 'model.bin', 5, 'completed', 5, NULL),
    ('file-partial-present', 'v1-partial', 'present.bin', 10, 'completed', 10, NULL),
    ('file-partial-missing', 'v1-partial', 'missing.bin', 4, 'completed', 4, NULL),
    ('file-moved', 'v1-moved', 'model.bin', 5, 'completed', 5, NULL),
    ('file-changed', 'v1-changed', 'model.bin', 5, 'completed', 5, NULL),
    ('file-failed', 'v1-failed', 'model.bin', 6, 'failed', 0, 'fixture failure');
