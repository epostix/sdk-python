from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

from ._generated.models import EmailBatchResponse
from ._types import NotGiven

BatchOutcomeStatus: TypeAlias = Literal["accepted", "rejected", "unresolved"]
UnresolvedReason: TypeAlias = Literal["NotReported", "StatusFailedWithoutError"]


@dataclass(frozen=True)
class AcceptedOutcome:
    index: int
    email_id: str
    status: str
    outcome: BatchOutcomeStatus = "accepted"


@dataclass(frozen=True)
class RejectedOutcome:
    index: int
    error_type: str
    message: str
    outcome: BatchOutcomeStatus = "rejected"


@dataclass(frozen=True)
class UnresolvedOutcome:
    index: int
    reason: UnresolvedReason
    outcome: BatchOutcomeStatus = "unresolved"


BatchOutcome: TypeAlias = AcceptedOutcome | RejectedOutcome | UnresolvedOutcome


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    outcomes: list[BatchOutcome]
    accepted: list[AcceptedOutcome]
    rejected: list[RejectedOutcome]
    unresolved: list[UnresolvedOutcome]
    accepted_email_ids: list[str]
    all_accepted: bool
    has_unresolved: bool


def _text(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)

    if isinstance(value, NotGiven) or value is None:
        return ""

    return str(value)


def normalize_batch(response: EmailBatchResponse, input_count: int) -> BatchResult:
    by_index: dict[int, BatchOutcome] = {}

    results = response.results if not isinstance(response.results, NotGiven) else []

    for item in results:
        index = item.index
        if index < 0:
            continue

        status = _text(item.status)
        email_id = _text(item.id)

        if email_id and status != "failed":
            by_index[index] = AcceptedOutcome(
                index=index, email_id=email_id, status=status or "pending"
            )
            continue

        error = item.error
        if not isinstance(error, NotGiven) and error is not None:
            by_index[index] = RejectedOutcome(
                index=index, error_type=_text(error.type), message=_text(error.message)
            )
            continue

        by_index[index] = UnresolvedOutcome(index=index, reason="StatusFailedWithoutError")

    errors = response.errors if not isinstance(response.errors, NotGiven) else []

    for failure in errors:
        index = failure.index
        if index < 0 or index in by_index:
            continue

        by_index[index] = RejectedOutcome(
            index=index, error_type=_text(failure.code), message=_text(failure.message)
        )

    outcomes: list[BatchOutcome] = [
        by_index.get(index, UnresolvedOutcome(index=index, reason="NotReported"))
        for index in range(input_count)
    ]

    accepted = [item for item in outcomes if isinstance(item, AcceptedOutcome)]
    rejected = [item for item in outcomes if isinstance(item, RejectedOutcome)]
    unresolved = [item for item in outcomes if isinstance(item, UnresolvedOutcome)]

    return BatchResult(
        batch_id=_text(response.batch_id),
        outcomes=outcomes,
        accepted=accepted,
        rejected=rejected,
        unresolved=unresolved,
        accepted_email_ids=[item.email_id for item in accepted],
        all_accepted=not rejected and not unresolved,
        has_unresolved=bool(unresolved),
    )
