"""Google auth: OAuth (desktop loopback, agent-friendly 2-step), service accounts, and ADC.

Resolution order for credentials:
  1. ASAM_CREDENTIALS env / `credentials_file` config  (service-account or authorized-user JSON)
  2. $ASAM_HOME/token.json                               (from `asam auth login`)
  3. $ASAM_HOME/service_account.json                     (from `asam auth use sa.json`)
  4. Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS / gcloud ADC)
"""

from __future__ import annotations

import base64
import glob
import hashlib
import http.server
import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests

from . import config
from .errors import AsamError, not_authenticated

SCOPES_CORE = [
    "https://www.googleapis.com/auth/webmasters",  # Search Console (read + write)
    "https://www.googleapis.com/auth/analytics.readonly",  # GA4 Data API
    "https://www.googleapis.com/auth/analytics.edit",  # GA4 Admin API (properties, streams, key events, dims)
    "https://www.googleapis.com/auth/analytics.manage.users",  # GA4 access bindings (grant a service account)
    "https://www.googleapis.com/auth/siteverification",  # verify site ownership programmatically
    "https://www.googleapis.com/auth/indexing",  # Google Indexing API
]
SCOPES_IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
SCOPE_CLOUD = "https://www.googleapis.com/auth/cloud-platform"  # opt-in: lets asam enable APIs itself

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

_cached: dict[str, Any] = {}


# ---------------------------------------------------------------- file locations
def token_path() -> Path:
    return config.home() / "token.json"


def sa_path() -> Path:
    return config.home() / "service_account.json"


def pending_path() -> Path:
    return config.home() / "pending_login.json"


def client_store_path() -> Path:
    return config.home() / "client_secret.json"


# ---------------------------------------------------------------- oauth client discovery
def find_oauth_client(explicit: str | None = None) -> Path | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("ASAM_OAUTH_CLIENT"):
        candidates.append(os.environ["ASAM_OAUTH_CLIENT"])
    candidates.append(str(client_store_path()))
    for pattern in ("client_secret*.json", "~/Downloads/client_secret*.json", "~/client_secret*.json"):
        candidates.extend(sorted(glob.glob(os.path.expanduser(pattern)), key=os.path.getmtime, reverse=True))
    for c in candidates:
        p = Path(c).expanduser()
        if p.is_file():
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if "installed" in data or "web" in data:
                return p
    return None


def _load_client(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    block = data.get("installed") or data.get("web")
    if not block:
        raise AsamError("BAD_CLIENT_FILE", f"{path} is not an OAuth client JSON.")
    if "web" in data and "installed" not in data:
        raise AsamError(
            "WRONG_CLIENT_TYPE",
            "OAuth client is of type 'Web application'; a 'Desktop app' client is required.",
            fix="Google Cloud Console > APIs & Services > Credentials > Create credentials > OAuth client ID > "
            "Application type: Desktop app. Download the JSON and run `asam auth login --client <file>`.",
        )
    return {"client_id": block["client_id"], "client_secret": block.get("client_secret", "")}


# ---------------------------------------------------------------- login (OAuth)
def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _scopes(cloud: bool) -> list[str]:
    s = SCOPES_CORE + SCOPES_IDENTITY
    return s + [SCOPE_CLOUD] if cloud else s


def start_login(client: str | None = None, port: int = 8765, cloud: bool = False) -> dict[str, Any]:
    """Step 1 of the agent-friendly login. Returns the URL a human must open once."""
    path = find_oauth_client(client)
    if not path:
        raise AsamError(
            "NO_OAUTH_CLIENT",
            "No OAuth 'Desktop app' client JSON found (looked in ASAM_OAUTH_CLIENT, ~/.config/asam, ./, ~/Downloads).",
            fix="Create one (3 min): see `asam guide auth`. Then re-run `asam auth login --client <client_secret.json>`. "
            "Or skip OAuth and use a service account: `asam auth use <sa.json>`.",
        )
    creds = _load_client(path)
    # keep a copy so later refreshes / re-logins never need the Downloads file again
    if path.resolve() != client_store_path().resolve():
        config.write_secret(client_store_path(), path.read_text())
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{port}/"
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_scopes(cloud)),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTH_URI + "?" + urllib.parse.urlencode(params)
    config.write_secret(
        pending_path(),
        json.dumps({"state": state, "verifier": verifier, "redirect_uri": redirect_uri, "created": time.time(), **creds}),
    )
    return {
        "auth_url": url,
        "redirect_uri": redirect_uri,
        "instructions": (
            "Open auth_url in a browser signed into the Google account that owns your Search Console / GA4, approve. "
            "The browser then goes to http://127.0.0.1:... - if that page fails to load (remote machine), that's fine: "
            "copy the FULL URL from the address bar and give it to `asam auth finish <url>` (MCP: auth_finish)."
        ),
    }


