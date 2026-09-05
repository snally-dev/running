from __future__ import annotations

import httpx
import pytest

from running.strava.api import (
    PER_PAGE,
    STREAM_KEYS,
    StravaAPIError,
    StravaClient,
    StravaScopeError,
)


def _client(handler: httpx.MockTransport) -> StravaClient:
    return StravaClient(
        lambda: "access-token",
        client=httpx.Client(transport=handler, timeout=1.0),
        sleep=lambda _: None,
        max_retries=0,
    )


def test_one_page_activity_listing_continues_to_empty_page() -> None:
    pages: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.url.params["after"] == "100"
        assert request.url.params["before"] == "200"
        assert request.url.params["per_page"] == str(PER_PAGE)
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(200, json=[{"id": 1}] if page == 1 else [])

    activities = list(
        _client(httpx.MockTransport(respond)).iter_activities(after=100, before=200)
    )
    assert activities == [{"id": 1}]
    assert pages == [1, 2]


def test_paginated_activity_listing_does_not_stop_on_short_page() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        payload = {1: [{"id": 1}], 2: [{"id": 2}], 3: []}[page]
        return httpx.Response(200, json=payload)

    activities = list(_client(httpx.MockTransport(respond)).iter_activities(after=0))
    assert [activity["id"] for activity in activities] == [1, 2]


def test_api_failure_is_clear() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "broken"})
    )
    with pytest.raises(StravaAPIError, match="HTTP 500"):
        list(_client(transport).iter_activities(after=0))


def test_rate_limit_failure_reports_headers() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429,
            headers={
                "X-ReadRateLimit-Usage": "100,500",
                "X-ReadRateLimit-Limit": "100,1000",
            },
        )
    )
    with pytest.raises(StravaAPIError, match="usage 100,500; limit 100,1000"):
        list(_client(transport).iter_activities(after=0))


def test_insufficient_activity_scope_is_clear() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    with pytest.raises(StravaScopeError, match="activity:read_all"):
        list(_client(transport).iter_activities(after=0))


def test_unauthorized_access_token_is_refreshed_once() -> None:
    requests = 0
    refreshes = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401 if requests == 1 else 200, json={"id": 1})

    def refresh() -> str:
        nonlocal refreshes
        refreshes += 1
        return "new-access"

    client = StravaClient(
        lambda: "access-token",
        refresh_access_token=refresh,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    assert client.get_authenticated_athlete() == {"id": 1}
    assert requests == 2
    assert refreshes == 1


def test_activity_stream_request_uses_only_analytical_streams() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/activities/123/streams")
        assert request.url.params["keys"] == ",".join(STREAM_KEYS)
        assert request.url.params["key_by_type"] == "true"
        return httpx.Response(200, json={"latlng": {"data": [[1, 2]]}})

    payload = _client(httpx.MockTransport(respond)).get_activity_streams(123)
    assert payload["latlng"]["data"] == [[1, 2]]


def test_detailed_activity_explicitly_excludes_segment_efforts() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/activities/123")
        assert request.url.params["include_all_efforts"] == "false"
        return httpx.Response(200, json={"id": 123})

    assert _client(httpx.MockTransport(respond)).get_activity(123) == {"id": 123}
