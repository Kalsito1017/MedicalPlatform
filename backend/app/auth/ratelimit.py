"""In-process login rate limiting / lockout (docs/SECURITY.md "Login hardening").

Policy for `POST /api/v1/auth/login`:

- Per (email, client IP): after `MAX_FAILURES_PER_EMAIL_IP` failed attempts
  within `FAILURE_WINDOW_SECONDS`, further attempts are rejected with
  `429 rate_limited` and a `Retry-After` header for the remaining window.
- Per client IP (coarse): after `MAX_FAILURES_PER_IP` failed attempts in the
  window, further login attempts from that IP are rejected regardless of email.
- A successful login clears the (email, IP) counter for that pair.
- Rejected attempts do not extend the window (the lockout expires on its own).

Limitation (documented, acceptable for this single-process demo): state is
in-memory per process. A multi-process or multi-instance deployment needs a
shared store (e.g. Redis or a database table) and per-account lockout UX; this
is tracked as production hardening work in docs/BACKLOG.md.
"""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

FAILURE_WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL_IP = 5
MAX_FAILURES_PER_IP = 50
LOCKOUT_RETRY_AFTER_SECONDS = 15 * 60


class LoginRateLimiter:
    """Sliding-window failure counter keyed by (normalized email, client IP)."""

    def __init__(
        self,
        *,
        window_seconds: int = FAILURE_WINDOW_SECONDS,
        max_failures_per_email_ip: int = MAX_FAILURES_PER_EMAIL_IP,
        max_failures_per_ip: int = MAX_FAILURES_PER_IP,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_failures_per_email_ip = max_failures_per_email_ip
        self.max_failures_per_ip = max_failures_per_ip
        self._by_email_ip: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def retry_after_seconds(self, email: str, ip: str | None) -> int | None:
        """None when login may proceed; otherwise seconds to wait (1..window)."""
        now = monotonic()
        pair = (email.strip().lower(), ip or "")
        pair_bucket = self._by_email_ip[pair]
        self._prune(pair_bucket, now)
        ip_bucket = self._by_ip[pair[1]]
        self._prune(ip_bucket, now)

        blocked = (
            len(pair_bucket) >= self.max_failures_per_email_ip
            or len(ip_bucket) >= self.max_failures_per_ip
        )
        if not blocked:
            return None
        oldest = min(pair_bucket[0] if pair_bucket else now, ip_bucket[0] if ip_bucket else now)
        remaining = int((oldest + self.window_seconds) - now) + 1
        return max(1, min(remaining, self.window_seconds))

    def record_failure(self, email: str, ip: str | None) -> None:
        now = monotonic()
        pair = (email.strip().lower(), ip or "")
        self._by_email_ip[pair].append(now)
        self._by_ip[pair[1]].append(now)

    def record_success(self, email: str, ip: str | None) -> None:
        pair = (email.strip().lower(), ip or "")
        self._by_email_ip.pop(pair, None)

    def reset(self) -> None:
        self._by_email_ip.clear()
        self._by_ip.clear()


login_rate_limiter = LoginRateLimiter()
