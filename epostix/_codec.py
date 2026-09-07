from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Sequence

from ._errors import EpostixConfigurationError, EpostixError
from ._mailbox import Mailbox
from ._model import Model
from ._types import NOT_GIVEN, NotGiven


class EpostixDecodeError(EpostixError):
    pass


class Codec(ABC):
    @abstractmethod
    def encode(self, value: Any) -> Any: ...

    @abstractmethod
    def decode(self, value: Any) -> Any: ...


class _Passthrough(Codec):
    def encode(self, value: Any) -> Any:
        return value

    def decode(self, value: Any) -> Any:
        return value


def format_datetime(value: datetime) -> str:
    text = value.isoformat()

    if text.endswith("+00:00"):
        return text[:-6] + "Z"

    return text


class _DateTime(Codec):
    def encode(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return format_datetime(value)

        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value

        return value


PASSTHROUGH: Codec = _Passthrough()
DATETIME: Codec = _DateTime()


class ArrayCodec(Codec):
    def __init__(self, inner: Callable[[], Codec]) -> None:
        self._inner = inner

    def encode(self, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            inner = self._inner()
            return [inner.encode(item) for item in value]

        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, list):
            inner = self._inner()
            return [inner.decode(item) for item in value]

        return value


class MapCodec(Codec):
    def __init__(self, inner: Callable[[], Codec]) -> None:
        self._inner = inner

    def encode(self, value: Any) -> Any:
        if isinstance(value, dict):
            inner = self._inner()
            return {key: inner.encode(item) for key, item in value.items()}

        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict):
            inner = self._inner()
            return {key: inner.decode(item) for key, item in value.items()}

        return value


class EnumCodec(Codec):
    def __init__(self, enum_class: type[Enum]) -> None:
        self._enum_class = enum_class

    def encode(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value

        return value

    def decode(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        try:
            return self._enum_class(value)
        except ValueError:
            return value


def is_known_enum_value(enum_class: type[Enum], value: str) -> bool:
    try:
        enum_class(value)
    except ValueError:
        return False

    return True


class ScalarOrArrayCodec(Codec):
    def encode(self, value: Any) -> Any:
        if isinstance(value, str):
            return [value]

        if isinstance(value, (list, tuple)):
            return list(value)

        return value

    def decode(self, value: Any) -> Any:
        return value


class MailboxCodec(Codec):
    def encode(self, value: Any) -> Any:
        if isinstance(value, Mailbox):
            if not value.name:
                return value.email

            return {"email": value.email, "name": value.name}

        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict):
            return Mailbox(email=str(value.get("email", "")), name=value.get("name"))

        return value


@dataclass(frozen=True)
class FieldSpec:
    wire: str
    name: str
    codec: Callable[[], Codec]
    required: bool
    nullable: bool


class ModelCodec(Codec):
    def __init__(self, model_class: type[Model], fields: Sequence[FieldSpec]) -> None:
        self._model_class = model_class
        self._fields = fields
        self._known = frozenset(spec.wire for spec in fields)

    def encode(self, value: Any) -> Any:
        if not isinstance(value, self._model_class):
            return value

        out: dict[str, Any] = {}

        for spec in self._fields:
            raw = getattr(value, spec.name, NOT_GIVEN)

            if isinstance(raw, NotGiven):
                continue

            out[spec.wire] = None if raw is None else spec.codec().encode(raw)

        return out

    def decode(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        instance = self._model_class.__new__(self._model_class)

        for spec in self._fields:
            if spec.wire in value:
                raw = value[spec.wire]
                setattr(
                    instance,
                    spec.name,
                    None if raw is None else spec.codec().decode(raw),
                )
            else:
                setattr(instance, spec.name, NOT_GIVEN)

        instance.unknown_fields = {
            key: item for key, item in value.items() if key not in self._known
        }
        instance.response_metadata = None

        return instance


@dataclass(frozen=True)
class VariantSpec:
    discriminator: str
    model_class: type[Model]
    codec: Callable[[], Codec]


class VariantCodec(Codec):
    def __init__(self, variants: Sequence[VariantSpec], name: str) -> None:
        self._variants = variants
        self._name = name

    def encode(self, value: Any) -> Any:
        for variant in self._variants:
            if isinstance(value, variant.model_class):
                return variant.codec().encode(value)

        raise EpostixConfigurationError(f"value does not match any {self._name} variant")

    def decode(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        for variant in self._variants:
            if variant.discriminator in value:
                return variant.codec().decode(value)

        raise EpostixDecodeError(
            f"the server returned a {self._name} shape this version does not recognise"
        )
