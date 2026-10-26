"""AI endpoint request-rate limiting (docs/SECURITY.md "DoS on the AI endpoint").

Sliding-window limiter: `POST /api/v1/ai/ask` allows
`LLM_REQUESTS_PER_MINUTE` (default 10) requests per user per 60-second window;
further requests are rejected with 429 `rate_limited` and a Retry-After header.

This is not `app.auth.ratelimit`: that shared module counts LOGIN FAILURES for
progressive lockout and is used through its public API only. Counting every AI
request (successes included) is a different policy, so the limiter lives next
to the feature that owns it rather than changing shared auth internals.

Limitation (documented, same as the login limiter): state is in-memory per
process. A multi-process deployment needs a shared store (docs/BACKLOG.md).
"""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

AI_WINDOW_SECONDS = 60
AI_REQUESTS_PER_MINUTE = 10
AI_RETRY_AFTER_SECONDS = 60


class SlidingWindowRateLimiter:
    """Counts requests per key inside a sliding window."""

    def __init__(
        self,
        *,
        window_seconds: int = AI_WINDOW_SECONDS,
        max_requests: int = AI_REQUESTS_PER_MINUTE,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def retry_after_seconds(self, key: str) -> int | None:
        """None when a request may proceed; otherwise seconds to wait (1..window)."""
        now = monotonic()
        bucket = self._hits[key]
        self._prune(bucket, now)
        if len(bucket) < self.max_requests:
            return None
        remaining = int((bucket[0] + self.window_seconds) - now) + 1
        return max(1, min(remaining, self.window_seconds))

    def record(self, key: str) -> None:
        self._hits[key].append(monotonic())

    def reset(self) -> None:
        self._hits.clear()


ai_rate_limiter = SlidingWindowRateLimiter()
