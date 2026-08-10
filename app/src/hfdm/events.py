from __future__ import annotations

import json
import queue
import threading
from collections.abc import Generator
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._listeners: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener.put_nowait(event)
            except queue.Full:
                pass

    def stream(self) -> Generator[str, None, None]:
        listener: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._listeners.add(listener)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = listener.get(timeout=15)
                    yield f"event: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with self._lock:
                self._listeners.discard(listener)
