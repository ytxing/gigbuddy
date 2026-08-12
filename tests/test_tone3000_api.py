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


def test_login_validates_callback_and_persists_tokens(monkeypatch, tmp_path,
                                                     capsys):
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
    assert auth_url["value"] in capsys.readouterr().out


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
    captured = []

    def fake_open_json(request):
        captured.append(request)
        return {"data": [], "total": 0, "total_pages": 0}

    monkeypatch.setattr(tone3000, "_open_json", fake_open_json)
    assert tone3000.search("plexi", page_size=10) == []

    assert len(captured) == 2
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(
        request.full_url).query) for request in captured]
    query = queries[0]
    assert query["query"] == ["plexi"]
    assert query["page"] == ["1"]
    assert query["page_size"] == ["25"]
    assert [item.get("architecture") for item in queries] == [["2"], None]
    assert captured[0].get_header("Authorization") == "Bearer access-token"
    headers = {key.lower(): value for key, value in captured[0].header_items()}
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


def test_search_includes_supported_architectures_and_hides_a1_only(
        monkeypatch):
    calls = []

    def row(tone_id, *, a1=0, a2=0, custom=0, ir=0):
        return {
            "id": tone_id, "title": f"Tone {tone_id}",
            "a1_models_count": a1, "a2_models_count": a2,
            "custom_models_count": custom, "irs_count": ir,
            "models_count": a1 + a2 + custom + ir,
        }

    def fake_post(_url, body):
        calls.append(body)
        source = body["architecture_filter"]
        if source == "2":
            data = [row(1, a2=1), row(3, a1=1, a2=1), row(2, custom=1)]
        else:
            data = [row(4, ir=1), row(5, a1=1), row(6, custom=1)]
        return {"data": data, "total": len(data), "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)
    rows = tone3000.search("keeley", page_size=20)

    assert {row["id"] for row in rows} == {1, 3, 4}
    assert len(rows) == 3
    assert all(row["total_count"] == 3 for row in rows)
    assert [call["architecture_filter"] for call in calls] == ["2", None]


def test_ir_search_does_not_send_a_nam_architecture(monkeypatch):
    calls = []

    def fake_post(_url, body):
        calls.append(body)
        return {"data": [{"id": 7, "irs_count": 1}],
                "total": 1, "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)
    rows = tone3000.search("keeley", page_size=10, gear_filters=["ir"])

    assert [row["id"] for row in rows] == [7]
    assert len(calls) == 1
    assert calls[0]["architecture_filter"] is None
    assert calls[0]["gear_filters"] == ("ir",)


def test_search_keeps_a2_and_ir_rows_when_supported_counts_are_missing(
        monkeypatch):
    calls = []

    def fake_post(_url, body):
        calls.append(body)
        if body["architecture_filter"] == "2":
            return {"data": [{"id": 11, "title": "A2 without count"}],
                    "total_pages": 1}
        return {"data": [{"id": 12, "title": "IR without count"}],
                "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)

    rows = tone3000.search("keeley", page_size=10)

    assert [row["id"] for row in rows] == [11, 12]
    assert [row["total_count"] for row in rows] == [2, 2]
    assert [call["architecture_filter"] for call in calls] == ["2", None]


def test_search_rejects_contradictory_ir_metadata_without_counts(monkeypatch):
    def fake_post(_url, body):
        if body["architecture_filter"] == "2":
            return {"data": [{"id": 21, "format": "ir"}],
                    "total_pages": 1}
        return {"data": [
            {"id": 22, "format": "nam", "gear": "cab"},
            {"id": 23, "gear": "cab"},
        ], "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)

    assert [row["id"] for row in tone3000.search("mixed", page_size=10)] == [23]
    assert not tone3000._has_supported_tone_models({"id": 24, "gear": "cab"})


def test_top_rejects_cab_rows_without_ir_evidence(monkeypatch):
    monkeypatch.setattr(
        tone3000, "_get",
        lambda _url, **_params: [
            {"id": 31, "gear": "cab"},
            {"id": 32, "gear": "cab", "format": "ir"},
        ])
    monkeypatch.setattr(tone3000, "_attach_usernames",
                        lambda rows, **_kwargs: None)

    assert [row["id"] for row in tone3000.top(10)] == [32]


def test_search_composes_25_item_pages_across_architectures(monkeypatch):
    calls = []

    def fake_post(_url, body):
        calls.append(body)
        page = body["page_number"]
        source = body["architecture_filter"]
        base = {"2": 0, None: 1000}[source]
        first = base + (page - 1) * 25
        data = [{
            "id": first + offset, "title": f"Tone {first + offset}",
            "a2_models_count": 1 if source == "2" else 0,
            "irs_count": 1 if source is None else 0,
        } for offset in range(25)]
        return {"data": data, "total": 50, "total_pages": 2}

    monkeypatch.setattr(tone3000, "_post", fake_post)
    first_page = tone3000.search("plexi", page_size=40, page_number=1)
    second_page = tone3000.search("plexi", page_size=40, page_number=2)

    first_ids = {row["id"] for row in first_page}
    second_ids = {row["id"] for row in second_page}
    assert len(first_ids) == len(second_ids) == 40
    assert first_ids.isdisjoint(second_ids)
    assert all(row["total_count"] == 50 for row in first_page)
    assert all(row["total_count"] == 100 for row in second_page)
    assert len(calls) == 4  # each of 2 remote pages is fetched once per source
    assert [(call["architecture_filter"], call["page_number"])
            for call in calls] == [("2", 1), (None, 1), ("2", 2), (None, 2)]


def test_search_downloads_all_time_is_sorted_globally(monkeypatch):
    def fake_post(_url, body):
        source = body["architecture_filter"]
        data = ([{"id": 1, "downloads_count": 10, "a2_models_count": 1}]
                if source == "2" else
                [{"id": 2, "downloads_count": 100, "irs_count": 1}])
        return {"data": data, "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)

    rows = tone3000.search("amp", page_size=10,
                          order_by="downloads-all-time")

    assert [row["id"] for row in rows] == [2, 1]


def test_search_newest_is_sorted_by_created_at(monkeypatch):
    def fake_post(_url, body):
        source = body["architecture_filter"]
        data = ([{"id": 1, "created_at": "2026-01-01T00:00:00Z",
                  "a2_models_count": 1}]
                if source == "2" else
                [{"id": 2, "created_at": "2026-08-01T00:00:00Z",
                  "irs_count": 1}])
        return {"data": data, "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)

    rows = tone3000.search("amp", page_size=10, order_by="newest")

    assert [row["id"] for row in rows] == [2, 1]


def test_top_uses_public_aggregate_and_preserves_limit(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append((url, params))
        return [{"id": 1, "user_id": "u1", "downloads_count": 10,
                 "a2_models_count": 1}]

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


def test_top_fills_page_after_filtering_unsupported_tones(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append(params)
        if params.get("offset") == 2:
            return [{"id": 4, "a2_models_count": 1}]
        return [{"id": 1, "custom_models_count": 2},
                {"id": 3, "a2_models_count": 1}]

    monkeypatch.setattr(tone3000, "_get", fake_get)
    monkeypatch.setattr(tone3000, "_attach_usernames",
                        lambda rows, **_kwargs: None)

    assert [row["id"] for row in tone3000.top(2)] == [3, 4]
    assert calls[0]["limit"] == 2
    assert calls[1]["offset"] == 2


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


def test_a2_model_fetch_rejects_non_nam_files_and_formats(monkeypatch):
    def fake_get(_url, **params):
        assert params["architecture"] == "2"
        return {
            "data": [
                {"id": 1, "architecture_version": "2",
                 "name": "good.nam"},
                {"id": 2, "architecture_version": "2",
                 "name": "wrong.wav"},
                {"id": 3, "architecture_version": "2",
                 "name": "wrong.aida-x"},
                {"id": 4, "architecture_version": "2",
                 "format": "proteus", "name": "wrong.nam"},
            ],
            "total_pages": 1,
        }

    monkeypatch.setattr(tone3000, "_get", fake_get)

    assert [row["id"] for row in tone3000.models(9, a2_only=True)] == [1]


def test_a2_model_fetch_keeps_architectureless_nam_rows_from_a2_view(monkeypatch):
    def fake_get(_url, **params):
        if params.get("architecture") == "2":
            return {
                "data": [
                    {"id": 1, "name": "good.nam"},
                    {"id": 2, "format": "nam", "name": "good-2.nam"},
                    {"id": 3, "name": "wrong.wav"},
                ],
                "total_pages": 1,
            }
        # The unfiltered endpoint can repeat an architectureless A2 row. The
        # A2 source must win so the row is not lost to fail-closed inference.
        return {"data": [{"id": 1, "name": "good.nam"}], "total_pages": 1}

    monkeypatch.setattr(tone3000, "_get", fake_get)

    assert [row["id"] for row in tone3000.models(9, a2_only=False)] == [1, 2]


def test_complete_model_fetch_keeps_only_a2_and_ir(monkeypatch):
    calls = []

    def fake_get(url, **params):
        assert url == f"{tone3000.API}/models"
        calls.append(params)
        if params.get("architecture") == "2":
            rows = [{"id": 1, "architecture_version": "2",
                     "name": "modern.nam"}]
        else:
            rows = [
                {"id": 1, "architecture_version": "2", "name": "modern.nam"},
                {"id": 2, "architecture": "WaveNet", "name": "legacy.nam"},
                {"id": 3, "architecture": "custom", "name": "custom.nam"},
                {"id": 4, "architecture": "IR", "name": "cab.wav"},
                {"id": 5, "architecture": "AIDA-X", "name": "aida.nam"},
                {"id": 6, "architecture": "AA-SNAPSHOT", "name": "aa.nam"},
                {"id": 7, "architecture": "Proteus", "name": "proteus.nam"},
                {"id": 8, "architecture": "FutureRuntime", "name": "future.nam"},
                {"id": 9, "architecture_version": "2",
                 "architecture": "custom", "name": "conflict.nam"},
            ]
        return {"data": rows, "total_pages": 1}

    monkeypatch.setattr(tone3000, "_get", fake_get)
    rows = tone3000.models(9, a2_only=False)

    assert [row["id"] for row in rows] == [1, 4]
    assert [call.get("architecture") for call in calls] == [None, "2"]


def test_architectureless_ir_model_uses_parent_tone_context(monkeypatch):
    calls = []

    def fake_get(url, **params):
        calls.append((url, params))
        if url == f"{tone3000.API}/models":
            if params.get("architecture") == "2":
                return {"data": [], "total_pages": 1}
            return {"data": [{"id": 1, "tone_id": 9}], "total_pages": 1}
        assert url == f"{tone3000.API}/tones/9"
        return {"id": 9, "gear": "cab", "format": "ir"}

    monkeypatch.setattr(tone3000, "_get", fake_get)

    rows = tone3000.models(9, a2_only=False)
    assert [row["id"] for row in rows] == [1]
    assert rows[0]["architecture"] == "IR"
    assert (f"{tone3000.API}/tones/9", {}) in calls


def test_supported_model_classifier_rejects_other_formats_and_unknown_ir():
    assert tone3000.is_supported_model(
        {"architecture_version": "2", "name": "modern.nam"})
    assert tone3000.is_supported_model(
        {"architecture": "IR", "name": "cab.wav"})
    assert tone3000.is_supported_model(
        {"name": "cab.wav"}, {"format": "ir", "gear": "cab"})

    for model in (
            {"architecture": "WaveNet", "name": "legacy.nam"},
            {"architecture": "custom", "name": "custom.nam"},
            {"architecture": "Proteus", "name": "capture.wav"},
            {"architecture": "FutureRuntime", "name": "capture.wav"},
            {"architecture_version": "2", "format": "aida-x"}):
        assert not tone3000.is_supported_model(model, {"gear": "cab"})

    assert not tone3000.is_supported_model(
        {"architecture": "FutureRuntime"}, {"gear": "cab", "format": "ir"})


@pytest.mark.parametrize("model", [
    {"architecture_version": "2", "architecture": "custom",
     "name": "conflict.nam"},
    {"architecture_version": "2", "architecture": "WaveNet",
     "name": "conflict.nam"},
    {"architecture_version": "2", "architecture": "Proteus",
     "name": "conflict.nam"},
    {"architecture_version": "2", "architecture": "FutureRuntime",
     "name": "conflict.nam"},
    {"architecture_version": "2", "architecture": "IR",
     "name": "conflict.nam"},
])
def test_supported_model_classifier_rejects_conflicting_architecture_fields(model):
    assert not tone3000.is_supported_model(model)


def test_search_uses_the_requested_supported_count_for_each_architecture_view(
        monkeypatch):
    def fake_post(_url, body):
        if body["architecture_filter"] == "2":
            return {"data": [
                {"id": 1, "a2_models_count": 0, "irs_count": 2},
                {"id": 2, "a2_models_count": 1, "irs_count": 0},
            ], "total_pages": 1}
        return {"data": [
            {"id": 3, "a2_models_count": 2, "irs_count": 0},
            {"id": 4, "a2_models_count": 1, "irs_count": 1},
        ], "total_pages": 1}

    monkeypatch.setattr(tone3000, "_post", fake_post)

    assert [row["id"] for row in tone3000.search("mixed", page_size=10)] == [
        2, 4]
    assert [row["id"] for row in tone3000.search(
        "mixed", page_size=10, gear_filters=["ir"])] == [4]


def test_supported_model_count_prefers_explicit_model_rows():
    tone = {
        "a2_models_count": 9,
        "irs_count": 2,
        "local_dir": "data/tones/1-pack",
        "models": [
            {"architecture_version": "2", "name": "amp.nam"},
            {"architecture": "IR", "name": "cab.wav"},
            {"architecture": "WaveNet", "name": "legacy.nam"},
            {"architecture": "custom", "name": "custom.nam"},
        ],
    }

    assert tone3000.supported_tone_model_count(tone) == 2


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


def test_logout_clears_persisted_tokens(monkeypatch, tmp_path):
    token_file = tmp_path / "tokens.json"
    monkeypatch.setattr(tone3000, "TOKEN_FILE", token_file)
    tone3000._write_tokens({"access_token": "access"})
    assert token_file.exists()

    assert tone3000.logout() is True
    assert not token_file.exists()


def test_cli_exposes_tone_logout(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(library.tone3000, "logout",
                        lambda: called.append(True) or True)

    assert library.main(["tone", "logout"]) == 0
    assert called == [True]
    assert "logout complete" in capsys.readouterr().out.lower()


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
        "id": 1, "model_url": "https://cdn.example/model.nam", "name": "model.nam",
        "architecture_version": "2"
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
    monkeypatch.setattr(
        tone3000, "models",
        lambda *_args, **_kwargs: [{
            "name": "Clean.nam", "architecture_version": "2",
        }])

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
