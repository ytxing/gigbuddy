#!/usr/bin/env python3
"""GigBuddy TONE3000 integration (zero local database dependencies).

The integration uses TONE3000's documented OAuth 2.0 + PKCE flow and its
authenticated ``/api/v1`` REST API.  Access and refresh tokens are kept in the
user's config directory; no server-side secret is required for this desktop
application.

CLI:
    tone3000.py search <query>              # 关键词搜索（A2 + IR）
    tone3000.py top [limit]                 # 全站下载排行
    tone3000.py models <tone_id>            # 列出 tone 的 A2 模型
    tone3000.py download <tone_id> <dest>   # 下载全部 A2 .nam 到目录
    tone3000.py dry <dest> [name...]        # 下载试听干音素材（MIT，mayer/brit/rollin 等）
"""
import base64
from dataclasses import dataclass, field
import json
import hashlib
import http.client
import http.server
import os
import re
import secrets
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path, PurePosixPath


@dataclass
class SearchPage:
    rows: list[dict]
    loaded_count: int
    has_more: bool
    exhausted: bool


@dataclass
class _SearchSourceState:
    architecture_filter: str | None
    gear_filters: tuple[str, ...] | None
    next_page: int = 1
    rows: list[dict] = field(default_factory=list)
    seen_ids: set[int] = field(default_factory=set)
    exhausted: bool = False


@dataclass
class _SearchState:
    sources: list[_SearchSourceState]


_SEARCH_STATES: dict[tuple, _SearchState] = {}


def slugify(text, maxlen=48):
    """'Fender Super Reverb 1977' -> 'fender-super-reverb-1977' (empty -> 'tone')"""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:maxlen].rstrip("-") or "tone")

TONE3000_ORIGIN = "https://www.tone3000.com"
API = f"{TONE3000_ORIGIN}/api/v1"
# The current REST API does not expose the public aggregate view used by the
# library's download/favorites leaderboards. Keep this read-only compatibility
# endpoint isolated from the OAuth-backed API above.
LEGACY_API = "https://api.tone3000.com/rest/v1"
LEGACY_ANON_KEY = os.environ.get(
    "TONE3000_SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd6eWJpdW9weGtkeGJ5dG5vamRzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzgwODIxNjUsImV4cCI6MjA1MzY1ODE2NX0."
    "Gq66BJXjtLsqP2nAGXm9Xb9PAjoeZalWUj66K4nmVSU")
DEFAULT_CLIENT_ID = "t3k_pub_JYKns9gy0ua38l1n9eICrPVn_P6jeAYG"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"

# OAuth callback 成功页。字符画为 dos_rebel 字体的 GIGBUDDY，每行固定 96
# 字符（含行尾空格），行尾空格是右缘对齐的一部分，切勿删除；行内 ▒ 为
# U+2592 中等阴影块。字体本身是斜体设计（rebel = 倾斜），左缘逐行内缩属
# 正常特征，不是错位。
_CALLBACK_ART = [
    "   █████████  █████   █████████  ███████████  █████  █████ ██████████   ██████████   █████ █████",
    "  ███▒▒▒▒▒███▒▒███   ███▒▒▒▒▒███▒▒███▒▒▒▒▒███▒▒███  ▒▒███ ▒▒███▒▒▒▒███ ▒▒███▒▒▒▒███ ▒▒███ ▒▒███ ",
    " ███     ▒▒▒  ▒███  ███     ▒▒▒  ▒███    ▒███ ▒███   ▒███  ▒███   ▒▒███ ▒███   ▒▒███ ▒▒███ ███  ",
    "▒███          ▒███ ▒███          ▒██████████  ▒███   ▒███  ▒███    ▒███ ▒███    ▒███  ▒▒█████   ",
    "▒███    █████ ▒███ ▒███    █████ ▒███▒▒▒▒▒███ ▒███   ▒███  ▒███    ▒███ ▒███    ▒███   ▒▒███    ",
    "▒▒███  ▒▒███  ▒███ ▒▒███  ▒▒███  ▒███    ▒███ ▒███   ▒███  ▒███    ███  ▒███    ███     ▒███    ",
    " ▒▒█████████  █████ ▒▒█████████  ███████████  ▒▒████████   ██████████   ██████████      █████   ",
    "  ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒▒▒▒▒▒▒    ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒▒▒      ▒▒▒▒▒    ",
]

# Normal text is intentional here: the browser's font fallback can corrupt
# box-drawing glyphs, while these messages must remain readable everywhere.
_CALLBACK_MESSAGES = (
    "Login successful",
    "You can return to GigBuddy now.",
)

_TONE3000_LOGO_SVG = (
    Path(__file__).with_name("tone3000_logo.svg")
    .read_text(encoding="utf-8")
    .strip()
)

