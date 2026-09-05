"""Minimal Strava OAuth token loading and refresh support."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TOKEN_URL = "https://www.strava.com/oauth/token"
REFRESH_EARLY_SECONDS = 3600
REQUIRED_SCOPES = ("activity:read_all",)


class StravaAuthError(RuntimeError):
    """Strava credentials are unavailable or token refresh failed."""


class StravaScopeError(StravaAuthError):
    """The OAuth grant does not include the required read-only activity scope."""


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    initial_refresh_token: str | None
    initial_access_token: str | None
    initial_expires_at: int | None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> OAuthConfig:
        values = os.environ if environ is None else environ
        missing = [
            name
            for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET")
            if not values.get(name)
        ]
        if missing:
            raise StravaAuthError(
                "missing Strava credentials: "
                + ", ".join(missing)
                + "; also provide STRAVA_REFRESH_TOKEN on the first run"
            )
        raw_expires = values.get("STRAVA_ACCESS_TOKEN_EXPIRES_AT")
        try:
            expires_at = int(raw_expires) if raw_expires else None
        except ValueError as error:
            raise StravaAuthError(
                "STRAVA_ACCESS_TOKEN_EXPIRES_AT must be a Unix timestamp"
            ) from error
        return cls(
            client_id=values["STRAVA_CLIENT_ID"],
            client_secret=values["STRAVA_CLIENT_SECRET"],
            initial_refresh_token=values.get("STRAVA_REFRESH_TOKEN"),
            initial_access_token=values.get("STRAVA_ACCESS_TOKEN"),
            initial_expires_at=expires_at,
        )


@dataclass(frozen=True)
class TokenState:
    access_token: str | None
    expires_at: int | None
    refresh_token: str
    scopes: tuple[str, ...] = ()


class TokenStore:
    """Ignored local JSON storage for the newest rotating refresh token."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TokenState | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            refresh_token = raw["refresh_token"]
            access_token = raw.get("access_token")
            expires_at = raw.get("expires_at")
            raw_scopes = raw.get("scopes", [])
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("refresh_token is missing")
            if access_token is not None and not isinstance(access_token, str):
                raise ValueError("access_token must be a string")
            if expires_at is not None and not isinstance(expires_at, int):
                raise ValueError("expires_at must be an integer")
            if not isinstance(raw_scopes, list) or not all(
                isinstance(scope, str) for scope in raw_scopes
            ):
                raise ValueError("scopes must be a string array")
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
            raise StravaAuthError(
                f"invalid Strava token state at {self.path}"
            ) from error
        return TokenState(access_token, expires_at, refresh_token, tuple(raw_scopes))

    def save(self, state: TokenState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "access_token": state.access_token,
            "expires_at": state.expires_at,
            "refresh_token": state.refresh_token,
            "scopes": list(state.scopes),
        }
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


class AccessTokenProvider:
    """Return a usable access token and persist every refreshed token pair."""

    def __init__(
        self,
        config: OAuthConfig,
        store: TokenStore,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.store = store
        self.client = client or httpx.Client(timeout=20.0)
        self.now = now
        self._state = store.load() or self._initial_state(config)
        self.refreshed = False

    @staticmethod
    def _initial_state(config: OAuthConfig) -> TokenState:
        if not config.initial_refresh_token:
            raise StravaAuthError(
                "missing Strava refresh token: set STRAVA_REFRESH_TOKEN or provide "
                "an existing STRAVA_TOKEN_FILE"
            )
        return TokenState(
            access_token=config.initial_access_token,
            expires_at=config.initial_expires_at,
            refresh_token=config.initial_refresh_token,
        )

    def access_token(self) -> str:
        state = self._state
        if state.access_token and (
            state.expires_at is None
            or state.expires_at > self.now() + REFRESH_EARLY_SECONDS
        ):
            return state.access_token
        return self.refresh_access_token()

    def refresh_access_token(self) -> str:
        state = self._state
        try:
            response = self.client.post(
                TOKEN_URL,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": state.refresh_token,
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
            refreshed = TokenState(
                access_token=payload["access_token"],
                expires_at=int(payload["expires_at"]),
                refresh_token=payload["refresh_token"],
                scopes=_parse_scopes(payload.get("scope")) or state.scopes,
            )
            if not refreshed.access_token or not refreshed.refresh_token:
                raise ValueError("empty token in response")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise StravaAuthError("Strava access-token refresh failed") from error
        self.store.save(refreshed)
        self._state = refreshed
        self.refreshed = True
        return refreshed.access_token

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._state.scopes

    def require_scopes(self, required: tuple[str, ...] = REQUIRED_SCOPES) -> None:
        """Validate scopes when Strava returned an explicit OAuth scope grant."""
        if not self.scopes:
            return
        missing = tuple(scope for scope in required if scope not in self.scopes)
        if missing:
            raise StravaScopeError(
                "Strava authorization is missing read-only scope activity:read_all; "
                "reauthorize the application with activity:read_all"
            )


def _parse_scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(sorted(set(value.replace(",", " ").split())))


__all__ = [
    "REFRESH_EARLY_SECONDS",
    "REQUIRED_SCOPES",
    "TOKEN_URL",
    "AccessTokenProvider",
    "OAuthConfig",
    "StravaAuthError",
    "StravaScopeError",
    "TokenState",
    "TokenStore",
]
