from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from running.strava.auth import (
    AccessTokenProvider,
    OAuthConfig,
    StravaAuthError,
    StravaScopeError,
    TokenState,
    TokenStore,
)


def test_unexpired_access_token_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {
                "access_token": "current-access",
                "expires_at": 10_000,
                "refresh_token": "current-refresh",
            }
        ),
        encoding="utf-8",
    )
    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", None, None, None),
        TokenStore(path),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(AssertionError("unexpected HTTP"))
            )
        ),
        now=lambda: 1_000,
    )
    assert provider.access_token() == "current-access"


def test_access_token_without_expiry_is_tried_before_refresh(tmp_path: Path) -> None:
    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", "refresh", "current-access", None),
        TokenStore(tmp_path / "token.json"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(AssertionError("unexpected HTTP"))
            )
        ),
    )
    assert provider.access_token() == "current-access"


def test_refresh_persists_changed_refresh_token(tmp_path: Path) -> None:
    path = tmp_path / "token.json"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        assert b"refresh_token=old-refresh" in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_at": 20_000,
                "refresh_token": "new-refresh",
                "scope": "read activity:read_all",
            },
        )

    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", "old-refresh", None, None),
        TokenStore(path),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        now=lambda: 1_000,
    )
    assert provider.access_token() == "new-access"
    saved = TokenStore(path).load()
    assert saved is not None
    assert saved.refresh_token == "new-refresh"
    assert saved.scopes == ("activity:read_all", "read")
    assert provider.refreshed is True
    assert "client_secret" not in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o600


def test_expired_access_token_is_refreshed(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    TokenStore(path).save(TokenState("expired", 4_000, "refresh"))
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "access_token": "replacement",
                "expires_at": 20_000,
                "refresh_token": "replacement-refresh",
            },
        )

    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", None, None, None),
        TokenStore(path),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        now=lambda: 1_000,
    )
    assert provider.access_token() == "replacement"
    assert calls == 1


def test_state_file_overrides_stale_environment_tokens(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    TokenStore(path).save(TokenState("stored-access", 10_000, "stored-refresh"))
    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", "stale-refresh", "stale-access", 10_000),
        TokenStore(path),
        now=lambda: 1_000,
    )
    assert provider.access_token() == "stored-access"


def test_missing_credentials_are_clear() -> None:
    with pytest.raises(StravaAuthError, match="STRAVA_CLIENT_ID"):
        OAuthConfig.from_environment({})


def test_known_insufficient_scope_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    TokenStore(path).save(TokenState("access", 10_000, "refresh", ("read",)))
    provider = AccessTokenProvider(
        OAuthConfig("1", "secret", None, None, None),
        TokenStore(path),
        now=lambda: 1_000,
    )
    with pytest.raises(StravaScopeError, match="activity:read_all"):
        provider.require_scopes()
