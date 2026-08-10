"""Focused tests for the documented TONE3000 REST/OAuth boundary."""

import base64
import hashlib
import io
import stat
import threading
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pytest

import library
import tone3000

_REAL_TOP_CREATORS = tone3000.top_creators


def test_authorization_url_uses_s256_pkce(monkeypatch):
    monkeypatch.setenv("TONE3000_CLIENT_ID", "t3k_pub_test")
    verifier = "verifier-value"
    url = tone3000.authorization_url(
        code_verifier=verifier,
        state="state-value",
        redirect_uri="http://127.0.0.1:8765/oauth/callback",
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert query == {
        "client_id": ["t3k_pub_test"],
        "redirect_uri": ["http://127.0.0.1:8765/oauth/callback"],
        "response_type": ["code"],
        "code_challenge": [expected],
        "code_challenge_method": ["S256"],
        "state": ["state-value"],
    }


def test_login_validates_callback_and_persists_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("TONE3000_CLIENT_ID", "t3k_pub_test")
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    captured = {}
    auth_url = {}

    real_authorization_url = tone3000.authorization_url

    def capture_authorization_url(**kwargs):
        auth_url["value"] = real_authorization_url(**kwargs)
        return auth_url["value"]

    monkeypatch.setattr(tone3000, "authorization_url", capture_authorization_url)

    def fake_token_request(body):
        captured.update(body)
        return {"access_token": "access", "refresh_token": "refresh",
                "expires_in": 3600, "token_type": "bearer"}

    monkeypatch.setattr(tone3000, "_token_request", fake_token_request)

    class FakeServer:
        def __init__(self, _address, handler_class):
            self.handler_class = handler_class
            self.timeout = None

        def handle_request(self):
            state = urllib.parse.parse_qs(
                urllib.parse.urlparse(auth_url["value"]).query)["state"][0]
            handler = object.__new__(self.handler_class)
            handler.path = f"/oauth/callback?code=code-value&state={state}"
            handler.send_response = lambda _status: None
            handler.send_header = lambda *_args: None
            handler.end_headers = lambda: None
            handler.wfile = io.BytesIO()
            self.handler_class.do_GET(handler)

        def server_close(self):
            pass

    monkeypatch.setattr(tone3000.http.server, "HTTPServer", FakeServer)
    result = tone3000.login(
        timeout=1, open_browser=False,
        redirect_uri="http://127.0.0.1:8765/oauth/callback",
    )

    assert result["access_token"] == "access"
    assert captured["grant_type"] == "authorization_code"
    assert captured["code"] == "code-value"
    assert captured["client_id"] == "t3k_pub_test"
    assert captured["code_verifier"]
    assert token_file.exists()
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_login_reports_callback_authorization_error(monkeypatch, tmp_path):
    monkeypatch.setenv("TONE3000_CLIENT_ID", "t3k_pub_test")
    monkeypatch.setattr(tone3000, "TOKEN_FILE", tmp_path / "tokens.json")
    auth_url = {}

    real_authorization_url = tone3000.authorization_url

    def capture_authorization_url(**kwargs):
        auth_url["value"] = real_authorization_url(**kwargs)
        return auth_url["value"]

    monkeypatch.setattr(tone3000, "authorization_url", capture_authorization_url)
    token_calls = []
    monkeypatch.setattr(tone3000, "_token_request",
                        lambda body: token_calls.append(body))

    class FakeServer:
        def __init__(self, _address, handler_class):
            self.handler_class = handler_class
            self.timeout = None

        def handle_request(self):
            state = urllib.parse.parse_qs(
                urllib.parse.urlparse(auth_url["value"]).query)["state"][0]
            handler = object.__new__(self.handler_class)
            handler.path = (
                "/oauth/callback?error=access_denied&"
                f"error_description=User+cancelled&state={state}"
            )
            handler.send_response = lambda _status: None
            handler.send_header = lambda *_args: None
            handler.end_headers = lambda: None
            handler.wfile = io.BytesIO()
            self.handler_class.do_GET(handler)

        def server_close(self):
            pass

    monkeypatch.setattr(tone3000.http.server, "HTTPServer", FakeServer)
    with pytest.raises(tone3000.AuthenticationRequiredError,
                       match="User cancelled"):
        tone3000.login(
            timeout=1, open_browser=False,
            redirect_uri="http://127.0.0.1:8765/oauth/callback",
        )
    assert token_calls == []


def test_login_reports_callback_port_error(monkeypatch):
    monkeypatch.setattr(
        tone3000.http.server,
        "HTTPServer",
        lambda *_args: (_ for _ in ()).throw(OSError("address in use")),
    )

    with pytest.raises(tone3000.AuthenticationRequiredError,
                       match="Cannot listen.*address in use"):
        tone3000.login(
            timeout=1, open_browser=False,
            redirect_uri="http://127.0.0.1:8765/oauth/callback",
        )


def test_search_uses_authenticated_official_request(monkeypatch):
    monkeypatch.setenv("TONE3000_ACCESS_TOKEN", "access-token")
    monkeypatch.setattr(tone3000, "_MIN_REQUEST_INTERVAL", 0)
    captured = {}

    def fake_open_json(request):
        captured["request"] = request
        return {"data": [], "total": 0, "total_pages": 0}

    monkeypatch.setattr(tone3000, "_open_json", fake_open_json)
    assert tone3000.search("plexi", page_size=10) == []

    request = captured["request"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    assert query["query"] == ["plexi"]
    assert query["page"] == ["1"]
    assert query["page_size"] == ["25"]
    assert request.get_header("Authorization") == "Bearer access-token"
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["content-type"] == "application/json"


def test_authenticated_request_refreshes_once_after_401(monkeypatch):
    monkeypatch.setattr(tone3000, "_MIN_REQUEST_INTERVAL", 0)
    tokens = iter(["stale", "fresh"])
    seen = []

    def fake_open_json(request):
        seen.append(request.get_header("Authorization"))
        if len(seen) == 1:
            raise tone3000.Tone3000HTTPError(401, "expired")
        return {"data": {"id": 7}}

    monkeypatch.setattr(tone3000, "access_token",
                        lambda **_kwargs: next(tokens))
    monkeypatch.setattr(tone3000, "_open_json", fake_open_json)

    assert tone3000._request_json(
        f"{tone3000.API}/user", authenticated=True) == {"data": {"id": 7}}
    assert seen == ["Bearer stale", "Bearer fresh"]


def test_current_user_unwraps_authenticated_profile(monkeypatch):
    seen = []

    def request(url, **kwargs):
        seen.append((url, kwargs))
        return {"data": {"id": 7, "username": "alice"}}

    monkeypatch.setattr(tone3000, "_request_json", request)

    assert tone3000.current_user() == {"id": 7, "username": "alice"}
    assert seen == [(f"{tone3000.API}/user", {"authenticated": True})]


def test_environment_token_never_falls_back_to_disk_token_after_401(
        monkeypatch, tmp_path):
    monkeypatch.setenv("TONE3000_ACCESS_TOKEN", "environment-token")
    monkeypatch.setattr(tone3000, "TOKEN_FILE", tmp_path / "tokens.json")
    tone3000._write_tokens({
        "access_token": "disk-token", "refresh_token": "disk-refresh",
        "expires_in": 3600,
    })
    monkeypatch.setattr(tone3000, "_MIN_REQUEST_INTERVAL", 0)
    seen = []

    def fake_open_json(request):
        seen.append(request.get_header("Authorization"))
        raise tone3000.Tone3000HTTPError(401, "expired")

    monkeypatch.setattr(tone3000, "_open_json", fake_open_json)

    with pytest.raises(tone3000.Tone3000HTTPError, match="HTTP 401"):
        tone3000._request_json(f"{tone3000.API}/user", authenticated=True)

    assert seen == ["Bearer environment-token", "Bearer environment-token"]


def test_api_retries_rate_limit_with_retry_after(monkeypatch):
    attempts = 0
    delays = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b"{}"

    error = urllib.error.HTTPError("https://example.invalid", 429, "busy", {}, None)
    error.headers = {"Retry-After": "0"}

    def fake_urlopen(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return Response()

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tone3000.time, "sleep", lambda delay: delays.append(delay))
    assert tone3000._open_json(tone3000.urllib.request.Request("https://example.invalid")) == {}
    assert attempts == 2
    assert delays == [0.0]


def test_api_backs_off_when_rate_limit_has_no_retry_after(monkeypatch):
    attempts = 0
    delays = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b"{}"

    error = urllib.error.HTTPError("https://example.invalid", 429, "busy", {}, None)
    error.headers = {}

    def fake_urlopen(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return Response()

    monkeypatch.setattr(tone3000.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tone3000.time, "sleep", lambda delay: delays.append(delay))
    assert tone3000._open_json(tone3000.urllib.request.Request(
        "https://example.invalid")) == {}
    assert attempts == 2
    assert delays == [0.5]


def test_search_composes_official_25_item_pages(monkeypatch):
    calls = []

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/tones/search"
        calls.append(params)
        first = (params["page"] - 1) * 25
        return {
            "data": [{"id": first + offset, "title": f"Tone {first + offset}"}
                     for offset in range(1, 26)],
            "total": 80,
            "total_pages": 4,
        }

    monkeypatch.setattr(tone3000, "_get", fake_get)
    rows = tone3000.search("plexi", page_size=40, page_number=1)
    assert [row["id"] for row in rows] == list(range(1, 41))
    assert len(calls) == 2
    assert [call["page"] for call in calls] == [1, 2]
    assert all(call["page_size"] == 25 for call in calls)
    assert all(row["total_count"] == 80 for row in rows)


def test_top_uses_public_aggregate_and_preserves_limit(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append((url, params))
        return [{"id": 1, "user_id": "u1", "downloads_count": 10}]

    monkeypatch.setattr(tone3000, "_get", fake_get)
    monkeypatch.setattr(tone3000, "_attach_usernames",
                        lambda rows, **_kwargs: None)

    rows = tone3000.top(30)

    assert [row["id"] for row in rows] == [1]
    assert calls == [(
        f"{tone3000.LEGACY_API}/tones_counts",
        {
            "select": (
                "id,title,description,gear,downloads_count,favorites_count,"
                "a1_models_count,a2_models_count,custom_models_count,"
                "irs_count,models_count,created_at,user_id,platform"
            ),
            "order": "downloads_count.desc",
            "limit": 30,
        },
    )]


def test_models_honor_official_total_pages(monkeypatch):
    calls = []

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/models"
        calls.append(params)
        page = params["page"]
        rows = [{"id": page, "tone_id": 9,
                 "architecture_version": "2", "name": f"model-{page}"}]
        return {"data": rows, "total_pages": 2}

    monkeypatch.setattr(tone3000, "_get", fake_get)
    rows = tone3000.models(9, a2_only=True)

    assert [row["id"] for row in rows] == [1, 2]
    assert [call["architecture"] for call in calls] == ["2", "2"]
    assert [call["page_size"] for call in calls] == [300, 300]


def test_top_creators_composes_ten_item_api_pages(monkeypatch):
    calls = []

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/users"
        calls.append(params)
        start = (params["page"] - 1) * 10
        return {
            "data": [{"id": start + offset, "username": f"user-{start + offset}",
                      "tones_count": 1}
                     for offset in range(10)],
            "total_pages": 3,
        }

    monkeypatch.setattr(tone3000, "_get", fake_get)
    rows = _REAL_TOP_CREATORS(page_size=15, page_number=1)

    assert [row["id"] for row in rows] == list(range(15))
    assert [call["page"] for call in calls] == [1, 2]
    assert all(call["page_size"] == 10 for call in calls)


def test_top_creators_maps_logical_page_to_remote_pages(monkeypatch):
    calls = []

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/users"
        calls.append(params)
        start = (params["page"] - 1) * 10
        return {
            "data": [{"id": start + offset, "username": f"user-{start + offset}",
                      "tones_count": 1}
                     for offset in range(10)],
            "total_pages": 10,
        }

    monkeypatch.setattr(tone3000, "_get", fake_get)
    rows = _REAL_TOP_CREATORS(page_size=15, page_number=2)

    assert [row["id"] for row in rows] == list(range(15, 30))
    assert [call["page"] for call in calls] == [2, 3]
    assert all(call["page_size"] == 10 for call in calls)


def test_refresh_token_replaces_expired_access_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TONE3000_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TONE3000_CLIENT_ID", "t3k_pub_test")
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    tone3000._write_tokens({
        "access_token": "expired", "refresh_token": "refresh-old",
        "expires_in": 0,
    })
    body = {}

    def fake_token_request(request_body):
        body.update(request_body)
        return {"access_token": "fresh", "refresh_token": "refresh-new",
                "expires_in": 3600}

    monkeypatch.setattr(tone3000, "_token_request", fake_token_request)
    assert tone3000.access_token() == "fresh"
    assert body == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-old",
        "client_id": "t3k_pub_test",
    }
    assert token_file.read_text().find("fresh") >= 0


def test_concurrent_refreshes_consume_one_refresh_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TONE3000_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TONE3000_CLIENT_ID", "t3k_pub_test")
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    tone3000._write_tokens({
        "access_token": "expired", "refresh_token": "refresh-old",
        "expires_in": 0,
    })
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_token_request(body):
        calls.append(body)
        started.set()
        assert release.wait(2)
        return {"access_token": "fresh", "refresh_token": "refresh-new",
                "expires_in": 3600}

    monkeypatch.setattr(tone3000, "_token_request", fake_token_request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(tone3000.access_token)
        assert started.wait(2)
        second = pool.submit(tone3000.access_token)
        release.set()
        assert first.result() == "fresh"
        assert second.result() == "fresh"
    assert len(calls) == 1


def test_expired_access_token_without_refresh_token_requires_login(monkeypatch, tmp_path):
    monkeypatch.delenv("TONE3000_ACCESS_TOKEN", raising=False)
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    tone3000._write_tokens({"access_token": "expired", "expires_in": 0})

    with pytest.raises(tone3000.AuthenticationRequiredError, match="login required"):
        tone3000.access_token()


def test_invalid_refresh_token_is_cleared_and_requires_login(monkeypatch, tmp_path):
    monkeypatch.delenv("TONE3000_ACCESS_TOKEN", raising=False)
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    tone3000._write_tokens({
        "access_token": "expired", "refresh_token": "refresh-old",
        "expires_in": 0,
    })

    def invalid_refresh(_body):
        raise tone3000.Tone3000HTTPError(400, "invalid_grant")

    monkeypatch.setattr(tone3000, "_token_request", invalid_refresh)
    with pytest.raises(tone3000.AuthenticationRequiredError, match="log in again"):
        tone3000.access_token()
    assert not token_file.exists()


def test_cli_exposes_tone_login(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(library.tone3000, "login",
                        lambda: called.append(True) or {"access_token": "access"})

    assert library.main(["tone", "login"]) == 0
    assert called == [True]
    assert "login complete" in capsys.readouterr().out.lower()


def test_nested_user_fields_fill_empty_compatibility_values():
    row = tone3000._canonical_tone({
        "user": {"id": 42, "username": "alice", "avatar_url": "avatar",
                 "url": "https://www.tone3000.com/alice"},
        "user_id": None, "username": "", "avatar_url": None, "user_url": None,
        "tags": [{"id": 1, "name": "Clean"}],
        "makes": [{"id": 2, "name": "Two Rock"}],
    })

    assert row["user_id"] == 42
    assert row["username"] == "alice"
    assert row["avatar_url"] == "avatar"
    assert row["user_url"].endswith("/alice")
    assert row["tags"] == ["Clean"]
    assert row["makes"] == ["Two Rock"]


def test_model_download_refreshes_bearer_after_401(monkeypatch, tmp_path):
    monkeypatch.setattr(tone3000, "models", lambda *_args, **_kwargs: [{
        "id": 1, "model_url": "https://cdn.example/model.nam", "name": "model.nam"
    }])
    tokens = iter(["stale", "fresh"])
    seen = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b"model"

    def fake_urlopen(request, **_kwargs):
        seen.append(request.get_header("Authorization"))
        if len(seen) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, None)
        return Response()

    monkeypatch.setattr(tone3000, "access_token",
                        lambda force_refresh=False: next(tokens))
    monkeypatch.setattr(tone3000.urllib.request, "urlopen", fake_urlopen)
    assert tone3000.download(9, tmp_path, quiet=True) == 1
    assert seen == ["Bearer stale", "Bearer fresh"]
    assert (tmp_path / "model.nam").read_bytes() == b"model"


def test_tone_by_id_uses_official_resource_shape(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append((url, params))
        assert url == f"{tone3000.API}/tones/7"
        return {
            "id": 7, "title": "Official tone", "user_id": 42,
            "user": {"id": 42, "username": "alice", "url": "https://www.tone3000.com/alice"},
            "tags": [{"id": 1, "name": "clean"}],
            "makes": [{"id": 2, "name": "Fender"}],
            "format": "nam",
        }

    monkeypatch.setattr(tone3000, "_get", fake_get)
    monkeypatch.setattr(tone3000, "models",
                        lambda *_args, **_kwargs: [{"name": "Clean.nam"}])

    row = tone3000.tone_by_id(7)

    assert row["username"] == "alice"
    assert row["tags"] == ["clean"]
    assert row["makes"] == ["Fender"]
    assert row["model_name"] == "Clean.nam"
    assert calls == [(f"{tone3000.API}/tones/7", {})]


def test_user_uses_documented_query_search(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append((url, params))
        return {"data": [
            {"id": 1, "username": "alice2"},
            {"id": 2, "username": "Alice"},
        ], "total_pages": 1}

    monkeypatch.setattr(tone3000, "_get", fake_get)

    assert tone3000.user("alice")["id"] == 2
    assert calls == [(f"{tone3000.API}/users",
                      {"query": "alice", "page": 1, "page_size": 10})]
