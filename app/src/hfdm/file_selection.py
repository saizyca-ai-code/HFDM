from __future__ import annotations

from pathlib import PurePosixPath


class InvalidGlobPattern(ValueError):
    pass


def select_repo_paths(
    paths: list[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> list[str]:
    includes = _normalize_patterns(include_globs)
    excludes = _normalize_patterns(exclude_globs)
    selected: list[str] = []
    for path in paths:
        candidate = PurePosixPath(path)
        if includes and not any(_matches(candidate, pattern) for pattern in includes):
            continue
        if any(_matches(candidate, pattern) for pattern in excludes):
            continue
        selected.append(path)
    return selected


def _normalize_patterns(patterns: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in patterns or []:
        pattern = raw.strip().replace("\\", "/")
        if not pattern:
            continue
        if len(pattern) > 200:
            raise InvalidGlobPattern("glob pattern 不可超過 200 個字元")
        pure = PurePosixPath(pattern)
        if pure.is_absolute() or ".." in pure.parts or "\0" in pattern:
            raise InvalidGlobPattern(f"不安全的 glob pattern：{raw}")
        if pattern not in normalized:
            normalized.append(pattern)
    return normalized


def _matches(candidate: PurePosixPath, pattern: str) -> bool:
    if candidate.match(pattern):
        return True
    return pattern.startswith("**/") and candidate.match(pattern[3:])
