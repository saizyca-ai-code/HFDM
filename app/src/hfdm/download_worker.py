from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any

from huggingface_hub import hf_hub_download
from tqdm import tqdm


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


class JsonTqdm(tqdm):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["disable"] = False
        kwargs["mininterval"] = 0.2
        self._last_emit = 0.0
        super().__init__(*args, **kwargs)

    def display(self, msg: str | None = None, pos: int | None = None) -> None:
        return None

    def update(self, n: int | float = 1) -> bool | None:
        result = super().update(n)
        now = time.monotonic()
        if now - self._last_emit >= 0.2 or (self.total and self.n >= self.total):
            self._last_emit = now
            emit({"type": "progress", "downloaded": int(self.n), "total": int(self.total or 0)})
        return result


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        path = hf_hub_download(
            repo_id=payload["repo_id"],
            filename=payload["filename"],
            repo_type="model",
            revision=payload["commit_hash"],
            local_dir=payload["destination"],
            token=payload.get("token") or False,
            tqdm_class=JsonTqdm,
        )
        emit({"type": "complete", "path": path})
        return 0
    except BaseException as exc:
        emit({"type": "error", "error": str(exc), "kind": type(exc).__name__})
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
