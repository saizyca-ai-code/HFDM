# HFDM V1 migration fixture

`hfdm-v1.sql` is a reproducible V1 database snapshot. Materialize it into an empty SQLite database with `sqlite3.Connection.executescript`; copy the adjacent `download/` tree beside the test database before starting V2.

The fixture deliberately covers:

- a completed task whose file still exists with the expected size;
- a completed task with one present and one missing file;
- a completed task whose entire destination was moved away;
- a completed task whose path exists with a changed size;
- a failed transfer that must remain historical and must not become available;
- an untracked `.part` file that reconciliation must ignore.

Migration must not edit this download tree. Tests should compare its file paths and hashes before and after initialization.
