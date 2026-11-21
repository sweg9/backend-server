"""The server's part of sign-in (wiki: Stack > Sign-in).

The hub checks passwords and sessions; the server checks the hub token and limits login tries:
after `login_max_failures` wrong passwords within `login_window_s`, `POST /login` answers 429.
"""

import hmac
import os
import time
from collections import deque

SESSION_COOKIE = "session"


def hub_token_ok(authorization: str | None) -> bool:
    expected = os.environ.get("HUB_TOKEN")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization.removeprefix("Bearer "), expected)


class LoginLimiter:
    def __init__(self, max_failures: int, window_s: float, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_s = window_s
        self.clock = clock
        self._failures: deque[float] = deque()

    def _prune(self) -> None:
        now = self.clock()
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()

    def blocked(self) -> bool:
        self._prune()
        return len(self._failures) >= self.max_failures

    def failed(self) -> None:
        self._failures.append(self.clock())
