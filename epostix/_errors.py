from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Sequence, TypeAlias

from ._response import RateLimitSnapshot, ResponseMetadata

TimeoutPhase: TypeAlias = Literal["connect", "read", "operation_deadline"]
QuotaPeriod: TypeAlias = Literal["daily", "monthly"]

PaginationFailureReason: TypeAlias = Literal[
    "MissingCursor",
    "RepeatedCursor",
    "ContinuationUnsupported",
    "PageLimitExceeded",
]

WebhookFailureReason: TypeAlias = Literal[
    "MissingSignatureHeader",
    "MissingTimestampHeader",
    "MalformedSignatureHeader",
    "TimestampOutOfTolerance",
    "NoMatchingSignature",
    "MalformedPayload",
]


class EpostixErrorType(str, Enum):
    INVALID_REQUEST = "invalid_request"
    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_FOUND = "not_found"
    AUTHENTICATION_FAILED = "authentication_failed"
    API_KEY_EXPIRED = "api_key_expired"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    API_KEY_IP_RESTRICTED = "api_key_ip_restricted"
    DOMAIN_SCOPE_RESTRICTED = "domain_scope_restricted"
    WORKSPACE_SUSPENDED = "workspace_suspended"
    TEST_MODE_RESTRICTED = "test_mode_restricted"
    DOMAIN_NOT_FOUND = "domain_not_found"
    DOMAIN_NOT_VERIFIED = "domain_not_verified"
    INVALID_FROM_ADDRESS = "invalid_from_address"
    DUPLICATE_DETECTED = "duplicate_detected"
    INVALID_SCHEDULE = "invalid_schedule"
    EMAIL_NOT_CANCELLABLE = "email_not_cancellable"
    BATCH_TOO_LARGE = "batch_too_large"
    TEMPLATE_NOT_FOUND = "template_not_found"
    TEMPLATE_RENDER_FAILED = "template_render_failed"
    ATTACHMENT_NOT_FOUND = "attachment_not_found"
    ATTACHMENT_EXPIRED = "attachment_expired"
    ATTACHMENT_TOO_LARGE = "attachment_too_large"
    ATTACHMENT_FETCH_FAILED = "attachment_fetch_failed"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded"
    MONTHLY_QUOTA_EXCEEDED = "monthly_quota_exceeded"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_IN_PROGRESS = "idempotency_in_progress"
    API_KEY_IN_USE = "api_key_in_use"
    WEBHOOK_LIMIT_REACHED = "webhook_limit_reached"
    WEBHOOK_URL_NOT_ALLOWED = "webhook_url_not_allowed"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str
    code: str


class EpostixError(Exception):
    pass


class EpostixConfigurationError(EpostixError):
    pass


