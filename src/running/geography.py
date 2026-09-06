"""Cached BigDataCloud reverse geocoding for private run coordinates."""

from __future__ import annotations

import csv
import io
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from running.normalize import Run

BIGDATACLOUD_URL = "https://api-bdc.net/data/reverse-geocode"
CACHE_COLUMNS = (
    "activity_id",
    "latitude",
    "longitude",
    "city",
    "locality",
    "postcode",
    "state",
    "state_code",
    "country",
    "country_code",
    "continent",
    "continent_code",
    "timezone",
    "provider_payload_json",
    "provider",
    "geocoded_at",
)
REQUIRED_CACHE_COLUMNS = frozenset(
    {
        "activity_id",
        "latitude",
        "longitude",
        "city",
        "state",
        "country",
        "country_code",
        "provider",
        "geocoded_at",
    }
)
COORDINATE_DIGITS = 6
PROVIDER = "bigdatacloud"
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})


class GeocodingError(RuntimeError):
    """Reverse geocoding cannot safely produce complete public data."""


@dataclass(frozen=True)
class Location:
    city: str | None
    state: str | None
    country: str | None
    country_code: str | None
    locality: str | None = None
    postcode: str | None = None
    state_code: str | None = None
    continent: str | None = None
    continent_code: str | None = None
    timezone: str | None = None
    provider_payload: Mapping[str, Any] | None = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True)
class CacheEntry:
    activity_id: int
    latitude: float
    longitude: float
    location: Location
    geocoded_at: str


@dataclass(frozen=True)
class GeocodingStats:
    total_runs: int
    valid_coordinates: int
    cache_hits: int
    public_hits: int
    api_requests: int
    city_enriched: int
    state_enriched: int
    city_missing: int


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _coordinate(value: float) -> float:
    return round(value, COORDINATE_DIGITS)


def _same_coordinates(entry: CacheEntry, latitude: float, longitude: float) -> bool:
    return entry.latitude == _coordinate(latitude) and entry.longitude == _coordinate(
        longitude
    )


def parse_bigdatacloud_response(payload: Mapping[str, Any]) -> Location:
    """Map provider fields to the project's stable location vocabulary."""
    country_code = _text(payload.get("countryCode"))
    if country_code is not None:
        country_code = country_code.upper() if len(country_code) == 2 else None
    locality = _text(payload.get("locality"))
    timezone = next(
        (
            _text(item.get("name"))
            for item in payload.get("localityInfo", {}).get("informative", [])
            if isinstance(item, dict) and item.get("description") == "time zone"
        ),
        None,
    )
    return Location(
        # BigDataCloud explicitly recommends locality as the fallback for city.
        city=_text(payload.get("city")) or locality,
        state=_text(payload.get("principalSubdivision")) or _text(payload.get("region")),
        country=_text(payload.get("countryName")) or _text(payload.get("country")),
        country_code=country_code,
        locality=locality,
        postcode=_text(payload.get("postcode")),
        state_code=_text(payload.get("principalSubdivisionCode")),
        continent=_text(payload.get("continent")),
        continent_code=_text(payload.get("continentCode")),
        timezone=timezone,
        provider_payload=dict(payload),
    )


class BigDataCloudGeocoder:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise GeocodingError("BIGDATACLOUD_API_KEY is required")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20.0)
        self._sleep = sleep
        self._max_retries = max_retries

    def reverse(self, latitude: float, longitude: float) -> Location:
        attempt = 0
        while True:
            try:
                response = self._client.get(
                    BIGDATACLOUD_URL,
                    params={
                        "latitude": f"{_coordinate(latitude):.{COORDINATE_DIGITS}f}",
                        "longitude": f"{_coordinate(longitude):.{COORDINATE_DIGITS}f}",
                        "localityLanguage": "en",
                    },
                    # A header prevents the key from appearing in URLs or errors.
                    headers={"x-bdc-key": self._api_key},
                )
            except httpx.HTTPError as error:
                if attempt == self._max_retries:
                    raise GeocodingError(
                        "BigDataCloud reverse geocoding request failed"
                    ) from error
                self._sleep(0.5 * 2**attempt)
                attempt += 1
                continue

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                self._sleep(0.5 * 2**attempt)
                attempt += 1
                continue
            if response.status_code == 402:
                raise GeocodingError("BigDataCloud monthly API quota was exceeded")
            if response.status_code in {401, 403}:
                raise GeocodingError(
                    "BigDataCloud API key is invalid or lacks reverse-geocoding access"
                )
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise GeocodingError(
                    f"BigDataCloud reverse geocoding returned HTTP "
                    f"{response.status_code}"
                ) from error
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as error:
                raise GeocodingError(
                    "BigDataCloud reverse geocoding returned malformed JSON"
                ) from error
            if not isinstance(payload, dict):
                raise GeocodingError(
                    "BigDataCloud reverse geocoding returned malformed JSON"
                )
            return parse_bigdatacloud_response(payload)


class GeocodingCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries = self._load()
        self.dirty = False

    def _load(self) -> dict[int, CacheEntry]:
        if not self.path.is_file():
            return {}
        try:
            with self.path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                missing = REQUIRED_CACHE_COLUMNS - set(reader.fieldnames or ())
                if missing:
                    raise ValueError("missing cache columns")
                entries: dict[int, CacheEntry] = {}
                for row in reader:
                    if row["provider"] != PROVIDER:
                        continue
                    activity_id = int(row["activity_id"])
                    if activity_id in entries:
                        raise ValueError("duplicate cache activity ID")
                    entries[activity_id] = CacheEntry(
                        activity_id=activity_id,
                        latitude=_coordinate(float(row["latitude"])),
                        longitude=_coordinate(float(row["longitude"])),
                        location=Location(
                            city=_text(row["city"]),
                            state=_text(row["state"]),
                            country=_text(row["country"]),
                            country_code=_text(row["country_code"]),
                            locality=_text(row.get("locality")),
                            postcode=_text(row.get("postcode")),
                            state_code=_text(row.get("state_code")),
                            continent=_text(row.get("continent")),
                            continent_code=_text(row.get("continent_code")),
                            timezone=_text(row.get("timezone")),
                            provider_payload=(
                                json.loads(row["provider_payload_json"])
                                if _text(row.get("provider_payload_json"))
                                else None
                            ),
                        ),
                        geocoded_at=row["geocoded_at"],
                    )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise GeocodingError(
                f"invalid private geocoding cache at {self.path}"
            ) from error
        return entries

    def lookup(
        self, activity_id: int, latitude: float, longitude: float
    ) -> Location | None:
        entry = self._entries.get(activity_id)
        if entry is not None and _same_coordinates(entry, latitude, longitude):
            return entry.location
        return next(
            (
                candidate.location
                for candidate in self._entries.values()
                if _same_coordinates(candidate, latitude, longitude)
            ),
            None,
        )

    def contains(self, activity_id: int) -> bool:
        """Return whether an activity has any provider cache entry."""
        return activity_id in self._entries

    def entry(self, activity_id: int) -> CacheEntry | None:
        """Return a private cache entry by activity ID."""
        return self._entries.get(activity_id)

    def update(
        self,
        activity_id: int,
        latitude: float,
        longitude: float,
        location: Location,
        *,
        geocoded_at: str,
    ) -> None:
        self._entries[activity_id] = CacheEntry(
            activity_id=activity_id,
            latitude=_coordinate(latitude),
            longitude=_coordinate(longitude),
            location=location,
            geocoded_at=geocoded_at,
        )
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CACHE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for entry in sorted(self._entries.values(), key=lambda item: item.activity_id):
            writer.writerow(
                {
                    "activity_id": entry.activity_id,
                    "latitude": f"{entry.latitude:.{COORDINATE_DIGITS}f}",
                    "longitude": f"{entry.longitude:.{COORDINATE_DIGITS}f}",
                    "city": entry.location.city or "",
                    "locality": entry.location.locality or "",
                    "postcode": entry.location.postcode or "",
                    "state": entry.location.state or "",
                    "state_code": entry.location.state_code or "",
                    "country": entry.location.country or "",
                    "country_code": entry.location.country_code or "",
                    "continent": entry.location.continent or "",
                    "continent_code": entry.location.continent_code or "",
                    "timezone": entry.location.timezone or "",
                    "provider_payload_json": (
                        json.dumps(
                            entry.location.provider_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        if entry.location.provider_payload is not None
                        else ""
                    ),
                    "provider": PROVIDER,
                    "geocoded_at": entry.geocoded_at,
                }
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(output.getvalue())
            temporary.replace(self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        self.dirty = False


def _has_public_location(run: Run) -> bool:
    return any(
        (
            run.start_city,
            run.start_locality,
            run.start_state,
            run.start_state_code,
            run.start_country,
            run.start_country_code,
        )
    )


def _with_location(run: Run, location: Location) -> Run:
    return replace(
        run,
        start_city=location.city or run.start_city,
        start_locality=location.locality or run.start_locality,
        start_state=location.state or run.start_state,
        start_state_code=location.state_code or run.start_state_code,
        start_country=location.country or run.start_country,
        start_country_code=location.country_code or run.start_country_code,
        timezone=location.timezone or run.timezone,
    )


def enrich_runs(
    runs: Iterable[Run],
    *,
    cache_path: Path,
    api_key: str | None,
    geocoder: BigDataCloudGeocoder | None = None,
    refresh_cached_metadata: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[list[Run], GeocodingStats]:
    """Apply cached/public locations and geocode only genuinely missing runs."""
    runs = list(runs)
    cache = GeocodingCache(cache_path)
    enriched: list[Run] = []
    cache_hits = public_hits = api_requests = valid_coordinates = 0
    active_geocoder = geocoder

    try:
        if refresh_cached_metadata:
            if active_geocoder is None:
                if not api_key:
                    raise GeocodingError(
                        "BIGDATACLOUD_API_KEY is required to refresh cached metadata"
                    )
                active_geocoder = BigDataCloudGeocoder(api_key)
            targets: dict[tuple[float, float], list[CacheEntry]] = {}
            for run in runs:
                entry = cache.entry(run.activity_id)
                if entry is not None:
                    targets.setdefault((entry.latitude, entry.longitude), []).append(
                        entry
                    )
            coordinates = sorted(targets)
            if coordinates:
                with ThreadPoolExecutor(max_workers=min(8, len(coordinates))) as executor:
                    locations = executor.map(
                        lambda point: active_geocoder.reverse(*point),  # type: ignore[union-attr]
                        coordinates,
                    )
                    refreshed_at = now().isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    )
                    for coordinate, location in zip(
                        coordinates, locations, strict=True
                    ):
                        for entry in targets[coordinate]:
                            cache.update(
                                entry.activity_id,
                                entry.latitude,
                                entry.longitude,
                                location,
                                geocoded_at=refreshed_at,
                            )
                        api_requests += 1

        for run in runs:
            cached_entry = cache.entry(run.activity_id)
            if run.start_lat is None or run.start_lon is None:
                if cached_entry is None:
                    enriched.append(run)
                    continue
                cache_hits += 1
                enriched.append(_with_location(run, cached_entry.location))
                continue
            valid_coordinates += 1
            location = cache.lookup(run.activity_id, run.start_lat, run.start_lon)
            if location is not None:
                cache_hits += 1
                enriched.append(_with_location(run, location))
                continue
            # Public location columns are the durable safe cache for CI runners,
            # which intentionally do not persist precise private coordinates.
            # A mismatched private entry proves the start changed, so stale
            # public fields must not suppress a new provider lookup.
            if not cache.contains(run.activity_id) and _has_public_location(run):
                public_hits += 1
                enriched.append(run)
                continue
            if active_geocoder is None:
                if not api_key:
                    raise GeocodingError(
                        "BIGDATACLOUD_API_KEY is required for uncached run "
                        f"{run.activity_id}"
                    )
                active_geocoder = BigDataCloudGeocoder(api_key)
            location = active_geocoder.reverse(run.start_lat, run.start_lon)
            api_requests += 1
            cache.update(
                run.activity_id,
                run.start_lat,
                run.start_lon,
                location,
                geocoded_at=now().isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            enriched.append(_with_location(run, location))
    finally:
        cache.save()

    return enriched, GeocodingStats(
        total_runs=len(enriched),
        valid_coordinates=valid_coordinates,
        cache_hits=cache_hits,
        public_hits=public_hits,
        api_requests=api_requests,
        city_enriched=sum(run.start_city is not None for run in enriched),
        state_enriched=sum(run.start_state is not None for run in enriched),
        city_missing=sum(run.start_city is None for run in enriched),
    )


__all__ = [
    "BIGDATACLOUD_URL",
    "CACHE_COLUMNS",
    "COORDINATE_DIGITS",
    "PROVIDER",
    "BigDataCloudGeocoder",
    "GeocodingCache",
    "GeocodingError",
    "GeocodingStats",
    "Location",
    "enrich_runs",
    "parse_bigdatacloud_response",
]
