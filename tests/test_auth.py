from __future__ import annotations

import json
import time
import urllib.parse

import httpx
import pytest

from google_search_console import AuthError, OAuthCredentials, OAuthFlow, Scope, load_client_secrets


def test_load_client_secrets(tmp_path) -> None:
    p = tmp_path / "client_secret_x.json"
    p.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    assert load_client_secrets(p) == ("cid", "sec")
    p.write_text("{}")
    with pytest.raises(AuthError):
        load_client_secrets(p)


def test_authorization_url_uses_pkce_and_offline_access() -> None:
    req = OAuthFlow("cid", None, [Scope.READONLY]).authorization_url()
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.url).query)
    assert q["scope"] == [Scope.READONLY] and q["code_challenge_method"] == ["S256"]
    assert q["access_type"] == ["offline"] and req.code_verifier


def test_refresh_and_save_roundtrip(tmp_path) -> None:
    def token_endpoint(request: httpx.Request) -> httpx.Response:
        form = urllib.parse.parse_qs(request.content.decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["r1"]
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 3600})

    http = httpx.Client(transport=httpx.MockTransport(token_endpoint))
    creds = OAuthCredentials(access_token="a1", refresh_token="r1", client_id="cid",
                             expires_at=time.time() - 10)
    assert creds.token(http) == "a2"
    creds.save(tmp_path / "token.json")
    loaded = OAuthCredentials.load(tmp_path / "token.json")
    assert loaded.refresh_token == "r1" and loaded.access_token == "a2"
