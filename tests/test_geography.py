from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from running.build import write_runs
from running.geography import (
    BigDataCloudGeocoder,
    GeocodingCache,
    GeocodingError,
    Location,
    enrich_runs,
    parse_bigdatacloud_response,
)
from running.normalize import Run


def _run(**changes: object) -> Run:
    values: dict[str, object] = {
        "activity_id": 1,
        "start_datetime": datetime(2024, 1, 2, tzinfo=UTC),
        "name": "Morning Run",
        "distance_m": 5000.0,
        "moving_time_s": 1500,
        "elapsed_time_s": 1600,
        "elevation_gain_m": None,
        "average_heart_rate_bpm": None,
        "max_heart_rate_bpm": None,
        "calories": None,
        "start_lat": 38.1234564,
        "start_lon": -77.1234564,
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def _cache(
    path: Path,
    *,
    latitude: float = 38.1234564,
    longitude: float = -77.1234564,
    location: Location | None = None,
) -> None:
    location = location or Location("Frederick", "Maryland", "United States", "US")
    cache = GeocodingCache(path)
    cache.update(
        1,
        latitude,
        longitude,
        location,
        geocoded_at="2024-01-01T00:00:00Z",
    )
    cache.save()


class FakeGeocoder:
    def __init__(self, location: Location) -> None:
        self.location = location
        self.calls: list[tuple[float, float]] = []

    def reverse(self, latitude: float, longitude: float) -> Location:
        self.calls.append((latitude, longitude))
        return self.location


def test_successful_city_state_and_country_mapping() -> None:
    location = parse_bigdatacloud_response(
        {
            "city": "Frederick",
            "locality": "Downtown",
            "principalSubdivision": "Maryland",
            "principalSubdivisionCode": "US-MD",
            "countryName": "United States",
            "countryCode": "us",
            "continent": "North America",
            "continentCode": "NA",
            "postcode": "21701",
            "localityInfo": {
                "informative": [
                    {"name": "America/New_York", "description": "time zone"}
                ]
            },
        }
    )
    assert location.city == "Frederick"
    assert location.locality == "Downtown"
    assert location.postcode == "21701"
    assert location.state == "Maryland"
    assert location.state_code == "US-MD"
    assert location.country_code == "US"
    assert location.continent_code == "NA"
    assert location.timezone == "America/New_York"
    assert location.provider_payload is not None


def test_locality_is_used_only_when_city_is_missing() -> None:
    location = parse_bigdatacloud_response(
        {
            "city": "",
            "locality": "Ballenger Creek",
            "principalSubdivision": "Maryland",
        }
    )
    assert location.city == "Ballenger Creek"


def test_compact_response_aliases_are_supported() -> None:
    location = parse_bigdatacloud_response(
        {
            "city": "Walkersville",
            "locality": "Walkersville",
            "region": "Maryland",
            "country": "United States of America",
            "countryCode": "US",
            "postcode": "21793",
            "continent": "North America",
        }
    )
    assert location.state == "Maryland"
    assert location.country == "United States of America"
    assert location.postcode == "21793"


def test_missing_city_and_locality_remain_empty() -> None:
    assert (
        parse_bigdatacloud_response({"principalSubdivision": "Maryland"}).city is None
    )


def test_run_without_coordinates_needs_no_key(tmp_path: Path) -> None:
    runs, stats = enrich_runs(
        [_run(start_lat=None, start_lon=None)],
        cache_path=tmp_path / "cache.csv",
        api_key=None,
    )
    assert runs[0].start_city is None
    assert stats.valid_coordinates == stats.api_requests == 0


def test_cache_hit_makes_no_api_request(tmp_path: Path) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    geocoder = FakeGeocoder(Location(None, None, None, None))
    runs, stats = enrich_runs(
        [_run()],
        cache_path=path,
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
    )
    assert runs[0].start_city == "Frederick"
    assert stats.cache_hits == 1
    assert stats.api_requests == 0
    assert geocoder.calls == []


def test_cache_miss_performs_one_api_request(tmp_path: Path) -> None:
    geocoder = FakeGeocoder(Location("Frederick", "Maryland", "United States", "US"))
    runs, stats = enrich_runs(
        [_run()],
        cache_path=tmp_path / "cache.csv",
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
    )
    assert runs[0].start_state == "Maryland"
    assert stats.api_requests == 1
    assert geocoder.calls == [(38.1234564, -77.1234564)]


def test_cached_metadata_can_be_refreshed(tmp_path: Path) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    location = Location(
        "Frederick",
        "Maryland",
        "United States",
        "US",
        locality="Downtown",
        postcode="21701",
        state_code="US-MD",
        continent="North America",
        continent_code="NA",
        timezone="America/New_York",
    )
    geocoder = FakeGeocoder(location)

    runs, stats = enrich_runs(
        [_run(start_lat=None, start_lon=None)],
        cache_path=path,
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
        refresh_cached_metadata=True,
    )

    assert geocoder.calls == [(38.123456, -77.123456)]
    assert stats.api_requests == 1
    assert runs[0].start_locality == "Downtown"
    assert runs[0].start_state_code == "US-MD"
    assert runs[0].timezone == "America/New_York"
    cached = GeocodingCache(path).entry(1)
    assert cached is not None
    assert cached.location.postcode == "21701"
    assert cached.location.continent_code == "NA"


def test_same_coordinates_are_geocoded_only_once(tmp_path: Path) -> None:
    geocoder = FakeGeocoder(Location("Frederick", "Maryland", "United States", "US"))
    runs, stats = enrich_runs(
        [_run(activity_id=1), _run(activity_id=2)],
        cache_path=tmp_path / "cache.csv",
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
    )
    assert [run.start_city for run in runs] == ["Frederick", "Frederick"]
    assert stats.api_requests == 1
    assert stats.cache_hits == 1
    assert geocoder.calls == [(38.1234564, -77.1234564)]


def test_changed_coordinates_invalidate_cache(tmp_path: Path) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    geocoder = FakeGeocoder(Location("Richmond", "Virginia", "United States", "US"))
    runs, stats = enrich_runs(
        [_run(start_lat=37.5407, start_lon=-77.4360)],
        cache_path=path,
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
    )
    assert runs[0].start_city == "Richmond"
    assert stats.cache_hits == 0
    assert stats.api_requests == 1


def test_changed_coordinates_do_not_reuse_stale_public_location(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    changed = _run(
        start_lat=37.5407,
        start_lon=-77.4360,
        start_city="Frederick",
        start_state="Maryland",
        start_country="United States",
        start_country_code="US",
    )
    geocoder = FakeGeocoder(Location("Richmond", "Virginia", "United States", "US"))

    runs, stats = enrich_runs(
        [changed],
        cache_path=path,
        api_key=None,
        geocoder=geocoder,  # type: ignore[arg-type]
    )

    assert geocoder.calls == [(37.5407, -77.436)]
    assert stats.public_hits == 0
    assert stats.api_requests == 1
    assert runs[0].start_city == "Richmond"


def test_coordinate_rounding_allows_equivalent_cache_hit(tmp_path: Path) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    runs, stats = enrich_runs(
        [_run(start_lat=38.12345639, start_lon=-77.12345639)],
        cache_path=path,
        api_key=None,
    )
    assert runs[0].start_city == "Frederick"
    assert stats.cache_hits == 1


def test_provider_payload_is_retained_in_private_cache(tmp_path: Path) -> None:
    payload = {
        "city": "Frederick",
        "postcode": "21701",
        "plusCode": "example-private-value",
    }
    path = tmp_path / "cache.csv"
    cache = GeocodingCache(path)
    cache.update(
        1,
        38.1234564,
        -77.1234564,
        parse_bigdatacloud_response(payload),
        geocoded_at="2024-01-01T00:00:00Z",
    )
    cache.save()

    entry = GeocodingCache(path).entry(1)
    assert entry is not None
    assert entry.location.provider_payload == payload
    assert "example-private-value" in path.read_text(encoding="utf-8")


def test_missing_key_with_complete_cache_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "cache.csv"
    _cache(path)
    runs, _ = enrich_runs([_run()], cache_path=path, api_key=None)
    assert runs[0].start_country_code == "US"


def test_public_location_is_durable_cache_for_ephemeral_ci(tmp_path: Path) -> None:
    runs, stats = enrich_runs(
        [_run(start_city="Frederick", start_state="Maryland")],
        cache_path=tmp_path / "missing-private-cache.csv",
        api_key=None,
    )
    assert runs[0].start_city == "Frederick"
    assert stats.public_hits == 1
    assert stats.api_requests == 0


def test_missing_key_with_uncached_coordinates_fails(tmp_path: Path) -> None:
    with pytest.raises(GeocodingError, match="BIGDATACLOUD_API_KEY"):
        enrich_runs([_run()], cache_path=tmp_path / "cache.csv", api_key=None)


def test_http_error_is_clear_and_key_is_not_in_url() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "test-api-key" not in str(request.url)
        assert request.headers["x-bdc-key"] == "test-api-key"
        return httpx.Response(402)

    geocoder = BigDataCloudGeocoder(
        "test-api-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    with pytest.raises(GeocodingError, match="quota") as caught:
        geocoder.reverse(38.1, -77.1)
    assert "test-api-key" not in str(caught.value)


def test_malformed_json_fails_clearly() -> None:
    geocoder = BigDataCloudGeocoder(
        "test-api-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"not-json")
            )
        ),
        max_retries=0,
    )
    with pytest.raises(GeocodingError, match="malformed JSON"):
        geocoder.reverse(38.1, -77.1)


def test_public_csv_is_deterministic_and_contains_no_secrets_or_coordinates(
    tmp_path: Path,
) -> None:
    run = _run(
        start_city="Frederick",
        start_state="Maryland",
        start_country="United States",
        start_country_code="US",
    )
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_runs([run], output_path=first)
    write_runs([run], output_path=second)
    text = first.read_text(encoding="utf-8")
    assert first.read_bytes() == second.read_bytes()
    with first.open(encoding="utf-8", newline="") as stream:
        record = next(csv.DictReader(stream))
    assert record["start_city"] == "Frederick"
    assert record["start_region"] == "Maryland"
    assert record["start_country"] == "United States"
    assert record["start_country_code"] == "US"
    assert "38.1234564" not in text
    assert "test-api-key" not in text
