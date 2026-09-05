"""Small direct client for the Strava API endpoints used by this pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx

API_BASE_URL = "https://www.strava.com/api/v3"
PER_PAGE = 200
STREAM_KEYS = ("latlng", "distance", "time", "altitude")
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})


class StravaAPIError(RuntimeError):
    """A Strava API request failed or returned an invalid representation."""


class StravaScopeError(StravaAPIError):
    """The access token lacks read-only activity access."""


class StravaClient:
    def __init__(
        self,
        access_token: Callable[[], str],
        *,
        refresh_access_token: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ) -> None:
        self._access_token = access_token
        self._refresh_access_token = refresh_access_token
        self._client = client or httpx.Client(timeout=20.0)
        self._sleep = sleep
        self._max_retries = max_retries

    def _get(self, path: str, *, params: Mapping[str, object]) -> Any:
        attempt = 0
        retried_authentication = False
        while True:
            try:
                response = self._client.get(
                    f"{API_BASE_URL}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._access_token()}"},
                )
            except httpx.HTTPError as error:
                if attempt == self._max_retries:
                    raise StravaAPIError(
                        f"Strava request failed: GET {path}"
                    ) from error
                self._sleep(0.5 * 2**attempt)
                attempt += 1
                continue

            if (
                response.status_code == 401
                and self._refresh_access_token is not None
                and not retried_authentication
            ):
                self._refresh_access_token()
                retried_authentication = True
                continue
            if response.status_code == 403 and (
                path == "/athlete/activities" or path.startswith("/activities/")
            ):
                raise StravaScopeError(
                    "Strava authorization is missing read-only scope "
                    "activity:read_all; reauthorize the application with "
                    "activity:read_all"
                )
            if response.status_code == 429:
                usage = response.headers.get(
                    "X-ReadRateLimit-Usage"
                ) or response.headers.get("X-RateLimit-Usage", "unknown")
                limit = response.headers.get(
                    "X-ReadRateLimit-Limit"
                ) or response.headers.get("X-RateLimit-Limit", "unknown")
                raise StravaAPIError(
                    f"Strava rate limit reached (usage {usage}; limit {limit})"
                )
            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                self._sleep(0.5 * 2**attempt)
                attempt += 1
                continue
            try:
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise StravaAPIError(
                    f"Strava API returned HTTP {response.status_code} for GET {path}"
                ) from error

    def get_authenticated_athlete(self) -> dict[str, Any]:
        """Verify that the access token authenticates a read-only API request."""
        payload = self._get("/athlete", params={})
        if not isinstance(payload, dict):
            raise StravaAPIError("Strava athlete response was not a JSON object")
        return payload

    def iter_activities(
        self, *, after: int, before: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Page through all athlete activities in a fixed UTC time window."""
        page = 1
        while True:
            params: dict[str, object] = {
                "after": after,
                "page": page,
                "per_page": PER_PAGE,
            }
            if before is not None:
                params["before"] = before
            payload = self._get("/athlete/activities", params=params)
            if not isinstance(payload, list):
                raise StravaAPIError("Strava activity list was not a JSON array")
            if not payload:
                return
            for activity in payload:
                if not isinstance(activity, dict):
                    raise StravaAPIError("Strava activity list contained a non-object")
                yield activity
            # Strava may return short non-final pages, so only an empty page ends
            # pagination (per the official V3 API pagination guidance).
            page += 1

    def get_activity(self, activity_id: int) -> dict[str, Any]:
        """Retrieve DetailedActivity without segment efforts; not used by default."""
        payload = self._get(
            f"/activities/{activity_id}", params={"include_all_efforts": "false"}
        )
        if not isinstance(payload, dict):
            raise StravaAPIError("Strava detailed activity was not a JSON object")
        return payload

    def get_activity_streams(self, activity_id: int) -> Any:
        """Retrieve only streams useful for later route/geographic analysis."""
        return self._get(
            f"/activities/{activity_id}/streams",
            params={"keys": ",".join(STREAM_KEYS), "key_by_type": "true"},
        )


__all__ = [
    "API_BASE_URL",
    "PER_PAGE",
    "STREAM_KEYS",
    "StravaAPIError",
    "StravaClient",
    "StravaScopeError",
]
