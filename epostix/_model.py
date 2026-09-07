from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._response import ResponseMetadata


@dataclass
class Model:
    unknown_fields: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    response_metadata: ResponseMetadata | None = field(
        default=None, init=False, repr=False, compare=False
    )


def response_metadata_of(value: Model) -> ResponseMetadata | None:
    return value.response_metadata


def unknown_fields_of(value: Model) -> dict[str, Any]:
    return value.unknown_fields
