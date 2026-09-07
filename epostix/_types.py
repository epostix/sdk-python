from __future__ import annotations

from typing import TypeAlias, Union

JsonValue: TypeAlias = Union[
    None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]
]


class NotGiven:
    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()
