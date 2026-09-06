"""Parse running metadata and GPS tracks from a Strava account export."""

from __future__ import annotations

import csv
import gzip
import hashlib
import math
import stat
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

import fitdecode

from running.normalize import (
    Run,
    is_running_activity,
    normalize_run,
)

FIT_SEMICIRCLE_TO_DEGREES = 180.0 / 2**31


class StravaExportError(RuntimeError):
    """The Strava export is missing, malformed, or ambiguous."""


class TrackParseError(StravaExportError):
    """A referenced GPS track cannot be parsed."""


@dataclass(frozen=True)
class GPSPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ParsedExport:
    """Canonical runs and counts read from one export."""

    activities_parsed: int
    runs_parsed: int
    runs: tuple[Run, ...]


@dataclass(frozen=True)
class ArchiveExport:
    """A parsed ZIP with a deterministic preference key for overlapping exports."""

    path: Path
    exported_at: datetime
    digest: str
    parsed: ParsedExport

    @property
    def preference_key(self) -> tuple[datetime, str]:
        return self.exported_at, self.digest


def discover_archives(search_root: Path) -> list[Path]:
    """Find every Strava ZIP below the raw-data directory."""
    if not search_root.is_dir():
        return []
    return sorted(
        path
        for path in search_root.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".zip"
    )


def _optional_text(value: str | None) -> str | None:
    text = "" if value is None else value.strip()
    return text or None


def _optional_float(value: str | None, *, field: str, row_number: int) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError as error:
        raise StravaExportError(
            f"activities.csv row {row_number}: {field} is not numeric: {text!r}"
        ) from error
    if not math.isfinite(number):
        raise StravaExportError(
            f"activities.csv row {row_number}: {field} is not finite"
        )
    return number


def _required_float(value: str | None, *, field: str, row_number: int) -> float:
    number = _optional_float(value, field=field, row_number=row_number)
    if number is None:
        raise StravaExportError(
            f"activities.csv row {row_number}: required {field} is missing"
        )
    return number


def _whole_seconds(value: str | None, *, field: str, row_number: int) -> int:
    number = _required_float(value, field=field, row_number=row_number)
    if not number.is_integer():
        raise StravaExportError(
            f"activities.csv row {row_number}: {field} must be whole seconds"
        )
    return int(number)


def _parse_datetime(value: str | None, row_number: int) -> datetime:
    text = _optional_text(value)
    if text is None:
        raise StravaExportError(
            f"activities.csv row {row_number}: Activity Date is missing"
        )
    try:
        parsed = datetime.strptime(text, "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise StravaExportError(
                f"activities.csv row {row_number}: invalid Activity Date {text!r}"
            ) from error
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _unique_header(raw_header: Iterable[str]) -> list[str]:
    counts: Counter[str] = Counter()
    header: list[str] = []
    for name in raw_header:
        occurrence = counts[name]
        header.append(name if occurrence == 0 else f"{name}.{occurrence}")
        counts[name] += 1
    return header


def _validate_columns(header: list[str]) -> None:
    required = {
        "Activity ID",
        "Activity Date",
        "Activity Name",
        "Activity Type",
        "Filename",
        "Elapsed Time",
        "Moving Time",
        "Distance",
    }
    missing = sorted(required - set(header))
    if missing:
        raise StravaExportError(
            "activities.csv is missing required columns: " + ", ".join(missing)
        )


def _detail_column(header: list[str], base: str) -> str:
    return f"{base}.1" if f"{base}.1" in header else base


def _safe_track_path(export_directory: Path, filename: str, row_number: int) -> Path:
    root = export_directory.resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise StravaExportError(
            f"activities.csv row {row_number}: Filename escapes the export: {filename}"
        ) from error
    if not candidate.is_file():
        raise StravaExportError(
            f"activities.csv row {row_number}: referenced activity file not found: "
            f"{filename}"
        )
    return candidate