_CALLBACK_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GigBuddy - Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root { color-scheme: dark; }
  html { min-height: 100%; }
  body {
    background:
      radial-gradient(1200px 600px at 50% -10%, #3d2e1f, transparent 60%),
      #1b1512;
    color: #f0e2cc;
    font-family: -apple-system, "Segoe UI", "Helvetica Neue", sans-serif;
    min-height: 100svh; display: flex; align-items: center;
    justify-content: center; text-align: center; padding: 24px 20px;
    overflow-x: hidden;
  }
  .callback-content {
    width: 100%; display: flex; flex-direction: column; align-items: center;
    gap: clamp(12px, 2.5vh, 22px);
  }
  .powered-by {
    display: flex; align-items: center; justify-content: center;
    gap: 10px; color: #c9b18b; font-size: clamp(11px, 1.2vw, 14px);
    line-height: 1; font-weight: 600; letter-spacing: 0;
    opacity: 0.82;
  }
  .powered-by svg {
    display: block; width: min(210px, 38vw); height: auto;
  }
  .powered-by a {
    display: block; color: inherit; line-height: 0;
  }
  .logo-viewport {
    width: 100%; overflow-x: auto; overflow-y: hidden;
    display: flex; justify-content: safe center;
    scrollbar-width: thin;
    scrollbar-color: rgba(245, 176, 66, 0.45) transparent;
  }
  .logo-viewport { padding: 0 10px; }
  pre {
    font-family: "Menlo", "SF Mono", Consolas, "Liberation Mono", monospace;
    white-space: pre; font-variant-ligatures: none;
  }
  pre.logo {
    /* 等宽字体保证 █▒ 方块字符对齐 */
    /* 96 字符按视口自适应缩放，避免窄窗口横向溢出；
       等宽字体字符实际宽约 0.59em，按 58 折算取整 */
    font-size: clamp(10px, calc((100vw - 80px) / 58), 22px);
    line-height: 1.15; font-weight: bold;
    /* 多段金渐变背景横向流动 = 波浪效果 */
    background: linear-gradient(90deg,
      #8f6b46, #f5b042, #e59a3c, #f5b042, #8f6b46, #f5b042, #8f6b46);
    background-size: 300% 100%;
    -webkit-background-clip: text; background-clip: text; color: transparent;
    filter: drop-shadow(0 6px 24px rgba(229, 154, 60, 0.35));
    animation: wave 5s linear infinite;
    flex: 0 0 auto; width: max-content;
  }
  @keyframes wave {
    from { background-position: 0% 0; }
    to   { background-position: 300% 0; }
  }
  .logo-viewport::-webkit-scrollbar {
    height: 5px;
  }
  .logo-viewport::-webkit-scrollbar-thumb {
    background: rgba(245, 176, 66, 0.45); border-radius: 5px;
  }
  .success-copy {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    color: #d6bf98; font-size: clamp(14px, 1.8vw, 18px);
    line-height: 1.35; font-weight: 600; letter-spacing: 0;
    text-shadow: 0 3px 18px rgba(229, 154, 60, 0.2);
  }
  .success-copy p { margin: 0; }
  @media (max-height: 620px) {
    body { justify-content: flex-start; overflow-y: auto; }
    .callback-content { margin-block: auto; }
  }
  @media (prefers-reduced-motion: reduce) {
    pre.logo { animation: none; }
  }
</style>
</head>
<body>
<main class="callback-content">
<div class="powered-by">
<span>Powered by</span>
<a href="https://www.tone3000.com" target="_blank"
   rel="noopener noreferrer" aria-label="Open TONE3000 website">
""" + _TONE3000_LOGO_SVG + """
</a>
</div>
<div class="logo-viewport">
<pre class="logo" role="img" aria-label="GigBuddy">""" + "\n".join(_CALLBACK_ART) + """</pre>
</div>
<div class="success-copy">
""" + "\n".join(f"<p>{message}</p>" for message in _CALLBACK_MESSAGES) + """
</div>
</main>
</body>
</html>
"""
TOKEN_FILE = Path.home() / ".config" / "gigbuddy" / "tone3000_tokens.json"
_MIN_REQUEST_INTERVAL = 0.6  # documented default: 100 requests per minute
_last_request_at = 0.0
_request_lock = threading.Lock()
_token_lock = threading.RLock()

ROOT = Path(__file__).resolve().parent.parent
VERIFIED_FILE = ROOT / "data" / "verified_users.json"
_verified_cache: set[str] | None = None
_verified_write_lock = threading.Lock()

# tone3000.com sits behind Cloudflare: the default urllib UA gets the
# __next_error__ page, but a full browser UA passes through.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")


class AuthenticationRequiredError(RuntimeError):
    """The current user must complete the TONE3000 login flow."""


class Tone3000HTTPError(RuntimeError):
    """An HTTP error returned by the documented TONE3000 API."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = int(status)
        self.message = str(message or "")
        detail = f"TONE3000 API returned HTTP {self.status}"
        if self.message:
            detail += f": {self.message}"
        super().__init__(detail)


def _client_id() -> str:
    return os.environ.get("TONE3000_CLIENT_ID", DEFAULT_CLIENT_ID).strip()


def _redirect_uri() -> str:
    return os.environ.get("TONE3000_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()


def _pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(*, code_verifier: str, state: str,
                      redirect_uri: str | None = None) -> str:
    """Build the official OAuth authorization URL using S256 PKCE."""
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri or _redirect_uri(),
        "response_type": "code",
        "code_challenge": _pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{TONE3000_ORIGIN}/api/v1/oauth/authorize?{urllib.parse.urlencode(params)}"


def _read_tokens() -> dict:
    try:
        value = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_tokens(tokens: dict) -> None:
    """Persist OAuth tokens with a user-only file mode."""
    with _token_lock:
        payload = dict(tokens or {})
        if "expires_at" not in payload:
            try:
                expires_in = float(payload.get("expires_in", 0))
            except (TypeError, ValueError):
                expires_in = 0.0
            payload["expires_at"] = time.time() + max(expires_in, 0.0)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            TOKEN_FILE.parent.chmod(0o700)
        except OSError:
            pass
        temporary = TOKEN_FILE.with_name(f".{TOKEN_FILE.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(TOKEN_FILE)
        TOKEN_FILE.chmod(0o600)


def _clear_tokens() -> None:
    """Remove unusable persisted credentials so the next request can log in."""
    with _token_lock:
        try:
            TOKEN_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A read-only config directory should not hide the actual auth error.
            pass


def logout() -> bool:
    """Clear the persisted OAuth credentials used by this desktop client.

    An explicit ``TONE3000_ACCESS_TOKEN`` environment variable is outside the
    persisted login session and is therefore left untouched. Return whether
    no such environment credential remains active.
    """
    _clear_tokens()
    return not bool(os.environ.get("TONE3000_ACCESS_TOKEN", "").strip())


def _token_request(body: dict) -> dict:
    request = urllib.request.Request(
        f"{API}/oauth/token",
        data=urllib.parse.urlencode(body).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json", "User-Agent": BROWSER_UA},
        method="POST",
    )
    result = _open_json(request)
    if not isinstance(result, dict) or not result.get("access_token"):
        raise AuthenticationRequiredError("TONE3000 token response was invalid")
    return result


def access_token(*, force_refresh: bool = False) -> str:
    """Return a usable access token, refreshing it before expiry."""
    env_token = os.environ.get("TONE3000_ACCESS_TOKEN", "").strip()
    # An explicit environment token is an isolated credential source. Keep
    # using it on a forced retry rather than silently switching identities to
    # the OAuth token stored on disk.
    if env_token:
        return env_token
    # Several download-state workers can discover an expired token at once.
    # Serialize the refresh and re-read the file after waiting so only the
    # first worker consumes the refresh token.
    with _token_lock:
        tokens = _read_tokens()
        token = str(tokens.get("access_token") or "")
        try:
            expires_at = float(tokens.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0.0
        if token and not force_refresh and time.time() < expires_at - 60:
            return token
        refresh = str(tokens.get("refresh_token") or "")
        if refresh:
            try:
                fresh = _token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": _client_id(),
                })
            except Tone3000HTTPError as exc:
                if exc.status == 400 and "invalid_grant" in exc.message.casefold():
                    _clear_tokens()
                    raise AuthenticationRequiredError(
                        "TONE3000 login expired; log in again.") from exc
                raise
            if not fresh.get("refresh_token"):
                fresh["refresh_token"] = refresh
            _write_tokens(fresh)
            return str(fresh["access_token"])
        raise AuthenticationRequiredError(
            "TONE3000 login required; run `gigbuddy tone login`.")


def login(*, timeout: float = 300, open_browser: bool = True,
          redirect_uri: str | None = None) -> dict:
    """Run the local-browser OAuth callback flow and save the resulting tokens."""
    redirect_uri = redirect_uri or _redirect_uri()
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("TONE3000 redirect_uri must be a local HTTP callback")
    if parsed.path != "/oauth/callback":
        raise ValueError("TONE3000 redirect_uri path must be /oauth/callback")
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    url = authorization_url(code_verifier=verifier, state=state,
                            redirect_uri=redirect_uri)
    result: dict[str, str] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler hook
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            returned_state = query.get("state", [""])[0]
            if returned_state != state:
                result["error"] = "OAuth state mismatch"
            elif query.get("error"):
                result["error"] = query.get(
                    "error_description", query.get("error", ["login failed"]))[0]
            elif query.get("code"):
                result["code"] = query["code"][0]
            else:
                result["error"] = "OAuth callback did not contain a code"
            self.send_response(200 if "code" in result else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_CALLBACK_PAGE.encode("utf-8"))

        def log_message(self, *_args):
            return

    port = parsed.port or 8765
    try:
        server = http.server.HTTPServer((parsed.hostname, port), CallbackHandler)
    except OSError as exc:
        raise AuthenticationRequiredError(
            f"Cannot listen for the TONE3000 OAuth callback at {redirect_uri}: {exc}"
        ) from exc
    server.timeout = min(1.0, max(float(timeout), 0.05))
    deadline = time.monotonic() + max(float(timeout), 0.05)
    print("TONE3000 login URL (copy this if the browser does not open):",
          flush=True)
    print(url, flush=True)
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            print("The browser did not open automatically; copy the URL above.",
                  flush=True)
    try:
        while time.monotonic() < deadline and not result:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise AuthenticationRequiredError(result["error"])
    if not result.get("code"):
        raise AuthenticationRequiredError("TONE3000 login timed out")
    tokens = _token_request({
        "grant_type": "authorization_code",
        "code": result["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": _client_id(),
    })
    _write_tokens(tokens)
    return tokens


def _canonical_tone(row: dict) -> dict:
    """Normalize documented API rows to the shape used by the local library."""
    if not isinstance(row, dict):
        return row
    user = row.get("user")
    if isinstance(user, dict):
        for target, source in (("user_id", "id"), ("username", "username"),
                               ("avatar_url", "avatar_url"),
                               ("user_url", "url")):
            if row.get(target) in (None, "") and user.get(source) not in (None, ""):
                row[target] = user[source]
    if row.get("user_url") in (None, "") and row.get("username"):
        row["user_url"] = f"{TONE3000_ORIGIN}/{row['username']}"
    if row.get("format") in (None, "") and row.get("platform") not in (None, ""):
        row["format"] = row["platform"]
    if row.get("platform") in (None, "") and row.get("format") not in (None, ""):
        row["platform"] = row["format"]
    for key in ("tags", "makes"):
        value = row.get(key)
        if isinstance(value, list):
            names = []
            for item in value:
                name = item.get("name") if isinstance(item, dict) else item
                if name not in (None, ""):
                    names.append(str(name))
            row[key] = sorted(names, key=str.casefold)
    return row


def _canonical_tones(rows):
    return [_canonical_tone(row) for row in _response_rows(rows)]


_A2_ARCHITECTURE_TOKENS = frozenset({"2", "a2", "slimmablecontainer"})
_A1_ARCHITECTURE_TOKENS = frozenset({"1", "a1", "wave", "wavenet"})
_IR_ARCHITECTURE_TOKENS = frozenset({"ir"})


def _architecture_family(model: dict) -> str | None:
    """Resolve both architecture fields, rejecting contradictory metadata."""
    if not isinstance(model, dict):
        return None
    families = []
    for key in ("architecture_version", "architecture"):
        token = str(model.get(key) or "").strip().casefold()
        if not token:
            continue
        if token in _A2_ARCHITECTURE_TOKENS:
            families.append("a2")
        elif token in _A1_ARCHITECTURE_TOKENS:
            families.append("a1")
        elif token in _IR_ARCHITECTURE_TOKENS:
            families.append("ir")
        else:
            families.append("unknown")
    if not families:
        return None
    return families[0] if len(set(families)) == 1 else "conflict"


def _is_a2_model(model: dict) -> bool:
    """Accept A2 only when all explicit architecture fields agree."""
    return _architecture_family(model) == "a2"


def _is_a1_model(model: dict) -> bool:
    """A1 is the deprecated WaveNet runtime; the product filters it out.

    TONE3000 exposes the backend token ``WaveNet`` (plus the legacy ``1``/``a1``
    values) for A1 rows. Callers that fetch a full model set must drop these
    rows so A1 files are never downloaded, browsed, or shown.
    """
    return _architecture_family(model) == "a1"


_IR_SUFFIXES = frozenset({".wav", ".wave", ".flac", ".aif", ".aiff"})
_NAM_SUFFIXES = frozenset({".nam", ".aida-x", ".aa-snapshot", ".proteus"})
_SUPPORTED_NAM_SUFFIXES = frozenset({".nam"})
_IR_GEAR_TOKENS = frozenset({"cab", "space", "ir"})
_SUPPORTED_TONE_FORMATS = frozenset({"nam", "ir"})


def _has_supported_formats(model: dict, tone: dict | None = None) -> bool:
    """Reject explicit non-NAM/non-IR formats before architecture inference."""
    model = model if isinstance(model, dict) else {}
    tone = tone if isinstance(tone, dict) else {}
    model_format = str(model.get("format") or "").strip().casefold()
    tone_format = str(
        tone.get("format") or tone.get("platform") or ""
    ).strip().casefold()
    return all(not value or value in _SUPPORTED_TONE_FORMATS
               for value in (model_format, tone_format))


def _model_file_suffix(model: dict) -> str:
    """Return a model's source/file suffix without trusting its URL query."""
    for key in ("local_path", "name", "model_url", "url"):
        raw = str(model.get(key) or "").strip()
        if not raw:
            continue
        source = raw.split("?", 1)[0].replace("\\", "/")
        suffix = PurePosixPath(source).suffix.casefold()
        if suffix:
            return suffix
    return ""


def _is_ir_model(model: dict, tone: dict | None = None) -> bool:
    """Recognize an IR row when TONE3000 leaves architecture unset."""
    model = model if isinstance(model, dict) else {}
    tone = tone if isinstance(tone, dict) else {}
    if not _has_supported_formats(model, tone):
        return False
    model_format = str(model.get("format") or "").strip().casefold()
    tone_format = str(tone.get("format") or "").strip().casefold()
    if not tone_format and str(tone.get("platform") or "").strip().casefold() == "ir":
        tone_format = "ir"
    family = _architecture_family(model)
    if family == "ir":
        return True
    if family is not None:
        # A non-empty architecture is authoritative. In particular, a custom
        # or future NAM architecture on a CAB tone must not be guessed as IR.
        return False

    explicit_format = model_format or tone_format
    if explicit_format:
        return explicit_format == "ir"

    suffix = _model_file_suffix(model)
    if suffix in _IR_SUFFIXES:
        return True
    if suffix in _NAM_SUFFIXES:
        return False

    return str(tone.get("gear") or "").strip().casefold() in _IR_GEAR_TOKENS


def is_supported_model(model: dict, tone: dict | None = None) -> bool:
    """Return whether GigBuddy can expose or load this model.

    GigBuddy intentionally supports only NAM A2 and IR. Unknown model rows
    fail closed so a new architecture cannot silently enter the UI or import
    path before the product explicitly supports it.
    """
    model = model if isinstance(model, dict) else {}
    tone = tone if isinstance(tone, dict) else {}
    if not _has_supported_formats(model, tone):
        return False
    suffix = _model_file_suffix(model)
    if suffix and suffix not in (_SUPPORTED_NAM_SUFFIXES | _IR_SUFFIXES):
        return False

    family = _architecture_family(model)
    if family in {"a1", "unknown", "conflict"}:
        return False
    is_a2 = family == "a2"
    is_ir = family == "ir" or (family is None and _is_ir_model(model, tone))
    model_format = str(model.get("format") or "").strip().casefold()
    tone_format = str(
        tone.get("format") or tone.get("platform") or ""
    ).strip().casefold()
    if is_a2 and (model_format == "ir"
                  or (not model_format and tone_format == "ir")):
        return False
    if is_ir and model_format == "nam":
        return False
    if suffix in _SUPPORTED_NAM_SUFFIXES:
        return is_a2
    if suffix in _IR_SUFFIXES:
        return is_ir
    return is_a2 or is_ir


def verified_users() -> set[str]:
    """Usernames carrying TONE3000's "Verified Profiles" badge.

    The badge is rendered from server-side data the public REST API does not
    expose (users table has no flag), so the list is mirrored from the website
    author pages into data/verified_users.json; verify_username() adds entries
    on demand and scripts/fetch_verified_users.py refreshes the whole set.
    Falls back to empty on missing/corrupt file so callers never break.
    """
    global _verified_cache
    if _verified_cache is None:
        try:
            with VERIFIED_FILE.open() as fh:
                _verified_cache = set(json.load(fh).get("users") or [])
        except (OSError, ValueError, TypeError, KeyError):
            _verified_cache = set()
    return _verified_cache


def is_verified(username: str | None) -> bool:
    """Return whether a username is present in the persisted positive cache."""
    name = str(username or "").lower()
    return bool(name) and name in verified_users()


def verify_username(username: str, *, timeout: float = 8.0) -> bool | None:
    """Check the "Verified Profiles" badge for one author, live.

    The badge is server-rendered and the REST API exposes no flag, so the
    author page is fetched (full browser UA passes Cloudflare) and scanned for
    the badge's "Verified Profiles" tooltip string. Returns True/False on
    success (True is persisted into verified_users.json so later lookups are
    free); None on network/parse failure — callers then keep showing no badge
    and may retry on the next detail view.
    """
    name = str(username or "").lower()
    if not name:
        return False
    if name in verified_users():
        return True
    req = urllib.request.Request(
        f"https://www.tone3000.com/{name}?_data",
        headers={"User-Agent": BROWSER_UA,
                 "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read(2 * 1024 * 1024)  # marker sits early in page
    except Exception:
        return None
    ok = b"Verified Profiles" in html
    if ok:
        _remember_verified(name)
    return ok


def _remember_verified(name: str) -> None:
    """Persist a freshly confirmed verification (atomic file replace)."""
    global _verified_cache
    with _verified_write_lock:
        if _verified_cache is None:
            verified_users()
        _verified_cache.add(name)
        try:
            data = json.loads(VERIFIED_FILE.read_text())
        except (OSError, ValueError, TypeError):
            data = {"note": "TONE3000 'Verified Profiles' usernames."}
        users = set(data.get("users") or []) | {name}
        data["users"] = sorted(users)
        data["verified_at"] = time.strftime("%Y-%m-%d")
        tmp = VERIFIED_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(VERIFIED_FILE)


def _open_json(req):
    """Read JSON with bounded TLS and rate-limit retries."""
    transient = (urllib.error.URLError, ssl.SSLError, TimeoutError,
                 ConnectionResetError, http.client.IncompleteRead)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                retry_after = (exc.headers.get("Retry-After")
                               if exc.headers is not None else None)
                try:
                    if retry_after is None:
                        raise ValueError
                    delay = max(float(retry_after), 0.0)
                except (TypeError, ValueError):
                    delay = 0.5 * (2 ** attempt)
                time.sleep(delay)
                continue
            try:
                payload = json.loads(exc.read())
            except (OSError, ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                message = (payload.get("error_description")
                           or payload.get("error") or payload.get("message"))
            else:
                message = None
            raise Tone3000HTTPError(exc.code, message or exc.reason) from exc
        except transient:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))


def _request_json(url: str, *, params: dict | None = None,
                  method: str = "GET", body: dict | None = None,
                  authenticated: bool = True):
    """Issue one documented API request and retry one expired bearer token."""
    global _last_request_at
    query = {key: value for key, value in (params or {}).items()
             if value not in (None, "")}
    if query:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode(query)
    encoded = None
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": BROWSER_UA}
    if url.startswith(LEGACY_API):
        headers.update({"apikey": LEGACY_ANON_KEY,
                        "Authorization": f"Bearer {LEGACY_ANON_KEY}",
                        "Content-Profile": "public"})
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
    for attempt in range(2):
        if authenticated:
            token = access_token(force_refresh=attempt == 1)
            headers["Authorization"] = f"Bearer {token}"
        if _MIN_REQUEST_INTERVAL > 0:
            with _request_lock:
                now = time.monotonic()
                delay = _MIN_REQUEST_INTERVAL - (now - _last_request_at)
                if delay > 0:
                    time.sleep(delay)
                _last_request_at = time.monotonic()
        request = urllib.request.Request(
            url, data=encoded, headers=headers, method=method)
        try:
            return _open_json(request)
        except Tone3000HTTPError as exc:
            if authenticated and exc.status == 401 and attempt == 0:
                continue
            raise


def _get(url, **params):
    """GET an official endpoint; retain an unauthenticated utility seam."""
    return _request_json(
        url, params=params, authenticated=url.startswith(API))


def _response_rows(response) -> list[dict]:
    """Extract rows from an official paginated response or a legacy list."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        rows = response.get("data")
        if isinstance(rows, list):
            return rows
    return []


def _response_object(response) -> dict | None:
    """Extract one object from a direct or envelope-style API response."""
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict):
            return data
        return response
    if isinstance(response, list) and response and isinstance(response[0], dict):
        return response[0]
    return None


def current_user() -> dict:
    """Return the profile belonging to the active OAuth credentials."""
    profile = _response_object(_request_json(
        f"{API}/user", authenticated=True))
    return dict(profile) if isinstance(profile, dict) else {}


def _canonical_creator(row: dict) -> dict:
    """Keep the creator aggregate names used by the existing TUI."""
    result = dict(row)
    if result.get("public_tones_count") is None:
        result["public_tones_count"] = result.get("tones_count") or 0
    if result.get("public_models_count") is None:
        result["public_models_count"] = result.get("models_count") or 0
    return result


def _post(url, body):
    """POST helper; search bodies are translated to official REST query params."""
    if url == f"{API}/tones/search":
        gears = list(body.get("gear_filters") or ())
        format_filter = "ir" if "ir" in gears else None
        gears = [gear for gear in gears if gear != "ir"]
        params = {
            "query": body.get("query_term", ""),
            "page": body.get("page_number", 1),
            "page_size": 25,
            "sort": body.get("order_by", "trending"),
            "gears": "_".join(gears or ()),
            "format": format_filter,
            "tags": "_".join(body.get("tag_names") or ()),
            "makes": "_".join(body.get("make_names") or ()),
            "creators": ",".join(body.get("usernames") or ()),
            "architecture": body.get("architecture_filter"),
        }
        return _get(url, **params)
    return _request_json(url, method="POST", body=body,
                         authenticated=url.startswith(API))


_IR_GEAR_FILTERS = frozenset({"ir"})
_SUPPORTED_MODEL_COUNTS = ("a2_models_count", "irs_count")


def _has_supported_tone_models(
        row: dict, *, architecture_filter: str | None = None,
        gear_filters=None) -> bool:
    """Keep tones with at least one model the product can expose."""
    row = row if isinstance(row, dict) else {}
    format_token = str(
        row.get("format") or row.get("platform") or ""
    ).strip().casefold()
    if format_token and format_token not in _SUPPORTED_TONE_FORMATS:
        return False
    models = row.get("models")
    if isinstance(models, (list, tuple)):
        return any(
            is_supported_model(model, row)
            for model in models if isinstance(model, dict)
        )
    architecture_token = str(architecture_filter or "").strip().casefold()
    source_gears = {
        str(value).strip().casefold() for value in (gear_filters or ()) if value
    }
    # A source-specific aggregate must prove the architecture represented by
    # that source. This matters for mixed Packs and for exact ``--gear ir``
    # searches: a positive A2 count alone must not make an IR-only view visible,
    # and vice versa.
    if architecture_token in {"2", "a2"}:
        if "a2_models_count" in row:
            try:
                return int(row.get("a2_models_count") or 0) > 0
            except (TypeError, ValueError):
                return False
        if any(key in row for key in _SUPPORTED_MODEL_COUNTS):
            return False
    if source_gears & _IR_GEAR_FILTERS and architecture_filter is None:
        if "irs_count" in row:
            try:
                return int(row.get("irs_count") or 0) > 0
            except (TypeError, ValueError):
                return False
        if any(key in row for key in _SUPPORTED_MODEL_COUNTS):
            return False
    supported_counts = []
    for key in _SUPPORTED_MODEL_COUNTS:
        if key not in row:
            continue
        try:
            supported_counts.append(int(row.get(key) or 0))
        except (TypeError, ValueError):
            continue
    if supported_counts:
        return any(value > 0 for value in supported_counts)
    # The A2 search view is already an authoritative server-side filter. Do
    # not drop a valid result merely because an older response omitted the
    # aggregate a2_models_count field.
    if str(architecture_filter or "").strip().casefold() in {"2", "a2"}:
        return format_token in {"", "nam"}
    if format_token == "ir":
        return True
    # A NAM row cannot be admitted to the IR view just because its gear is
    # cab/space. The server-side filter is useful evidence only when the row
    # does not carry contradictory format metadata.
    if format_token == "nam":
        return False
    # _post sends format=ir for CAB/SPACE/IR sources. Trust that explicit
    # server-side view when older responses omit both format and irs_count;
    # the model-level classifier still guards the Pack and download paths.
    if source_gears & _IR_GEAR_FILTERS:
        return True
    return False


def supported_tone_model_count(row: dict | None) -> int:
    """Return the product-visible A2 plus IR count for one Tone row."""
    row = row if isinstance(row, dict) else {}
    models = row.get("models")
    has_local_model_evidence = any(
        isinstance(model, dict) and model.get("local_path")
        for model in (models or ())
    )
    model_count = None
    if isinstance(models, (list, tuple)) and models:
        model_count = sum(
            is_supported_model(model, row)
            for model in models if isinstance(model, dict)
        )
        if has_local_model_evidence:
            # A local pack's model list is the downloaded subset, while the
            # aggregate A2/IR counters describe the complete remote pack.
            # Keep the larger supported value so partial packs show missing
            # rows without allowing A1/Custom counters into the total.
            aggregate = 0
            for key in _SUPPORTED_MODEL_COUNTS:
                try:
                    aggregate += int(row.get(key) or 0)
                except (TypeError, ValueError):
                    continue
            return max(model_count, aggregate)
        if row.get("local_dir"):
            # An imported full-detail response has model-level evidence. Prefer
            # it over aggregate counters, which may include unsupported rows.
            return model_count
    counts = []
    for key in _SUPPORTED_MODEL_COUNTS:
        if key not in row:
            continue
        try:
            counts.append(int(row.get(key) or 0))
        except (TypeError, ValueError):
            continue
    total = sum(counts)
    if total:
        return total
    return model_count or 0


def _search_sources(gear_filters):
    """Return the supported catalog views needed for one search."""
    gears = tuple(str(value).strip().casefold()
                  for value in (gear_filters or ()) if value)
    if not gears:
        return [("2", None), (None, ("ir",))]

    nam_gears = tuple(value for value in gears
                      if value not in _IR_GEAR_FILTERS)
    ir_gears = tuple(value for value in gears
                     if value in _IR_GEAR_FILTERS)
    if any(value in {"cab", "space"} for value in gears):
        ir_gears = tuple(dict.fromkeys(ir_gears + tuple(
            value for value in gears if value in {"cab", "space"})))
    sources = []
    if nam_gears:
        sources.append(("2", nam_gears))
    if ir_gears:
        sources.append((None, ir_gears))
    return sources


def _fetch_search_prefix(query, limit, order_by, gear_filters, usernames,
                         tag_names, make_names, architecture_filter):
    """Fetch a bounded supported prefix from one official catalog view."""
    rows: list[dict] = []
    total = None
    page = 1
    exhausted = False
    while len(rows) < limit:
        response = _post(f"{API}/tones/search", {
            "query_term": query,
            "page_number": page,
            "page_size": 25,
            "order_by": order_by,
            "tag_names": tag_names,
            "make_names": make_names,
            "gear_filters": gear_filters,
            "is_calibrated": False,
            "size_filters": None,
            "usernames": usernames,
            "architecture_filter": architecture_filter,
        })
        if isinstance(response, dict):
            page_rows = response.get("data") or []
            total = response.get("total", total)
            total_pages = response.get("total_pages")
        else:
            page_rows = response or []
            total_pages = None
        if not isinstance(page_rows, list):
            page_rows = []
        if not page_rows:
            exhausted = True
            break
        rows.extend(
            row for row in _canonical_tones(page_rows)
            if isinstance(row, dict) and _has_supported_tone_models(
                row, architecture_filter=architecture_filter,
                gear_filters=gear_filters)
        )
        if total_pages is not None:
            try:
                if page >= int(total_pages):
                    exhausted = True
                    break
            except (TypeError, ValueError):
                pass
        if len(page_rows) < 25:
            exhausted = True
            break
        page += 1
    return (rows if exhausted else rows[:limit]), total, exhausted


def _search_identity(query, order_by, gear_filters, usernames, tag_names,
                     make_names):
    def normalized(values):
        return tuple(sorted(str(value).strip().casefold()
                            for value in (values or ()) if value))

    return (str(query or ""), str(order_by or "trending"),
            normalized(gear_filters), normalized(usernames),
            normalized(tag_names), normalized(make_names))


def _fetch_next_source_page(state, *, query, order_by, usernames, tag_names,
                            make_names):
    if state.exhausted:
        return
    response = _post(f"{API}/tones/search", {
        "query_term": query,
        "page_number": state.next_page,
        "page_size": 25,
        "order_by": order_by,
        "tag_names": tag_names,
        "make_names": make_names,
        "gear_filters": state.gear_filters,
        "is_calibrated": False,
        "size_filters": None,
        "usernames": usernames,
        "architecture_filter": state.architecture_filter,
    })
    if isinstance(response, dict):
        page_rows = response.get("data") or []
        total_pages = response.get("total_pages")
    else:
        page_rows, total_pages = response or [], None
    if not isinstance(page_rows, list) or not page_rows:
        state.exhausted = True
        return
    canonical = [row for row in _canonical_tones(page_rows)
                 if isinstance(row, dict) and _has_supported_tone_models(
                     row, architecture_filter=state.architecture_filter,
                     gear_filters=state.gear_filters)]
    for row in canonical:
        try:
            tone_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            tone_id = None
        if tone_id is None or tone_id not in state.seen_ids:
            state.rows.append(row)
            if tone_id is not None:
                state.seen_ids.add(tone_id)
    state.next_page += 1
    try:
        state.exhausted = state.next_page > int(total_pages)
    except (TypeError, ValueError):
        state.exhausted = len(page_rows) < 25


def _merge_search_sources(source_rows):
    """Interleave source rankings while removing mixed-pack duplicates."""
    merged: list[dict] = []
    seen: set[int] = set()
    for index in range(max((len(rows) for rows in source_rows), default=0)):
        for rows in source_rows:
            if index >= len(rows):
                continue
            row = rows[index]
            try:
                tone_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                tone_id = None
            if tone_id is not None:
                if tone_id in seen:
                    continue
                seen.add(tone_id)
            merged.append(row)
    return merged


def _sort_search_rows(rows, order_by):
    field = {
        "downloads": "downloads_count",
        "downloads-all-time": "downloads_count",
        "downloads_count": "downloads_count",
        "favorites": "favorites_count",
        "favorites_count": "favorites_count",
        "newest": "created_at",
        "created": "created_at",
        "name": "title",
        "title": "title",
    }.get(str(order_by or "trending").casefold())
    if not field:
        return rows

    def value(row):
        item = row.get(field)
        if field == "title":
            return str(item or "").casefold()
        return item if isinstance(item, (int, float)) else 0

    return sorted(rows, key=lambda row: (value(row), int(row.get("id", 0))),
                  reverse=field != "title")


def _fetch_supported_aggregate(fetch, requested: int) -> list[dict]:
    """Fill a leaderboard page after removing unsupported Tone rows."""
    visible: list[dict] = []
    offset = 0
    seen_ids: set[int] = set()
    while len(visible) < requested:
        batch = [_canonical_tone(row) for row in (fetch(offset) or [])
                 if isinstance(row, dict)]
        if not batch:
            break
        batch_ids = set()
        for row in batch:
            try:
                batch_ids.add(int(row["id"]))
            except (KeyError, TypeError, ValueError):
                continue
        if offset and batch_ids and batch_ids <= seen_ids:
            break
        seen_ids.update(batch_ids)
        visible.extend(row for row in batch if _has_supported_tone_models(row))
        if len(batch) < requested:
            break
        offset += len(batch)
    return visible[:requested]


def search(query="", page_size=50, order_by="trending", gear_filters=None,
           usernames=None, tag_names=None, make_names=None, page_number=1):
    """Search the official paginated tone catalog.

    TONE3000's documented default excludes A2 and its architecture filter only
    accepts one architecture at a time. Search the supported A2 and IR views,
    then merge and deduplicate the bounded prefixes. A1-only, Custom-only,
    and other unsupported tones are omitted; mixed packs remain visible with
    only their usable model counts.
    """
    return search_page(query, page_size=page_size, order_by=order_by,
                       gear_filters=gear_filters, usernames=usernames,
                       tag_names=tag_names, make_names=make_names,
                       page_number=page_number).rows


def search_page(query="", page_size=50, order_by="trending", gear_filters=None,
                usernames=None, tag_names=None, make_names=None, page_number=1):
    """Return a page while incrementally advancing each remote source."""
    requested = max(0, int(page_size))
    if requested == 0:
        return SearchPage([], 0, False, True)
    logical_page = max(1, int(page_number))
    key = _search_identity(query, order_by, gear_filters, usernames,
                           tag_names, make_names)
    if logical_page == 1 or key not in _SEARCH_STATES:
        _SEARCH_STATES[key] = _SearchState([
            _SearchSourceState(architecture, gears)
            for architecture, gears in _search_sources(gear_filters)
        ])
    state = _SEARCH_STATES[key]
    target = logical_page * requested
    while len(_merge_search_sources([source.rows for source in state.sources])) < target:
        before = sum(len(source.rows) for source in state.sources)
        for source in state.sources:
            _fetch_next_source_page(source, query=query, order_by=order_by,
                                    usernames=usernames, tag_names=tag_names,
                                    make_names=make_names)
        after = sum(len(source.rows) for source in state.sources)
        if after == before or all(source.exhausted for source in state.sources):
            break
    merged = _sort_search_rows(
        _merge_search_sources([source.rows for source in state.sources]), order_by)
    exhausted = all(source.exhausted for source in state.sources)
    start = (logical_page - 1) * requested
    rows = merged[start:start + requested]
    for row in rows:
        row["total_count"] = len(merged)
    return SearchPage(rows, len(merged), not exhausted, exhausted)


def top(limit=50):
    """Return the public all-time downloads aggregate ordering."""
    requested = max(0, int(limit))
    if requested == 0:
        return []
    select = ("id,title,description,gear,downloads_count,favorites_count,"
              "a1_models_count,a2_models_count,custom_models_count,"
              "irs_count,models_count,created_at,user_id,platform")

    def fetch(offset):
        params = dict(select=select, order="downloads_count.desc",
                      limit=requested)
        if offset:
            params["offset"] = offset
        return _get(f"{LEGACY_API}/tones_counts", **params)

    rows = _fetch_supported_aggregate(fetch, requested)
    _attach_usernames(rows, api=LEGACY_API)
    return rows


def top_favorites(limit=50, text=None, usernames=None):
    """收藏排行：search_tones_a2 RPC 无收藏排序（400），走 tones_counts 聚合表。

    行形状与 search 结果兼容。REQ-023：行缺 username/avatar_url（此前表格
    显示 @?）——按 user_id 批量联查 users 补上（一次 in 过滤请求）。

    text: 关键词，title/description 走 PostgREST ``or=(...ilike...)`` 过滤。
    usernames: 作者名列表（精确），先按 username 联查 users 表拿 user_id，
         再用 ``user_id=in.(...)`` 过滤；作者不存在时直接返回空列表。
    tones_counts 无 tag/make 字段，favorites 视图不支持 tag:/make: 过滤
    （与 search RPC 不同），调用方需自行忽略这两个维度。
    """
    params = dict(
        select="id,title,description,gear,downloads_count,favorites_count,"
               "a1_models_count,a2_models_count,custom_models_count,"
               "irs_count,models_count,created_at,user_id",
        order="favorites_count.desc", limit=limit)
    if usernames:
        user_response = _get(
            f"{LEGACY_API}/users", username=f"in.({','.join(usernames)})",
            select="id", limit=300)
        user_ids = [u["id"] for u in _response_rows(user_response)]
        if not user_ids:
            return []  # 作者不存在，无需再查排行
        params["user_id"] = f"in.({','.join(user_ids)})"
    if text:
        # ilike 通配符（*、%）与转义符（\）剔除，or= 表达式结构字符
        # （( ) ,）一并替换为空格，用户输入按字面匹配、不破坏过滤表达式
        safe = re.sub(r"[*%\\(),]", " ", text).strip()
        if safe:
            params["or"] = f"(title.ilike.*{safe}*,description.ilike.*{safe}*)"
    def fetch(offset):
        page_params = dict(params)
        if offset:
            page_params["offset"] = offset
        return _get(f"{LEGACY_API}/tones_counts", **page_params)

    rows = _fetch_supported_aggregate(fetch, max(0, int(limit)))
    _attach_usernames(rows, api=LEGACY_API)
    return rows


def top_creators(sort_by="tones", page_size=100, page_number=1):
    """Return the official paginated public creator leaderboard."""
    requested = max(0, int(page_size))
    if requested == 0:
        return []
    remote_page_size = 10
    logical_page = max(1, int(page_number))
    start_offset = (logical_page - 1) * requested
    first_page = start_offset // remote_page_size + 1
    skip = start_offset % remote_page_size
    pages = (skip + requested + remote_page_size - 1) // remote_page_size
    rows: list[dict] = []
    for offset in range(pages):
        response = _get(
            f"{API}/users", sort=sort_by, page=first_page + offset,
            page_size=remote_page_size)
        page_rows = _response_rows(response)
        rows.extend(page_rows)
        if not page_rows:
            break
        if isinstance(response, dict):
            try:
                if first_page + offset >= int(response.get("total_pages")):
                    break
            except (TypeError, ValueError):
                pass
        if len(page_rows) < remote_page_size:
            break
    return [_canonical_creator(row) for row in rows[skip:skip + requested]
            if isinstance(row, dict)]


def _attach_usernames(rows: list[dict], *, api: str = API) -> None:
    """按 user_id 批量联查 users，就地补 username/avatar_url（REQ-023）。"""
    user_ids = sorted({r.get("user_id") for r in rows if r.get("user_id")})
    if not user_ids:
        return
    users = _response_rows(_get(
        f"{api}/users", id=f"in.({','.join(user_ids)})",
        select="id,username,avatar_url", limit=len(user_ids)))
    by_id = {u["id"]: u for u in users}
    for row in rows:
        u = by_id.get(row.get("user_id"))
        if u:
            row["username"] = u.get("username")
            row["avatar_url"] = u.get("avatar_url")


def user(username: str) -> dict | None:
    """Look up one public user through the documented username search."""
    response = _get(f"{API}/users", query=username, page=1, page_size=10)
    rows = _response_rows(response)
    wanted = str(username).casefold()
    return next((row for row in rows
                 if str(row.get("username") or "").casefold() == wanted), None)


def user_stats(username: str) -> dict | None:
    """Return public user data and the counts supplied by the official API."""
    info = user(username)
    if not info:
        return None
    stats = {
        "tones": info.get("tones_count", info.get("public_tones_count")),
        "downloads": info.get("downloads_count") or 0,
        "favorites": info.get("favorites_count") or 0,
        "models": info.get("models_count", info.get("public_models_count")) or 0,
    }
    return {**info, "stats": stats}


def _model_has_explicit_architecture(model: dict) -> bool:
    """Whether a model row carries an architecture token of its own."""
    if not isinstance(model, dict):
        return False
    return any(str(model.get(key) or "").strip()
               for key in ("architecture_version", "architecture"))


def _model_needs_tone_context(model: dict) -> bool:
    """Whether a model row needs its parent Tone to classify an architectureless IR."""
    if not isinstance(model, dict):
        return False
    if _model_has_explicit_architecture(model) or str(
            model.get("format") or "").strip():
        return False
    return not bool(_model_file_suffix(model))


def models(tone_id, a2_only=True, tone=None):
    """Return A2 models, or A2 plus IR when ``a2_only`` is false.

    name 取 models 表顶层字段（TONE3000 网页/zip 下载的文件名即此 name 原样）。
    用 JSONB 投影只取 architecture，不拉全量 model_json（多模型 tone 的全量
    响应可达 19MB+、耗时 60s+，见 scripts/import_handoff.py 踩坑记录）。
    """
    remote_page_size = 300

    def fetch(architecture: str | None) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            params = {"tone_id": tone_id, "page": page,
                      "page_size": remote_page_size}
            if architecture is not None:
                params["architecture"] = architecture
            response = _get(f"{API}/models", **params)
            page_rows = _response_rows(response)
            rows.extend(page_rows)
            if not page_rows:
                break
            total_pages = None
            if isinstance(response, dict):
                try:
                    total_pages = int(response.get("total_pages"))
                    if page >= total_pages:
                        break
                except (TypeError, ValueError):
                    pass
            if total_pages is None and len(page_rows) < remote_page_size:
                break
            page += 1
        return rows

    # The documented default is A1 + Custom and explicitly excludes A2.
    # Add the A2 page when callers request the complete *supported* set. The
    # final classifier below removes A1, Custom, and all other architectures.
    architectures = ["2"] if a2_only else [None, "2"]
    tagged_rows: list[tuple[dict, str | None]] = []
    seen: dict[int, int] = {}
    for architecture in architectures:
        for model in fetch(architecture):
            if not isinstance(model, dict):
                continue
            model_id = model.get("id") if isinstance(model, dict) else None
            if model_id is not None:
                try:
                    key = int(model_id)
                except (TypeError, ValueError):
                    key = hash(str(model_id))
                if key in seen:
                    # The unfiltered endpoint can repeat a row returned by
                    # the A2 view. Keep the A2 view's copy because that source
                    # is the authoritative classification for an otherwise
                    # metadata-less .nam row.
                    if architecture == "2":
                        tagged_rows[seen[key]] = (model, architecture)
                    continue
                seen[key] = len(tagged_rows)
            tagged_rows.append((model, architecture))

    classification_tone = tone if isinstance(tone, dict) else None
    if (classification_tone is None
            and any(source is None and _model_needs_tone_context(model)
                    for model, source in tagged_rows)):
        parent = _response_object(_get(f"{API}/tones/{int(tone_id)}"))
        classification_tone = (
            _canonical_tone(dict(parent)) if isinstance(parent, dict) else {})

    rows: list[dict] = []
    for model, source in tagged_rows:
        candidate = dict(model)
        # The A2 endpoint is authoritative for an otherwise metadata-less row.
        # Preserve it as canonical metadata so downstream Pack/download filters
        # do not discard a valid A2 file after this method returns.
        if source == "2" and not _model_has_explicit_architecture(candidate):
            # The A2 endpoint is authoritative even when the row has a .nam
            # name or an explicit ``format=nam`` field. Do not require the
            # endpoint to repeat the architecture on every model row.
            if str(candidate.get("format") or "").strip().casefold() != "ir":
                candidate["architecture_version"] = "2"
        elif (source is None and classification_tone
              and not any(str(candidate.get(key) or "").strip()
                          for key in ("architecture_version", "architecture", "format"))
              and _is_ir_model(candidate, classification_tone)):
            # A legacy IR endpoint may omit both architecture and a file
            # suffix. Preserve the parent-tone classification for callers such
            # as download(), which may not carry the parent tone separately.
            candidate["architecture"] = "IR"
        if is_supported_model(candidate, classification_tone):
            rows.append(candidate)
    return rows


def _safe_download_name(value, fallback):
    """Keep the semantic basename while preventing remote path traversal."""
    text = str(value or "").replace("\\", "/")
    name = PurePosixPath(text).name
    if name in {"", ".", ".."} or "\x00" in name:
        return fallback
    return name


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(tone_id, dest_dir, tag=None, a2_only=True, ext=None,
             return_paths=False, progress=None, quiet=False, model_ids=None,
             existing_records=None, tone=None):
    """Download A2, or A2 plus IR when ``a2_only`` is false.

    文件名优先采用 models.name；旧响应没有 name 时采用 model_url 的 basename。
    不添加序号、不改写语义名称。model_ids 限制只下载指定模型（部分安装）。
    progress 回调接收 (completed, total, filename)。
    return_paths=True 时返回供 library 持久化的记录。
    quiet=True 供 TUI 等已有状态栏的调用方关闭 stdout 输出。
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    ms = (models(tone_id, a2_only=a2_only)
          if tone is None else models(tone_id, a2_only=a2_only, tone=tone))
    ms = [m for m in ms
          if is_supported_model(m, tone)
          and (not a2_only or _is_a2_model(m))]
    if model_ids:
        ms = [m for m in ms if m["id"] in set(model_ids)]
    if not ms:
        if not quiet:
            print(f"[{tone_id}] 无匹配模型")
        return [] if return_paths else 0
    got = 0
    records = []
    existing_by_id = {
        int(record["id"]): record for record in (existing_records or [])
        if isinstance(record, dict) and record.get("id") is not None
    }
    for i, m in enumerate(sorted(ms, key=lambda x: x["id"]), 1):
        # 文件名优先采用 models.name 原样（TONE3000 网页 zip 命名规则，保留
        # 空格和语义参数）；旧 API 响应没有 name 时回退到 URL basename，避免
        # 把用户看到的原始文件名改成无意义的 model-<id>。
        fname = m.get("name")
        if not fname:
            url_path = urllib.parse.urlparse(m.get("model_url") or "").path
            fname = Path(urllib.parse.unquote(url_path)).name
        fname = _safe_download_name(fname, f"model-{m['id']}")
        # Choose the extension per model. A mixed Pack can contain A2 and IR,
        # so a tone-level is_ir flag must not turn an A2 file into a WAV.
        lower_name = fname.casefold()
        if _is_ir_model(m, tone):
            if not lower_name.endswith(tuple(_IR_SUFFIXES)):
                suffix = f".{str(ext or 'wav').lstrip('.')}".lower()
                fname = f"{fname}{suffix}"
        elif not lower_name.endswith(tuple(_SUPPORTED_NAM_SUFFIXES)):
            fname = f"{fname}.nam"
        out = dest / fname
        if progress:
            progress(i - 1, len(ms), fname)

        existing = existing_by_id.get(int(m["id"]))
        reuse = False
        if existing and out.is_file():
            try:
                expected_size = int(existing.get("local_size"))
            except (TypeError, ValueError):
                expected_size = 0
            reuse = (
                expected_size > 0
                and out.stat().st_size == expected_size
                and existing.get("model_url") == m.get("model_url")
                and existing.get("name") == m.get("name")
                and Path(existing.get("local_path") or "").name == fname
                and existing.get("local_sha256") == _sha256_file(out)
            )
        if reuse:
            got += 1
        else:
            try:
                bearer = access_token()
            except (AuthenticationRequiredError, Tone3000HTTPError,
                    urllib.error.URLError, json.JSONDecodeError):
                # ``models()`` already authenticates official API calls. Keep
                # the downloader usable for caller-supplied public URLs and
                # network-free adapters; an official model URL will still
                # surface its 401 below and require login.
                bearer = None
            network_attempts = 0
            refreshed = False
            while True:
                try:
                    headers = {"Accept": "application/octet-stream",
                               "User-Agent": BROWSER_UA}
                    if bearer:
                        headers["Authorization"] = f"Bearer {bearer}"
                    req = urllib.request.Request(m["model_url"], headers=headers)
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = r.read()
                    if not data:
                        raise ValueError("downloaded file is empty")
                    fd, temp_name = tempfile.mkstemp(
                        prefix=f".{out.name}.", suffix=".part", dir=dest)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(data)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temp_name, out)
                    except Exception:
                        try:
                            os.unlink(temp_name)
                        except FileNotFoundError:
                            pass
                        raise
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 401 and not refreshed:
                        bearer = access_token(force_refresh=True)
                        refreshed = True
                        continue
                    if network_attempts >= 2:
                        raise
                    network_attempts += 1
                    print(f"[{tone_id}] {fname} 下载中断({e})，重试 {network_attempts + 1}/3", flush=True)
                    time.sleep(1)
                except Exception as e:
                    if network_attempts >= 2:
                        raise
                    network_attempts += 1
                    print(f"[{tone_id}] {fname} 下载中断({e})，重试 {network_attempts + 1}/3", flush=True)
                    time.sleep(1)
            got += 1
            time.sleep(0.1)
        size = out.stat().st_size
        digest = _sha256_file(out)
        if progress:
            progress(i, len(ms), fname)
        if return_paths:
            records.append({"id": m["id"], "tone_id": tone_id, "model_url": m["model_url"],
                            "name": m.get("name"),
                            "model_json": {
                                key: value for key, value in (
                                    ("architecture", m.get("architecture")),
                                    ("architecture_version", m.get("architecture_version")),
                                ) if value not in (None, "")
                            } or None,
                            "local_path": str(out),
                            "local_size": size,
                            "local_sha256": digest})
    if not quiet:
        print(f"[{tone_id}] 下载 {got}/{len(ms)} -> {dest}")
    return records if return_paths else got


def tone_by_id(tone_id, with_models=False):
    """Fetch one tone through the documented ``/tones/{id}`` resource."""
    response = _get(f"{API}/tones/{int(tone_id)}")
    tone = _response_object(response)
    if tone is None:
        return None
    t = _canonical_tone(dict(tone))
    if t.get("is_deleted"):
        return None
    t.setdefault("tags", [])
    t.setdefault("makes", [])
    # ``model_name`` is a legacy library field. Official Tone responses may
    # carry a name from an unsupported A1/Custom row, so derive it only from
    # the shared A2/IR model list instead of trusting the raw field.
    t["model_name"] = None
    for model in models(tone_id, a2_only=False, tone=t):
        if (model.get("name")
                and is_supported_model(model, t)):
            t["model_name"] = model["name"]
            break
    return _canonical_tone(t)


def tones_for_model_ids(model_ids):
    """Resolve exact TONE3000 model IDs to their parent tone search rows.

    ``search_tones_a2`` has no model-id predicate, so this takes the model
    lookup path instead of pretending a numeric ID is a title keyword. Each
    returned tone carries ``matched_model_ids`` for the TUI result label.
    """
    ids = list(dict.fromkeys(int(model_id) for model_id in model_ids))
    if not ids:
        return []
    models_by_id = {}
    for model_id in ids:
        row = _response_object(_get(f"{API}/models/{model_id}"))
        if row and row.get("id") is not None and row.get("tone_id") is not None:
            models_by_id[int(row["id"])] = row
    matches: dict[int, list[int]] = {}
    for model_id in ids:
        row = models_by_id.get(model_id)
        if row:
            matches.setdefault(int(row["tone_id"]), []).append(row)
    tones = []
    for tone_id, matched_models in matches.items():
        tone = tone_by_id(tone_id)
        if not tone:
            continue
        matched_ids = [model["id"] for model in matched_models
                       if is_supported_model(model, tone)]
        if matched_ids:
            tone["matched_model_ids"] = matched_ids
            tones.append(tone)
    return tones


def fmt(t):
    return (f"{t['id']:>7} | dl={t.get('downloads_count', 0):>6} fav={t.get('favorites_count', 0):>5} "
            f"a2={t.get('a2_models_count', 0):>3} ir={t.get('irs_count', 0):>3} | "
            f"{t.get('gear', '?'):<8} | {t.get('title', '')[:58]} | @{t.get('username', '')}")


# ---- 试听干音素材（TONE3000 网页播放器内置，托管于其 MIT 开源仓库） ----
# 来源: github.com/tone-3000/neural-amp-modeler-wasm (MIT, © Steven Atkinson)
DRY_INPUTS_BASE = ("https://raw.githubusercontent.com/tone-3000/"
                   "neural-amp-modeler-wasm/refs/heads/main/ui/public/inputs")
DRY_INPUTS = {
    # 吉他（26）
    "brit": "Brit - Guitar.wav", "celestial": "Celestial - Guitar.wav",
    "cream": "Cream - Guitar.wav", "decapitated": "Decapitated - Guitar.wav",
    "fast-thrash": "Fast Thrash - Guitar.wav", "fear": "Fear - Guitar.wav",
    "groove-thrash": "Groove Thrash - Guitar.wav", "hammer-lead": "Hammer Lead - Guitar.wav",
    "harmonics": "Harmonics - Guitar.wav", "honky": "Honky - Guitar.wav",
    "hotrod": "Hotrod - Guitar.wav", "jazz-hop": "Jazz Hop - Guitar.wav",
    "jazz-trot": "Jazz Trot - Guitar.wav", "john": "John - Guitar.wav",
    "lunar": "Lunar - Guitar.wav", "mayer": "Mayer - Guitar.wav",
    "metalcore": "Metalcore - Guitar.wav", "pluck": "Pluck - Guitar.wav",
    "pop-punk": "Pop Punk - Guitar.wav", "power": "Power - Guitar.wav",
    "power-thrash": "Power Thrash - Guitar.wav", "progression": "Progression -  Guitar.wav",
    "raid": "Raid - Guitar.wav", "rotary": "Rotary - Guitar.wav",
    "slide-lead": "Slide Lead - Guitar.wav", "smooth": "Smooth - Guitar.wav",
    "stroke": "Stroke - Guitar.wav", "tomb": "Tomb - Guitar.wav",
    # 贝斯（7）
    "downtown": "Downtown - Bass.wav", "drivin": "Drivin' - Bass.wav",
    "frogger": "Frogger - Bass.wav", "garden": "Garden - Bass.wav",
    "rollin": "Rollin' - Bass.wav", "smokin": "Smokin' - Bass.wav",
}

# The small set used by the input picker and a fast bootstrap. Keep this
# catalog-level choice next to the source map so UI and installer agree.
DRY_INPUT_STARTER_KEYS = (
    "mayer", "brit", "cream", "john", "pop-punk", "metalcore",
    "smooth", "fast-thrash", "hotrod", "slide-lead",
)


def fetch_dry_inputs(dest_dir, names=None, progress=None):
    """下载 TONE3000 试听干音（MIT）到 dest_dir。names 为 DRY_INPUTS 的 key 列表，缺省全下。
    progress(done, total, fname) 可选回调（每文件一次，done=已下载数）"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    keys = names or list(DRY_INPUTS)
    total = len(keys)
    done = 0
    got = 0
    for k in keys:
        fname = DRY_INPUTS[k]
        out = dest / fname
        if out.exists() and out.stat().st_size > 0:
            done += 1
            got += 1
            continue
        url = f"{DRY_INPUTS_BASE}/{urllib.parse.quote(fname)}"
        with urllib.request.urlopen(url, timeout=60) as r:
            out.write_bytes(r.read())
        done += 1
        got += 1
        if progress:
            progress(done, total, fname)
        time.sleep(0.1)
    if progress:
        progress(done, total, None)
    print(f"干音素材 {got}/{total} -> {dest}")
    return got


def fetch_dry_inputs_missing(dest_dir, names=None):
    """返回 dest_dir 中缺失（不存在或为空）的干声素材 key 列表。
    names 为 DRY_INPUTS 的 key 列表，缺省全部"""
    dest = Path(dest_dir)
    keys = names or list(DRY_INPUTS)
    missing = []
    for k in keys:
        out = dest / DRY_INPUTS[k]
        if not out.exists() or out.stat().st_size == 0:
            missing.append(k)
    return missing


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd = args[0]
    if cmd == "search":
        for t in search(args[1] if len(args) > 1 else ""):
            print(fmt(t))
    elif cmd == "top":
        for t in top(int(args[1]) if len(args) > 1 else 50):
            print(fmt(t))
    elif cmd == "models":
        for m in models(args[1]):
            print(f"model {m['id']}: {m['model_url'][:90]}")
    elif cmd == "download":
        download(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "dry":
        fetch_dry_inputs(args[1], args[2:] or None)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
