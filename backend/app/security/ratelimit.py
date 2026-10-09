"""In-process sliding-window rate limiter and login lockout (SECURITY.md §2, §3.1). Single-node POC."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class LoginLockout:
    def __init__(self, max_failures: int = 10, lock_s: float = 15 * 60) -> None:
        self.max_failures, self.lock_s = max_failures, lock_s
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_locked(self, username: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures[username] if now - t < self.lock_s]
            self._failures[username] = recent
            return len(recent) >= self.max_failures

    def record_failure(self, username: str) -> None:
        with self._lock:
            self._failures[username].append(time.monotonic())

    def clear(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)
