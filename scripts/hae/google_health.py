#!/usr/bin/env python3
"""
google_health.py — minimal Google Health API (v4) client for the Fitbit Air sync.

stdlib only and Python 3.9-safe (runs under the hermes venv 3.11, VPS /usr/bin/python3
3.12, and the Mac test runner 3.9). Deployed FLAT into ~/.hermes/scripts/ like the other
hae_* modules (a sub-package would not import there).

Credentials live in GOOGLE_HEALTH_DIR (default ~/.hermes/google_health/, never in the
vault or the repo, never wiped by deploy.sh):
  client.json   {"client_id", "client_secret"}            (0600)
  token.json    {"refresh_token", "access_token", "expires_at", "scope"} (0600)

Rules learned the hard way (research + review, 2026-10-08):
  * This client must NEVER fall back to ~/.hermes/google_token.json — that token carries
    Gmail scopes, and the Google Health API rejects any token with Gmail scopes.
  * Never re-run the consent flow from code: each consent mints a new refresh token and
    past 100 per client the oldest (this machine's) is silently invalidated.
  * Tokens are never printed or logged.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GH_DIR = Path(os.environ.get("GOOGLE_HEALTH_DIR", str(Path.home() / ".hermes" / "google_health")))
API = "https://health.googleapis.com/v4/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TIMEOUT = 30           # per request
RETRIES = 2            # on 429 / 5xx only; backoff 2, 4 s
RUN_BUDGET_S = 90      # whole-run deadline (review 2026-10-08: a network hang could stall ~48 min)


class GHError(Exception):
    kind = "api"


class AuthExpired(GHError):
    """The refresh token is dead (invalid_grant): re-consent needed (his action)."""
    kind = "auth_expired"


class AuthScope(GHError):
    """The token lacks a scope, or carries a disallowed one (e.g. Gmail)."""
    kind = "auth_scope"


class AccountNotLinked(GHError):
    kind = "account_not_linked"


class NetworkDown(GHError):
    """Network failure or run deadline hit: stop the whole run (don't retry 15 groups)."""
    kind = "network"


class Missing(GHError):
    """client.json / token.json not present."""
    kind = "not_configured"


def _write_private(path: Path, data: dict) -> None:
    """Atomic write with mode 0600 through a UNIQUE temp file in the same dir (a fixed
    `.tmp` name let two concurrent writers truncate each other — review 2026-10-08)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Client:
    def __init__(self, gh_dir: Path = None, opener=None, budget_s: float = RUN_BUDGET_S):
        self.dir = Path(gh_dir or GH_DIR)
        self._open = opener or urllib.request.urlopen     # injectable for tests
        self.deadline = time.time() + budget_s
        try:
            self.client = json.loads((self.dir / "client.json").read_text())
            self.token = json.loads((self.dir / "token.json").read_text())
        except (OSError, ValueError) as e:
            raise Missing(f"Google Health credentials missing or unreadable in {self.dir} "
                          f"({type(e).__name__})") from None
        if not self.token.get("refresh_token"):
            raise Missing(f"no refresh_token in {self.dir / 'token.json'}")

    # ── auth ────────────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        """Under an exclusive lock; re-reads token.json first so a concurrent run that just
        refreshed is reused rather than refreshed twice (review M2)."""
        lock = os.open(str(self.dir / "token.lock"), os.O_RDONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                fresh = json.loads((self.dir / "token.json").read_text())
                if fresh.get("access_token") and int(fresh.get("expires_at", 0)) > time.time():
                    self.token = fresh
                    return
            except (OSError, ValueError):
                pass
            self._refresh_locked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def _refresh_locked(self) -> None:
        body = urllib.parse.urlencode({
            "client_id": self.client["client_id"],
            "client_secret": self.client.get("client_secret", ""),
            "refresh_token": self.token["refresh_token"],
            "grant_type": "refresh_token"}).encode()
        try:
            with self._open(urllib.request.Request(TOKEN_URL, data=body), timeout=TIMEOUT) as r:
                tok = json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if not isinstance(e, urllib.error.HTTPError):
                raise NetworkDown(f"token refresh: network error ({type(e).__name__})") from None
            txt = e.read().decode(errors="replace")[:300]
            if "invalid_grant" in txt:
                raise AuthExpired("refresh token rejected (invalid_grant) — the app may still be in "
                                  "'Testing' (7-day tokens) or access was revoked; re-run consent.py "
                                  "on the Mac and copy token.json to the VPS") from None
            raise GHError(f"token refresh failed: HTTP {e.code}") from None
        self.token["access_token"] = tok["access_token"]
        self.token["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600)) - 120
        if tok.get("refresh_token"):            # Google may rotate it
            self.token["refresh_token"] = tok["refresh_token"]
        _write_private(self.dir / "token.json", self.token)

    def _access(self) -> str:
        if not self.token.get("access_token") or int(self.token.get("expires_at", 0)) <= time.time():
            self._refresh()
        return self.token["access_token"]

    # ── requests ─────────────────────────────────────────────────────────────────
    def call(self, method: str, path: str, body: dict = None, params: dict = None) -> dict:
        url = API + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        refreshed = False
        for attempt in range(RETRIES + 1):
            if time.time() > self.deadline:
                raise NetworkDown(f"run deadline ({RUN_BUDGET_S}s) reached before {path}")
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self._access(), "Content-Type": "application/json"})
            try:
                with self._open(req, timeout=TIMEOUT) as r:
                    raw = r.read().decode()
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="replace")[:500]
                if e.code == 401 and not refreshed:
                    self.token["access_token"] = ""
                    refreshed = True
                    continue
                if e.code == 403 and ("SCOPE" in txt or "DISALLOWED_OAUTH" in txt or "scopes" in txt):
                    raise AuthScope(f"HTTP 403 on {path}: {txt[:200]}") from None
                if "ACCOUNT_NOT_LINKED" in txt:
                    raise AccountNotLinked("this Google account has no Google Health profile") from None
                if e.code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise GHError(f"HTTP {e.code} on {path}: {txt[:200]}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise NetworkDown(f"network error on {path}: {type(e).__name__}") from None
        raise GHError(f"gave up on {path}")

    def list_points(self, data_type: str, filt: str, page_size: int = None) -> list:
        """All dataPoints of a type matching an AIP-160 filter, following nextPageToken."""
        out, token = [], None
        for _ in range(50):                               # hard stop: 50 pages
            params = {"filter": filt}
            if page_size:
                params["pageSize"] = page_size
            if token:
                params["pageToken"] = token
            d = self.call("GET", f"/dataTypes/{data_type}/dataPoints", params=params)
            out += d.get("dataPoints", [])
            token = d.get("nextPageToken")
            if not token:
                return out
        raise GHError(f"{data_type}: more than 50 pages — refusing to continue")

    def daily_rollup(self, data_type: str, start, end_exclusive, family: str = "google-wearables") -> list:
        """dailyRollUp over [start, end) civil dates (datetime.date), one window per day."""
        days = (end_exclusive - start).days
        if days < 1 or days > 90:
            raise ValueError("dailyRollUp range must be 1..90 days")
        body = {"range": {"start": {"date": {"year": start.year, "month": start.month, "day": start.day}},
                          "end": {"date": {"year": end_exclusive.year, "month": end_exclusive.month,
                                           "day": end_exclusive.day}}},
                "windowSizeDays": 1, "pageSize": days,
                "dataSourceFamily": f"users/me/dataSourceFamilies/{family}"}
        return self.call("POST", f"/dataTypes/{data_type}/dataPoints:dailyRollUp", body=body
                         ).get("rollupDataPoints", [])

    def paired_devices(self) -> list:
        d = self.call("GET", "/pairedDevices")
        return d.get("pairedDevices", d.get("devices", []))