def finish_login(code_or_url: str) -> dict[str, Any]:
    """Step 2: exchange the redirect URL (or bare code) for a refresh token and store it."""
    if not pending_path().exists():
        raise AsamError("NO_PENDING_LOGIN", "No login in progress.", fix="Run `asam auth login` first.")
    pending = json.loads(pending_path().read_text())
    code = code_or_url.strip()
    if code.startswith("http") or "code=" in code:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(code).query or code.split("?", 1)[-1])
        if q.get("error"):
            raise AsamError("OAUTH_DENIED", f"Google returned error: {q['error'][0]}", fix="Re-run `asam auth login`.")
        if q.get("state", [pending["state"]])[0] != pending["state"]:
            raise AsamError("OAUTH_STATE_MISMATCH", "State mismatch (stale URL?).", fix="Re-run `asam auth login`.")
        code = q.get("code", [""])[0]
    if not code:
        raise AsamError("OAUTH_NO_CODE", "No authorization code found in input.", fix="Paste the full redirect URL.")
    r = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": pending["client_id"],
            "client_secret": pending["client_secret"],
            "redirect_uri": pending["redirect_uri"],
            "grant_type": "authorization_code",
            "code_verifier": pending["verifier"],
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise AsamError(
            "OAUTH_EXCHANGE_FAILED",
            f"Token exchange failed: {r.text[:300]}",
            fix="Codes are single-use and expire in minutes. Re-run `asam auth login` and finish promptly.",
        )
    tok = r.json()
    if "refresh_token" not in tok:
        raise AsamError(
            "NO_REFRESH_TOKEN",
            "Google did not return a refresh token.",
            fix="Remove the app at https://myaccount.google.com/permissions and log in again.",
        )
    info = {
        "type": "authorized_user",
        "client_id": pending["client_id"],
        "client_secret": pending["client_secret"],
        "refresh_token": tok["refresh_token"],
        "token_uri": TOKEN_URI,
        "scopes": tok.get("scope", "").split(),
    }
    config.write_secret(token_path(), json.dumps(info, indent=2))
    pending_path().unlink(missing_ok=True)
    _cached.clear()
    return {"ok": True, "stored": str(token_path()), "scopes": info["scopes"], "identity": identity()}


def login_interactive(client: str | None = None, port: int = 8765, cloud: bool = False, timeout: int = 300,
                      open_browser: bool = True) -> dict[str, Any]:
    """One-shot login for a human at a terminal: opens browser, catches the loopback redirect."""
    start = start_login(client, port=port, cloud=cloud)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            result["url"] = "http://127.0.0.1" + self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>asam: login received. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return {**start, "note": f"Port {port} busy; finish manually with `asam auth finish <url>`."}
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    print("\nOpen this URL to authorize asam:\n\n  " + start["auth_url"] + "\n", flush=True)
    print("If this machine is remote, approve in any browser, then run:\n  asam auth finish '<full redirect URL>'\n",
          flush=True)
    if open_browser:
        try:
            webbrowser.open(start["auth_url"])
        except Exception:
            pass
    t.join(timeout)
    srv.server_close()
    if "url" not in result:
        return {**start, "note": "Timed out waiting for the browser; finish with `asam auth finish <url>`."}
    return finish_login(result["url"])


