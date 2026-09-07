from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Literal, TypeAlias

from ._errors import (
    EpostixAPIError,
    EpostixErrorType,
    EpostixIdempotencyInProgressError,
    EpostixRateLimitError,
)
from ._response import RetriesExhaustedReason

RetryClass: TypeAlias = Literal[
    "SafeRead",
    "NaturallyIdempotent",
    "DeduplicatedAdmission",
    "ExcludedMutation",
    "Streaming",
]

MutationRetryMode: TypeAlias = Literal["Never", "OnlyWhenDeduplicated", "Always"]


@dataclass(frozen=True)
class RetryPolicy:
    maximum_retries: int = 2
    operation_deadline: float = 30.0
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    backoff_base: float = 0.25
    backoff_maximum: float = 2.0
    backoff_multiplier: float = 2.0
    honor_retry_after: bool = True
    mutation_retry: MutationRetryMode = "OnlyWhenDeduplicated"


DEFAULT_RETRY_POLICY = RetryPolicy()
DISABLED_RETRY_POLICY = replace(DEFAULT_RETRY_POLICY, maximum_retries=0)
UPLOAD_RETRY_POLICY = replace(
    DEFAULT_RETRY_POLICY,
    operation_deadline=180.0,
    connect_timeout=30.0,
    read_timeout=180.0,
)
BATCH_RETRY_POLICY = replace(
    DEFAULT_RETRY_POLICY,
    operation_deadline=90.0,
    read_timeout=90.0,
)

NEVER_RETRY: frozenset[str] = frozenset(
    {
        EpostixErrorType.AUTHENTICATION_FAILED.value,
        EpostixErrorType.API_KEY_EXPIRED.value,
        EpostixErrorType.INSUFFICIENT_SCOPE.value,
        EpostixErrorType.API_KEY_IP_RESTRICTED.value,
        EpostixErrorType.DOMAIN_SCOPE_RESTRICTED.value,
        EpostixErrorType.TEST_MODE_RESTRICTED.value,
        EpostixErrorType.WORKSPACE_SUSPENDED.value,
        EpostixErrorType.VALIDATION_ERROR.value,
        EpostixErrorType.INVALID_REQUEST.value,
        EpostixErrorType.INVALID_FROM_ADDRESS.value,
        EpostixErrorType.INVALID_SCHEDULE.value,
        EpostixErrorType.BATCH_TOO_LARGE.value,
        EpostixErrorType.PAYLOAD_TOO_LARGE.value,
        EpostixErrorType.UNSUPPORTED_MEDIA_TYPE.value,
        EpostixErrorType.METHOD_NOT_ALLOWED.value,
        EpostixErrorType.EMAIL_NOT_CANCELLABLE.value,
        EpostixErrorType.DUPLICATE_DETECTED.value,
        EpostixErrorType.DOMAIN_NOT_VERIFIED.value,
        EpostixErrorType.API_KEY_IN_USE.value,
        EpostixErrorType.WEBHOOK_LIMIT_REACHED.value,
        EpostixErrorType.WEBHOOK_URL_NOT_ALLOWED.value,
        EpostixErrorType.NOT_FOUND.value,
        EpostixErrorType.DOMAIN_NOT_FOUND.value,
        EpostixErrorType.TEMPLATE_NOT_FOUND.value,
        EpostixErrorType.ATTACHMENT_NOT_FOUND.value,
        EpostixErrorType.ATTACHMENT_EXPIRED.value,
        EpostixErrorType.ATTACHMENT_TOO_LARGE.value,
        EpostixErrorType.TEMPLATE_RENDER_FAILED.value,
        EpostixErrorType.IDEMPOTENCY_CONFLICT.value,
        EpostixErrorType.DAILY_QUOTA_EXCEEDED.value,
        EpostixErrorType.MONTHLY_QUOTA_EXCEEDED.value,
    }
)


@dataclass(frozen=True)
class RetryDecision:
    action: Literal["stop", "retry"]
    delay: float = 0.0
    reason: RetriesExhaustedReason | None = None


@dataclass(frozen=True)
class ClassifyContext:
    policy: RetryPolicy
    retry_class: RetryClass
    attempt_number: int
    remaining: float
    error: BaseException | None
    has_idempotency_key: bool
    body_is_replayable: bool
    bytes_were_written: bool
    response_started: bool
    random: Callable[[], float]


def full_jitter(policy: RetryPolicy, attempt_number: int, random: Callable[[], float]) -> float:
    ceiling = min(
        policy.backoff_maximum,
        policy.backoff_base * (policy.backoff_multiplier ** (attempt_number - 1)),
    )

    return random() * ceiling


def _fits_deadline(delay: float, context: ClassifyContext) -> bool:
    return delay + context.policy.connect_timeout <= context.remaining


def _class_for_transient(context: ClassifyContext) -> RetryDecision:
    delay = full_jitter(context.policy, context.attempt_number, context.random)

    if context.retry_class in ("SafeRead", "NaturallyIdempotent"):
        return RetryDecision("retry", delay)

    if context.retry_class == "Streaming":
        if context.response_started:
            return RetryDecision("stop", reason="NotRetryable")

        return RetryDecision("retry", delay)

    if context.retry_class == "ExcludedMutation":
        if context.bytes_were_written:
            return RetryDecision("stop", reason="OperationExcluded")

        return RetryDecision("retry", delay)

    if context.policy.mutation_retry == "Never":
        return RetryDecision("stop", reason="OperationExcluded")

    if context.policy.mutation_retry == "Always":
        return RetryDecision("retry", delay)

    if not context.has_idempotency_key:
        return RetryDecision("stop", reason="OperationExcluded")

    if not context.body_is_replayable:
        return RetryDecision("stop", reason="BodyNotReplayable")

    return RetryDecision("retry", delay)


def _guard_deadline(decision: RetryDecision, context: ClassifyContext) -> RetryDecision:
    if decision.action == "retry" and not _fits_deadline(decision.delay, context):
        return RetryDecision("stop", reason="DeadlineExhausted")

    return decision


def classify_attempt(context: ClassifyContext) -> RetryDecision:
    if context.attempt_number > context.policy.maximum_retries:
        return RetryDecision("stop", reason="AttemptBudgetExhausted")

    if context.remaining <= context.policy.connect_timeout:
        return RetryDecision("stop", reason="DeadlineExhausted")

    error = context.error

    if isinstance(error, EpostixAPIError):
        if error.type in NEVER_RETRY:
            return RetryDecision("stop", reason="NotRetryable")

        if isinstance(error, (EpostixRateLimitError, EpostixIdempotencyInProgressError)):
            retry_after = error.response_metadata.retry_after

            if not context.policy.honor_retry_after or retry_after is None:
                return _guard_deadline(_class_for_transient(context), context)

            if not _fits_deadline(float(retry_after), context):
                return RetryDecision("stop", reason="RetryAfterExceedsDeadline")

            return RetryDecision("retry", float(retry_after))

        retryable_status = error.status == 408 or error.status >= 500
        retryable_type = error.type == EpostixErrorType.ATTACHMENT_FETCH_FAILED.value

        if not retryable_status and not retryable_type:
            return RetryDecision("stop", reason="NotRetryable")

    return _guard_deadline(_class_for_transient(context), context)