def _check_duplicate_semantics(
    row: dict[str, str], header: list[str], row_number: int
) -> None:
    for field in ("Elapsed Time", "Max Heart Rate"):
        detail = f"{field}.1"
        if detail not in header:
            continue
        first = _optional_float(row.get(field), field=field, row_number=row_number)
        second = _optional_float(row.get(detail), field=detail, row_number=row_number)
        if (
            first is not None
            and second is not None
            and not math.isclose(first, second, abs_tol=1e-6)
        ):
            raise StravaExportError(
                f"activities.csv row {row_number}: duplicate {field} columns disagree"
            )

    if "Distance.1" in header:
        summary = _optional_float(
            row.get("Distance"), field="Distance", row_number=row_number
        )
        detail = _optional_float(
            row.get("Distance.1"), field="Distance.1", row_number=row_number
        )
        if summary is not None and detail is not None and (summary or detail):
            tolerance = 10 + abs(detail) * 0.01
            if abs(detail - summary * 1000) > tolerance:
                raise StravaExportError(
                    f"activities.csv row {row_number}: duplicate Distance columns "
                    "do not have expected kilometre/metre semantics"
                )


def load_export_directory(export_directory: Path) -> ParsedExport:
    """Load running rows and source counts from an extracted Strava export."""
    export_directory = Path(export_directory)
    csv_path = export_directory / "activities.csv"
    if not csv_path.is_file():
        raise StravaExportError(f"activities.csv not found under {export_directory}")

    try:
        stream = csv_path.open(encoding="utf-8-sig", newline="")
    except OSError as error:
        raise StravaExportError(f"could not read {csv_path}: {error}") from error

    runs: list[Run] = []
    track_jobs: list[tuple[int, Path]] = []
    activities_parsed = 0
    runs_parsed = 0
    with stream:
        reader = csv.reader(stream)
        try:
            raw_header = next(reader)
        except StopIteration as error:
            raise StravaExportError(f"{csv_path} is empty") from error
        header = _unique_header(raw_header)
        _validate_columns(header)
        rows = csv.DictReader(stream, fieldnames=header)

        for row_number, row in enumerate(rows, start=2):
            activities_parsed += 1
            if not is_running_activity(row.get("Activity Type"), row.get("Type")):
                continue
            runs_parsed += 1
            _check_duplicate_semantics(row, header, row_number)
            raw_id = _optional_text(row.get("Activity ID"))
            try:
                activity_id = int(raw_id) if raw_id is not None else None
            except ValueError as error:
                raise StravaExportError(
                    f"activities.csv row {row_number}: invalid Activity ID {raw_id!r}"
                ) from error
            if activity_id is None:
                raise StravaExportError(
                    f"activities.csv row {row_number}: Activity ID is missing"
                )

            distance_column = _detail_column(header, "Distance")
            elapsed_column = _detail_column(header, "Elapsed Time")
            max_hr_column = _detail_column(header, "Max Heart Rate")
            distance_value = row.get(distance_column)
            distance_is_km = distance_column == "Distance"
            if _optional_text(distance_value) is None and distance_column != "Distance":
                distance_value = row.get("Distance")
                distance_is_km = True
            distance_m = _required_float(
                distance_value, field=distance_column, row_number=row_number
            )
            if distance_is_km:
                distance_m *= 1000

            elapsed_value = row.get(elapsed_column)
            if (
                _optional_text(elapsed_value) is None
                and elapsed_column != "Elapsed Time"
            ):
                elapsed_value = row.get("Elapsed Time")

            filename = _optional_text(row.get("Filename"))
            if filename is not None:
                track_path = _safe_track_path(export_directory, filename, row_number)

            max_hr = _optional_float(
                row.get(max_hr_column), field=max_hr_column, row_number=row_number
            )
            if max_hr is None and max_hr_column != "Max Heart Rate":
                max_hr = _optional_float(
                    row.get("Max Heart Rate"),
                    field="Max Heart Rate",
                    row_number=row_number,
                )

            run_index = len(runs)
            runs.append(
                normalize_run(
                    activity_id=activity_id,
                    start_datetime=_parse_datetime(
                        row.get("Activity Date"), row_number
                    ),
                    name=row.get("Activity Name", ""),
                    distance_m=distance_m,
                    moving_time_s=_whole_seconds(
                        row.get("Moving Time"),
                        field="Moving Time",
                        row_number=row_number,
                    ),
                    elapsed_time_s=_whole_seconds(
                        elapsed_value,
                        field=elapsed_column,
                        row_number=row_number,
                    ),
                    elevation_gain_m=_optional_float(
                        row.get("Elevation Gain"),
                        field="Elevation Gain",
                        row_number=row_number,
                    ),
                    max_speed_mps=_optional_float(
                        row.get("Max Speed"),
                        field="Max Speed",
                        row_number=row_number,
                    ),
                    average_heart_rate_bpm=_optional_float(
                        row.get("Average Heart Rate"),
                        field="Average Heart Rate",
                        row_number=row_number,
                    ),
                    max_heart_rate_bpm=max_hr,
                    calories=_optional_float(
                        row.get("Calories"), field="Calories", row_number=row_number
                    ),
                    source=f"activities.csv row {row_number}",
                )
            )
            if filename is not None:
                track_jobs.append((run_index, track_path))

    if track_jobs:
        paths = [path for _, path in track_jobs]
        if len(paths) == 1:
            endpoints = [load_track_endpoints(paths[0])]
        else:
            with ProcessPoolExecutor(max_workers=min(4, len(paths))) as executor:
                endpoints = list(executor.map(load_track_endpoints, paths, chunksize=8))
        for (run_index, _), (start_point, end_point) in zip(
            track_jobs, endpoints, strict=True
        ):
            runs[run_index] = replace(
                runs[run_index],
                start_lat=start_point.latitude if start_point else None,
                start_lon=start_point.longitude if start_point else None,
                end_lat=end_point.latitude if end_point else None,
                end_lon=end_point.longitude if end_point else None,
            )

    by_id: dict[int, Run] = {}
    for run in runs:
        previous = by_id.get(run.activity_id)
        if previous is None or _run_preference(run) > _run_preference(previous):
            by_id[run.activity_id] = run
    ordered = tuple(
        sorted(by_id.values(), key=lambda run: (run.start_datetime, run.activity_id))
    )
    return ParsedExport(
        activities_parsed=activities_parsed,
        runs_parsed=runs_parsed,
        runs=ordered,
    )