# ---------------------------------------------------------------- other credential sources
def use_file(path: str) -> dict[str, Any]:
    """Register a credential file (service account, authorized_user, or OAuth client)."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise AsamError("FILE_NOT_FOUND", f"{p} does not exist.")
    data = json.loads(p.read_text())
    if data.get("type") == "service_account":
        config.write_secret(sa_path(), p.read_text())
        _cached.clear()
        return {
            "stored": str(sa_path()),
            "type": "service_account",
            "email": data.get("client_email"),
            "next": (
                f"Grant {data.get('client_email')} access: (a) GA4 Admin > Property access management > add as "
                "Editor (or `asam ga4 grant-access` from an OAuth login); (b) Search Console: either add it as Owner "
                "in Settings > Users and permissions, or let asam verify the site as this account "
                "(`asam setup site <domain>` then `asam setup verify`), which makes it an owner with no UI."
            ),
        }
    if data.get("type") == "authorized_user":
        config.write_secret(token_path(), p.read_text())
        _cached.clear()
        return {"stored": str(token_path()), "type": "authorized_user"}
    if "installed" in data or "web" in data:
        _load_client(p)
        config.write_secret(client_store_path(), p.read_text())
        return {"stored": str(client_store_path()), "type": "oauth_client", "next": "Run `asam auth login`."}
    raise AsamError("UNKNOWN_CREDENTIAL", "Unrecognised credential JSON.")


def logout() -> dict[str, Any]:
    removed = []
    for p in (token_path(), sa_path(), pending_path()):
        if p.exists():
            p.unlink()
            removed.append(str(p))
    _cached.clear()
    return {"removed": removed}


def _all_scopes() -> list[str]:
    return SCOPES_CORE


def _source() -> tuple[str, Any]:
    """Return (kind, credentials) without refreshing."""
    from google.oauth2 import credentials as user_creds
    from google.oauth2 import service_account

    explicit = config.get("credentials") or config.get("credentials_file")
    files: list[Path] = []
    if explicit:
        files.append(Path(explicit).expanduser())
    files += [token_path(), sa_path()]
    for f in files:
        if not f.is_file():
            continue
        data = json.loads(f.read_text())
        if data.get("type") == "service_account":
            c = service_account.Credentials.from_service_account_info(data, scopes=_all_scopes())
            subj = config.get("impersonate")
            if subj:
                c = c.with_subject(subj)
            return f"service_account:{f}", c
        if data.get("type") == "authorized_user":
            return f"oauth_user:{f}", user_creds.Credentials.from_authorized_user_info(data)
    try:
        import google.auth

        c, project = google.auth.default(scopes=_all_scopes())
        return f"adc:{project or 'no-project'}", c
    except Exception:
        pass
    raise not_authenticated()


def credentials():
    if "creds" in _cached:
        return _cached["creds"]
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request

    kind, c = _source()
    try:
        if not c.valid:
            c.refresh(Request())
    except RefreshError as e:
        msg = str(e)
        fix = "Run `asam auth login` again."
        if kind.startswith("service_account"):
            fix = ("The service-account key was rejected (account/key deleted or disabled, or clock skew). Create a new "
                   "key: `gcloud iam service-accounts keys create sa.json --iam-account <email>` then "
                   "`asam auth use sa.json`.")
        elif kind.startswith("adc"):
            fix = "Application Default Credentials are stale: re-run `gcloud auth application-default login ...`."
        elif "invalid_grant" in msg:
            fix = (
                "Refresh token expired or revoked. If your OAuth consent screen is in 'Testing' mode, Google expires "
                "tokens after 7 days: set it to 'In production' (Google Auth Platform > Audience > Publish app; no "
                "verification needed for personal use), then `asam auth login`."
            )
        raise AsamError("AUTH_REFRESH_FAILED", f"Could not refresh {kind} credentials: {msg[:200]}", fix=fix) from e
    _cached["creds"] = c
    _cached["kind"] = kind
    return c


def session():
    from google.auth.transport.requests import AuthorizedSession

    if "session" not in _cached:
        _cached["session"] = AuthorizedSession(credentials())
    return _cached["session"]


def identity() -> dict[str, Any]:
    c = credentials()
    kind = _cached.get("kind", "")
    out: dict[str, Any] = {"source": kind}
    if hasattr(c, "service_account_email"):
        out["email"] = c.service_account_email
        out["type"] = "service_account"
        return out
    out["type"] = "user"
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": c.token}, timeout=15)
        if r.ok:
            j = r.json()
            out["email"] = j.get("email")
            out["scopes"] = j.get("scope", "").split()
    except requests.RequestException:
        pass
    return out


def status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "home": str(config.home()),
        "token_json": token_path().exists(),
        "service_account_json": sa_path().exists(),
        "oauth_client": str(find_oauth_client() or "") or None,
        "gcloud_installed": bool(shutil.which("gcloud")),
    }
    try:
        out["identity"] = identity()
        out["authenticated"] = True
        granted = set(out["identity"].get("scopes") or [])
        if granted:
            out["missing_scopes"] = [s for s in SCOPES_CORE if s not in granted]
    except AsamError as e:
        out["authenticated"] = False
        out["error"] = e.to_dict()
    return out
