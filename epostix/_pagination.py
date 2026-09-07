from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Generic, Iterator, Mapping, Sequence, TypeVar

from ._codec import Codec
from ._errors import EpostixConfigurationError, EpostixPaginationError
from ._model import Model
from ._response import ResponseMetadata
from ._transport import AsyncTransport, OperationSpec, RequestOptions, Transport
from ._types import NotGiven

MAXIMUM_PAGES = 10_000

T = TypeVar("T")


@dataclass
class Page(Generic[T]):
    data: list[T]
    has_more: bool
    next_cursor: str | None
    continuation_unavailable: bool
    response_metadata: ResponseMetadata | None


@dataclass
class Collection(Generic[T]):
    items: list[T]
    truncated: bool
    last_cursor: str | None


@dataclass
class _RawPage(Model):
    data: list[Any] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


class _RawPageCodec(Codec):
    def encode(self, value: Any) -> Any:
        return value

    def decode(self, value: Any) -> Any:
        page = _RawPage.__new__(_RawPage)
        page.unknown_fields = {}
        page.response_metadata = None

        if not isinstance(value, dict):
            page.data = []
            page.has_more = False
            page.next_cursor = None

            return page

        raw_items = value.get("data")
        raw_cursor = value.get("next_cursor")

        page.data = raw_items if isinstance(raw_items, list) else []
        page.has_more = value.get("has_more") is True
        page.next_cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None

        return page


_RAW_PAGE = _RawPageCodec()


def _freeze_filters(query: Mapping[str, Any] | None) -> dict[str, Any]:
    if query is None:
        return {}

    return {
        key: value
        for key, value in query.items()
        if value is not None and not isinstance(value, NotGiven)
    }


def _reject_backward_cursor(
    spec: OperationSpec, filters: Mapping[str, Any], backward_cursor_param: str | None
) -> None:
    if backward_cursor_param is None:
        return

    if filters.get(backward_cursor_param) is None:
        return

    raise EpostixConfigurationError(
        f"{spec.id}: {backward_cursor_param} cannot be used with an iterator,"
        " which walks forward only. Use the single-page method instead."
    )


def _page_of(raw: _RawPage, items: list[T]) -> Page[T]:
    return Page(
        data=items,
        has_more=raw.has_more,
        next_cursor=raw.next_cursor,
        continuation_unavailable=raw.has_more and raw.next_cursor is None,
        response_metadata=raw.response_metadata,
    )


def _advance(spec: OperationSpec, raw: _RawPage, cursor: str | None) -> str:
    if raw.next_cursor is None:
        raise EpostixPaginationError(
            "MissingCursor",
            spec.id,
            f"{spec.id}: the server reported more results but returned no cursor",
        )

    if cursor is not None and raw.next_cursor == cursor:
        raise EpostixPaginationError(
            "RepeatedCursor",
            spec.id,
            f"{spec.id}: the server returned the same cursor it was given",
        )

    return raw.next_cursor


def _check_page_budget(spec: OperationSpec, page_number: int) -> None:
    if page_number > MAXIMUM_PAGES:
        raise EpostixPaginationError(
            "PageLimitExceeded",
            spec.id,
            f"{spec.id}: stopped after {MAXIMUM_PAGES} pages",
        )


def _check_cap(maximum_items: int) -> None:
    if maximum_items <= 0:
        raise EpostixConfigurationError("collect requires a positive maximum_items")


class Paginator(Generic[T]):
    def __init__(
        self,
        transport: Transport,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        item_codec: Codec,
        cursor_param: str | None,
        backward_cursor_param: str | None = None,
        options: RequestOptions | None = None,
    ) -> None:
        self._transport = transport
        self._spec = spec
        self._path = tuple(path)
        self._filters = _freeze_filters(query)
        self._item_codec = item_codec
        self._cursor_param = cursor_param or "starting_after"
        self._options = options

        _reject_backward_cursor(spec, self._filters, backward_cursor_param)

    def iterate_pages(self) -> Iterator[Page[T]]:
        cursor: str | None = None
        page_number = 0

        while True:
            page_number += 1
            _check_page_budget(self._spec, page_number)

            query = dict(self._filters)
            if cursor is not None:
                query[self._cursor_param] = cursor

            raw: _RawPage = self._transport.execute(
                self._spec,
                path=self._path,
                query=query,
                decode=_RAW_PAGE,
                options=self._options,
            )

            items: list[T] = [self._item_codec.decode(item) for item in raw.data]
            yield _page_of(raw, items)

            if not raw.has_more:
                return

            cursor = _advance(self._spec, raw, cursor)

    def iterate(self) -> Iterator[T]:
        for page in self.iterate_pages():
            for item in page.data:
                yield item

    def __iter__(self) -> Iterator[T]:
        return self.iterate()

    def collect(self, maximum_items: int) -> Collection[T]:
        _check_cap(maximum_items)

        items: list[T] = []
        last_cursor: str | None = None

        for page in self.iterate_pages():
            last_cursor = page.next_cursor

            for item in page.data:
                if len(items) >= maximum_items:
                    return Collection(items=items, truncated=True, last_cursor=last_cursor)
                items.append(item)

            if len(items) >= maximum_items and page.has_more:
                return Collection(items=items, truncated=True, last_cursor=last_cursor)

        return Collection(items=items, truncated=False, last_cursor=last_cursor)


class AsyncPaginator(Generic[T]):
    def __init__(
        self,
        transport: AsyncTransport,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        item_codec: Codec,
        cursor_param: str | None,
        backward_cursor_param: str | None = None,
        options: RequestOptions | None = None,
    ) -> None:
        self._transport = transport
        self._spec = spec
        self._path = tuple(path)
        self._filters = _freeze_filters(query)
        self._item_codec = item_codec
        self._cursor_param = cursor_param or "starting_after"
        self._options = options

        _reject_backward_cursor(spec, self._filters, backward_cursor_param)

    async def iterate_pages(self) -> AsyncIterator[Page[T]]:
        cursor: str | None = None
        page_number = 0

        while True:
            page_number += 1
            _check_page_budget(self._spec, page_number)

            query = dict(self._filters)
            if cursor is not None:
                query[self._cursor_param] = cursor

            raw: _RawPage = await self._transport.execute(
                self._spec,
                path=self._path,
                query=query,
                decode=_RAW_PAGE,
                options=self._options,
            )

            items: list[T] = [self._item_codec.decode(item) for item in raw.data]
            yield _page_of(raw, items)

            if not raw.has_more:
                return

            cursor = _advance(self._spec, raw, cursor)

    async def iterate(self) -> AsyncIterator[T]:
        async for page in self.iterate_pages():
            for item in page.data:
                yield item

    def __aiter__(self) -> AsyncIterator[T]:
        return self.iterate()

    async def collect(self, maximum_items: int) -> Collection[T]:
        _check_cap(maximum_items)

        items: list[T] = []
        last_cursor: str | None = None

        async for page in self.iterate_pages():
            last_cursor = page.next_cursor

            for item in page.data:
                if len(items) >= maximum_items:
                    return Collection(items=items, truncated=True, last_cursor=last_cursor)
                items.append(item)

            if len(items) >= maximum_items and page.has_more:
                return Collection(items=items, truncated=True, last_cursor=last_cursor)

        return Collection(items=items, truncated=False, last_cursor=last_cursor)