def _run_preference(run: Run) -> tuple[int, str]:
    """Choose consistently if an export unexpectedly repeats an activity ID."""
    populated = sum(
        value not in (None, "")
        for value in (
            run.name,
            run.elevation_gain_m,
            run.max_speed_mps,
            run.average_heart_rate_bpm,
            run.max_heart_rate_bpm,
            run.calories,
            run.start_lat,
            run.start_lon,
            run.end_lat,
            run.end_lon,
        )
    )
    return populated, repr(run)


def load_runs(export_directory: Path) -> list[Run]:
    """Load canonical Strava runs from an extracted export directory."""
    return list(load_export_directory(export_directory).runs)


def _validate_members(archive_path: Path, archive: ZipFile) -> str:
    names: set[str] = set()
    activity_names: list[str] = []
    for info in archive.infolist():
        member = PurePosixPath(info.filename)
        if member.is_absolute() or ".." in member.parts:
            raise StravaExportError(
                f"unsafe path in Strava archive {archive_path}: {info.filename!r}"
            )
        if info.filename in names:
            raise StravaExportError(
                f"duplicate member in Strava archive {archive_path}: {info.filename!r}"
            )
        names.add(info.filename)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise StravaExportError(
                f"symbolic link in Strava archive {archive_path}: {info.filename!r}"
            )
        if not info.is_dir() and member.name == "activities.csv":
            activity_names.append(info.filename)
    if len(activity_names) != 1:
        raise StravaExportError(
            f"Strava archive {archive_path} must contain exactly one activities.csv; "
            f"found {len(activity_names)}"
        )
    return activity_names[0]


