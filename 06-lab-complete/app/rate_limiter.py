"""
Rate limiting module — sliding window counter (in-memory).

Each user bucket = first 8 chars of their API key.
Raises HTTP 429 when requests exceed the configured limit per minute.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException


# Global sliding-window store: key -> deque of timestamps
_rate_windows: dict[str, deque] = defaultdict(deque)


def check_rate_limit(user_key: str, rate_limit_per_minute: int) -> None:
    """
    Sliding window rate limiter.

    Args:
        user_key: identifier for the caller (first 8 chars of API key).
        rate_limit_per_minute: max allowed requests in a 60-second window.

    Raises:
        HTTPException(429): if the caller has exceeded their rate limit.
    """
    bucket = user_key[:8]
    now = time.time()
    window = _rate_windows[bucket]

    # Remove timestamps outside the 60-second window
    while window and window[0] < now - 60:
        window.popleft()

    if len(window) >= rate_limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {rate_limit_per_minute} req/min. Try again later.",
            headers={"Retry-After": "60"},
        )

    window.append(now)
