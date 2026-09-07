from __future__ import annotations

from typing import Any

import pytest
from conftest import Clients, Recorder, load_fixture, pages

from epostix import EpostixConfigurationError, EpostixPaginationError

FIXTURE = load_fixture("pagination")
CASES = FIXTURE["cases"]


def _bodies(case: dict[str, Any]) -> list[Any]:
    return [page["body"] for page in case["pages"]]


def _expected_query(page: dict[str, Any]) -> dict[str, list[str]]:
    return {key: [value] for key, value in page["query"].items()}


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["name"]))
def test_sync_iteration_matches_the_shared_corpus(clients: Clients, case: dict[str, Any]) -> None:
    expect = case["expect"]
    recorder = Recorder(pages(_bodies(case)))
    client = clients.sync(recorder)
    paginator = client.emails.list_emails_iterate(**case["params"])

    if "error" in expect:
        with pytest.raises(EpostixPaginationError) as raised:
            list(paginator)

        assert raised.value.reason == expect["error"]
        return

    if "maximumItems" in case:
        collected = paginator.collect(case["maximumItems"])
        assert [item.id for item in collected.items] == expect["items"]
        assert collected.truncated is expect["truncated"]
        assert recorder.count == expect["requests"]
        return

    seen: list[str] = []
    for item in paginator:
        seen.append(item.id)
        if "breakAfter" in case and len(seen) >= case["breakAfter"]:
            break

    assert seen == expect["items"]
    assert recorder.count == expect["requests"]

    for index, page in enumerate(case["pages"]):
        if "query" in page and index < recorder.count:
            assert recorder.query_of(index) == _expected_query(page)


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["name"]))
async def test_async_iteration_matches_the_shared_corpus(
    clients: Clients, case: dict[str, Any]
) -> None:
    expect = case["expect"]
    recorder = Recorder(pages(_bodies(case)))
    client = clients.asynchronous(recorder)
    paginator = client.emails.list_emails_iterate(**case["params"])

    if "error" in expect:
        with pytest.raises(EpostixPaginationError) as raised:
            async for _ in paginator:
                pass

        assert raised.value.reason == expect["error"]
        await client.aclose()
        return

    if "maximumItems" in case:
        collected = await paginator.collect(case["maximumItems"])
        assert [item.id for item in collected.items] == expect["items"]
        assert collected.truncated is expect["truncated"]
        assert recorder.count == expect["requests"]
        await client.aclose()
        return

    seen: list[str] = []
    async for item in paginator:
        seen.append(item.id)
        if "breakAfter" in case and len(seen) >= case["breakAfter"]:
            break

    assert seen == expect["items"]
    assert recorder.count == expect["requests"]

    await client.aclose()


def test_filters_are_frozen_at_construction_and_persist(clients: Clients) -> None:
    recorder = Recorder(
        pages(
            [
                {"data": [{"id": "e1"}], "has_more": True, "next_cursor": "e1"},
                {"data": [{"id": "e2"}], "has_more": False},
            ]
        )
    )
    client = clients.sync(recorder)

    list(client.emails.list_emails_iterate(status="delivered", limit=1))

    assert recorder.count == 2
    assert recorder.query_of(1)["status"] == ["delivered"]
    assert recorder.query_of(1)["limit"] == ["1"]
    assert recorder.query_of(1)["starting_after"] == ["e1"]


def test_an_iterator_refuses_the_backward_cursor(clients: Clients) -> None:
    client = clients.sync(Recorder(pages([{"data": [], "has_more": False}])))

    with pytest.raises(EpostixConfigurationError):
        client.emails.list_emails_iterate(ending_before="e9")


def test_collect_requires_a_positive_cap(clients: Clients) -> None:
    recorder = Recorder(pages([{"data": [{"id": "e1"}], "has_more": False}]))
    client = clients.sync(recorder)

    with pytest.raises(EpostixConfigurationError):
        client.emails.list_emails_iterate().collect(0)

    assert recorder.count == 0


def test_iterate_pages_exposes_page_level_detail(clients: Clients) -> None:
    recorder = Recorder(
        pages(
            [
                {"data": [{"id": "e1"}], "has_more": True, "next_cursor": "c1"},
                {"data": [{"id": "e2"}], "has_more": False},
            ]
        )
    )
    client = clients.sync(recorder)

    collected = list(client.emails.list_emails_iterate().iterate_pages())

    assert len(collected) == 2
    assert collected[0].has_more is True
    assert collected[0].next_cursor == "c1"
    assert collected[0].continuation_unavailable is False
    assert collected[1].has_more is False
    assert collected[0].response_metadata is not None


def test_unknown_fields_never_fail_a_page(clients: Clients) -> None:
    recorder = Recorder(
        pages([{"data": [{"id": "e1", "future_field": 42}], "has_more": False, "future": True}])
    )
    client = clients.sync(recorder)

    items = list(client.emails.list_emails_iterate())

    assert [item.id for item in items] == ["e1"]


def test_a_nested_resource_paginates_forward(clients: Clients) -> None:
    recorder = Recorder(
        pages(
            [
                {"data": [{"version": 3}], "has_more": True, "next_cursor": "3"},
                {"data": [{"version": 2}], "has_more": False},
            ]
        )
    )
    client = clients.sync(recorder)

    items = list(client.templates.versions.list_template_versions_iterate("tpl_1"))

    assert len(items) == 2
    assert recorder.query_of(1)["starting_after"] == ["3"]


def test_a_repeated_cursor_is_caught_on_the_request_that_repeats_it(clients: Clients) -> None:
    recorder = Recorder(
        pages([{"data": [{"id": "e1"}], "has_more": True, "next_cursor": "c1"}])
    )
    client = clients.sync(recorder)

    with pytest.raises(EpostixPaginationError) as raised:
        list(client.emails.list_emails_iterate())

    assert raised.value.reason == "RepeatedCursor"
    assert "the same cursor it was given" in str(raised.value)
    assert recorder.count == 2


async def test_the_async_iterator_catches_a_repeated_cursor_just_as_early(
    clients: Clients,
) -> None:
    recorder = Recorder(
        pages([{"data": [{"id": "e1"}], "has_more": True, "next_cursor": "c1"}])
    )
    client = clients.asynchronous(recorder)

    with pytest.raises(EpostixPaginationError) as raised:
        async for _ in client.emails.list_emails_iterate():
            pass

    assert raised.value.reason == "RepeatedCursor"
    assert recorder.count == 2

    await client.aclose()