def load_archive(path: Path) -> ArchiveExport:
    """Safely unpack and parse one Strava bulk-export ZIP."""
    path = Path(path)
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with ZipFile(path) as archive:
            activity_name = _validate_members(path, archive)
            activity_info = archive.getinfo(activity_name)
            exported_at = datetime(*activity_info.date_time, tzinfo=UTC)
            with tempfile.TemporaryDirectory(prefix="running-strava-") as temporary:
                archive.extractall(temporary)
                export_directory = Path(temporary) / PurePosixPath(activity_name).parent
                parsed = load_export_directory(export_directory)
    except StravaExportError:
        raise
    except (BadZipFile, OSError, RuntimeError, ValueError) as error:
        raise StravaExportError(
            f"could not read Strava archive {path}: {error}"
        ) from error
    return ArchiveExport(path, exported_at, digest, parsed)


def _valid_point(latitude: object, longitude: object) -> GPSPoint | None:
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return GPSPoint(lat, lon)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_xml(stream: BinaryIO) -> ET.Element:
    data = stream.read().lstrip()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise TrackParseError("GPS XML contains a prohibited DTD/entity declaration")
    return ET.fromstring(data)


def _parse_tcx(stream: BinaryIO) -> tuple[GPSPoint, ...]:
    points: list[GPSPoint] = []
    for trackpoint in _safe_xml(stream).iter():
        if _local_name(trackpoint.tag) != "Trackpoint":
            continue
        values = {
            _local_name(element.tag): element.text for element in trackpoint.iter()
        }
        point = _valid_point(
            values.get("LatitudeDegrees"), values.get("LongitudeDegrees")
        )
        if point is not None:
            points.append(point)
    return tuple(points)


def _parse_gpx(stream: BinaryIO) -> tuple[GPSPoint, ...]:
    points: list[GPSPoint] = []
    for element in _safe_xml(stream).iter():
        if _local_name(element.tag) not in {"trkpt", "rtept"}:
            continue
        point = _valid_point(element.get("lat"), element.get("lon"))
        if point is not None:
            points.append(point)
    return tuple(points)


def _parse_fit(stream: BinaryIO) -> tuple[GPSPoint, ...]:
    points: list[GPSPoint] = []
    with fitdecode.FitReader(
        stream,
        check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE,
    ) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name != "record":
                continue
            latitude = frame.get_value("position_lat", fallback=None)
            longitude = frame.get_value("position_long", fallback=None)
            if latitude is None or longitude is None:
                continue
            point = _valid_point(
                latitude * FIT_SEMICIRCLE_TO_DEGREES,
                longitude * FIT_SEMICIRCLE_TO_DEGREES,
            )
            if point is not None:
                points.append(point)
    return tuple(points)


def load_track(path: Path) -> tuple[GPSPoint, ...]:
    """Load valid coordinates from one gzip-compressed Strava activity file."""
    name = path.name.casefold()
    parser = (
        _parse_tcx
        if name.endswith(".tcx.gz")
        else _parse_gpx
        if name.endswith(".gpx.gz")
        else _parse_fit
        if name.endswith(".fit.gz")
        else None
    )
    if parser is None:
        raise TrackParseError(f"unsupported GPS file extension: {path.name}")
    try:
        with gzip.open(path, "rb") as stream:
            return parser(stream)
    except TrackParseError:
        raise
    except (OSError, EOFError, ET.ParseError, fitdecode.FitError) as error:
        raise TrackParseError(f"could not parse {path.name}: {error}") from error


def load_track_endpoints(path: Path) -> tuple[GPSPoint | None, GPSPoint | None]:
    points = load_track(path)
    return (points[0], points[-1]) if points else (None, None)


__all__ = [
    "FIT_SEMICIRCLE_TO_DEGREES",
    "ArchiveExport",
    "GPSPoint",
    "ParsedExport",
    "StravaExportError",
    "TrackParseError",
    "discover_archives",
    "load_archive",
    "load_export_directory",
    "load_runs",
    "load_track",
    "load_track_endpoints",
]
