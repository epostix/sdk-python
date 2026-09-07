from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypeAlias

RetriesExhaustedReason: TypeAlias = Literal[
    "AttemptBudgetExhausted",
    "DeadlineExhausted",
    "RetryAfterExceedsDeadline",
    "NotRetryable",
    "OperationExcluded",
    "BodyNotReplayable",
    "Cancelled",
]

_POLICY = re.compile(r"^\s*(\d+)\s*;\s*w\s*=\s*(\d+)\s*$")


@dataclass(frozen=True)
class RateLimitSnapshot:
    limit: int | None
    remaining: int | None
    reset_after: int | None
    policy_burst: int | None
    policy_window: int | None
    scope: str | None
    raw_policy: str | None


@dataclass(frozen=True)
class AttemptRecord:
    attempt_number: int
    started_at: float
    elapsed: float
    status_code: int | None
    error_type: str | None
    retry_after: int | None
    slept_before: float


@dataclass
class ResponseMetadata:
    status_code: int = 0
    request_id: str = ""
    rate_limit: RateLimitSnapshot | None = None
    retry_after: int | None = None
    idempotent_replayed: bool = False
    idempotency_key: str | None = None
    attempts: int = 0
    elapsed: float = 0.0
    attempt_records: list[AttemptRecord] = field(default_factory=list)
    retries_exhausted_reason: RetriesExhaustedReason | None = None
    transport_may_have_retried: bool | None = None


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    raw = headers.get(name)
    if raw is None:
        return None

    try:
        return int(raw.strip())
    except ValueError:
        return None


def rate_limit_from(headers: Mapping[str, str]) -> Optional[RateLimitSnapshot]:
    raw_policy = headers.get("RateLimit-Policy")
    limit = _header_int(headers, "RateLimit-Limit")
    remaining = _header_int(headers, "RateLimit-Remaining")
    reset_after = _header_int(headers, "RateLimit-Reset")
    scope = headers.get("RateLimit-Scope")

    if (
        limit is None
        and remaining is None
        and reset_after is None
        and raw_policy is None
        and scope is None
    ):
        return None

    policy_burst: int | None = None
    policy_window: int | None = None

    if raw_policy is not None:
        match = _POLICY.match(raw_policy)
        if match is not None:
            policy_burst = int(match.group(1))
            policy_window = int(match.group(2))

    return RateLimitSnapshot(
        limit=limit,
        remaining=remaining,
        reset_after=reset_after,
        policy_burst=policy_burst,
        policy_window=policy_window,
        scope=scope,
        raw_policy=raw_policy,
    )


def retry_after_from(headers: Mapping[str, str]) -> int | None:
    return _header_int(headers, "Retry-After")


def request_id_from(headers: Mapping[str, str]) -> str:
    return headers.get("x-request-id") or headers.get("request-id") or ""