class EpostixTransportError(EpostixError):
    def __init__(
        self,
        message: str,
        cause: BaseException | None,
        response_metadata: ResponseMetadata,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.response_metadata = response_metadata


class EpostixTimeoutError(EpostixTransportError):
    def __init__(
        self,
        phase: TimeoutPhase,
        cause: BaseException | None,
        response_metadata: ResponseMetadata,
    ) -> None:
        super().__init__(f"request timed out during {phase}", cause, response_metadata)
        self.phase = phase


class EpostixOutcomeUnknownError(EpostixError):
    def __init__(
        self,
        operation: str,
        idempotency_key: str | None,
        replayable_with_key: bool,
        response_metadata: ResponseMetadata,
        cause: BaseException | None,
    ) -> None:
        super().__init__(
            f"the outcome of {operation} could not be established:"
            " the request may or may not have been accepted"
        )
        self.operation = operation
        self.idempotency_key = idempotency_key
        self.replayable_with_key = replayable_with_key
        self.response_metadata = response_metadata
        self.cause = cause


class EpostixPaginationError(EpostixError):
    def __init__(self, reason: PaginationFailureReason, operation: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.operation = operation


class EpostixWebhookVerificationError(EpostixError):
    def __init__(self, reason: WebhookFailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class EpostixAPIError(EpostixError):
    def __init__(
        self,
        *,
        status: int,
        type: str,
        is_known_type: bool,
        api_message: str,
        request_id: str,
        details: Sequence[FieldError],
        response_metadata: ResponseMetadata,
        doc_url: str | None = None,
        raw_body_snippet: str | None = None,
        raw_body_truncated: bool = False,
    ) -> None:
        label = type if type else f"http_{status}"
        super().__init__(
            f"{label}: {api_message} (status={status}, request_id={request_id})"
        )
        self.status = status
        self.type = type
        self.is_known_type = is_known_type
        self.api_message = api_message
        self.request_id = request_id
        self.details = list(details)
        self.response_metadata = response_metadata
        self.doc_url = doc_url
        self.raw_body_snippet = raw_body_snippet
        self.raw_body_truncated = raw_body_truncated


class EpostixInvalidRequestError(EpostixAPIError):
    pass


class EpostixValidationError(EpostixAPIError):
    @property
    def field_errors(self) -> list[FieldError]:
        return self.details


class EpostixAuthenticationError(EpostixAPIError):
    pass


class EpostixPermissionError(EpostixAPIError):
    pass


class EpostixAccountSuspendedError(EpostixAPIError):
    pass


class EpostixNotFoundError(EpostixAPIError):
    pass


class EpostixConflictError(EpostixAPIError):
    pass


class EpostixIdempotencyConflictError(EpostixAPIError):
    pass


class EpostixContentError(EpostixAPIError):
    pass


class EpostixServerError(EpostixAPIError):
    pass


class EpostixIdempotencyInProgressError(EpostixAPIError):
    @property
    def retry_after(self) -> int | None:
        return self.response_metadata.retry_after


class EpostixRateLimitError(EpostixAPIError):
    @property
    def retry_after(self) -> int | None:
        return self.response_metadata.retry_after

    @property
    def rate_limit(self) -> RateLimitSnapshot | None:
        return self.response_metadata.rate_limit


class EpostixQuotaExceededError(EpostixAPIError):
    @property
    def quota_period(self) -> QuotaPeriod:
        return "daily" if self.type == EpostixErrorType.DAILY_QUOTA_EXCEEDED else "monthly"

    @property
    def retry_after(self) -> int | None:
        if self.quota_period == "monthly":
            return None

        return self.response_metadata.retry_after

    @property
    def next_attempt_at(self) -> datetime | None:
        retry_after = self.retry_after
        if retry_after is None:
            return None

        return datetime.now(timezone.utc) + timedelta(seconds=retry_after)


_BY_TYPE: dict[str, type[EpostixAPIError]] = {
    EpostixErrorType.INVALID_REQUEST.value: EpostixInvalidRequestError,
    EpostixErrorType.UNSUPPORTED_MEDIA_TYPE.value: EpostixInvalidRequestError,
    EpostixErrorType.PAYLOAD_TOO_LARGE.value: EpostixInvalidRequestError,
    EpostixErrorType.METHOD_NOT_ALLOWED.value: EpostixInvalidRequestError,
    EpostixErrorType.VALIDATION_ERROR.value: EpostixValidationError,
    EpostixErrorType.INVALID_FROM_ADDRESS.value: EpostixValidationError,
    EpostixErrorType.INVALID_SCHEDULE.value: EpostixValidationError,
    EpostixErrorType.BATCH_TOO_LARGE.value: EpostixValidationError,
    EpostixErrorType.AUTHENTICATION_FAILED.value: EpostixAuthenticationError,
    EpostixErrorType.API_KEY_EXPIRED.value: EpostixAuthenticationError,
    EpostixErrorType.INSUFFICIENT_SCOPE.value: EpostixPermissionError,
    EpostixErrorType.API_KEY_IP_RESTRICTED.value: EpostixPermissionError,
    EpostixErrorType.DOMAIN_SCOPE_RESTRICTED.value: EpostixPermissionError,
    EpostixErrorType.TEST_MODE_RESTRICTED.value: EpostixPermissionError,
    EpostixErrorType.WORKSPACE_SUSPENDED.value: EpostixAccountSuspendedError,
    EpostixErrorType.NOT_FOUND.value: EpostixNotFoundError,
    EpostixErrorType.DOMAIN_NOT_FOUND.value: EpostixNotFoundError,
    EpostixErrorType.TEMPLATE_NOT_FOUND.value: EpostixNotFoundError,
    EpostixErrorType.ATTACHMENT_NOT_FOUND.value: EpostixNotFoundError,
    EpostixErrorType.DUPLICATE_DETECTED.value: EpostixConflictError,
    EpostixErrorType.EMAIL_NOT_CANCELLABLE.value: EpostixConflictError,
    EpostixErrorType.DOMAIN_NOT_VERIFIED.value: EpostixConflictError,
    EpostixErrorType.API_KEY_IN_USE.value: EpostixConflictError,
    EpostixErrorType.WEBHOOK_LIMIT_REACHED.value: EpostixConflictError,
    EpostixErrorType.WEBHOOK_URL_NOT_ALLOWED.value: EpostixConflictError,
    EpostixErrorType.IDEMPOTENCY_CONFLICT.value: EpostixIdempotencyConflictError,
    EpostixErrorType.IDEMPOTENCY_IN_PROGRESS.value: EpostixIdempotencyInProgressError,
    EpostixErrorType.RATE_LIMIT_EXCEEDED.value: EpostixRateLimitError,
    EpostixErrorType.DAILY_QUOTA_EXCEEDED.value: EpostixQuotaExceededError,
    EpostixErrorType.MONTHLY_QUOTA_EXCEEDED.value: EpostixQuotaExceededError,
    EpostixErrorType.TEMPLATE_RENDER_FAILED.value: EpostixContentError,
    EpostixErrorType.ATTACHMENT_EXPIRED.value: EpostixContentError,
    EpostixErrorType.ATTACHMENT_TOO_LARGE.value: EpostixContentError,
    EpostixErrorType.ATTACHMENT_FETCH_FAILED.value: EpostixContentError,
    EpostixErrorType.INTERNAL_ERROR.value: EpostixServerError,
    EpostixErrorType.SERVICE_UNAVAILABLE.value: EpostixServerError,
}


def _by_status(status: int) -> type[EpostixAPIError]:
    if status == 401:
        return EpostixAuthenticationError
    if status == 403:
        return EpostixPermissionError
    if status == 404:
        return EpostixNotFoundError
    if status == 409:
        return EpostixConflictError
    if status == 429:
        return EpostixRateLimitError
    if status >= 500:
        return EpostixServerError
    if status >= 400:
        return EpostixInvalidRequestError

    return EpostixAPIError


def build_api_error(
    *,
    status: int,
    type: str,
    api_message: str,
    request_id: str,
    details: Sequence[FieldError],
    response_metadata: ResponseMetadata,
    doc_url: str | None = None,
    raw_body_snippet: str | None = None,
    raw_body_truncated: bool = False,
) -> EpostixAPIError:
    known = type in _BY_TYPE
    constructor = _BY_TYPE[type] if known else _by_status(status)

    return constructor(
        status=status,
        type=type,
        is_known_type=known,
        api_message=api_message,
        request_id=request_id,
        details=details,
        response_metadata=response_metadata,
        doc_url=doc_url,
        raw_body_snippet=raw_body_snippet,
        raw_body_truncated=raw_body_truncated,
    )
