#!/usr/bin/env python3
"""
hae_ingest.py — Health Auto Export ingest listener (Phase 3 wearable bridge).

Receives Health Auto Export (HAE) REST-API pushes over the Tailscale tunnel,
authenticates via a bearer token, and persists the raw JSON payload for the
Hermes morning-ingest cron to reconcile into the vault.

DESIGN — deliberately schema-agnostic. This listener does NOT parse HAE's
payload shape. It authenticates + persists raw bytes. The metric -> frontmatter
mapping lives in the separate morning-ingest script (built after we capture a
real HAE Manual Export), so the schema can be learned/iterated without ever
redeploying this always-on service.

PERSISTENCE
  $HAE_HEALTH_DIR/raw/<UTC-ISO-ts>.json   every accepted payload (durable archive)
  $HAE_HEALTH_DIR/last.json               most recent payload (quick inspection)

SECURITY — the Tailscale tunnel is the primary boundary:
  1. ufw default-deny-incoming on the public iface + `ufw allow in on tailscale0`
     -> ONLY the tailnet can reach the port at all (public internet is blocked).
  2. tailnet membership -> only the phone + VPS are on the network, both behind
     the owner's Tailscale account WireGuard keys. This is the real auth.
  3. bearer token (HAE_INGEST_TOKEN) -> OPTIONAL defense-in-depth. Health Auto
     Export's header UI would not reliably attach a custom Authorization header
     (observed 2026-06-05: header silently dropped), so token auth is OFF by
     default. If a token IS configured, a *wrong* token is still rejected (to
     catch misconfig), but a *missing* header is accepted (tailnet is the gate).

ENV
  HAE_INGEST_PORT    default 8789
  HAE_INGEST_TOKEN   optional; if set, a present-but-wrong token is rejected,
                     but a missing Authorization header is allowed (tailnet-only).
  HAE_REQUIRE_TOKEN  default "false"; set "true" to hard-require the token.
  HAE_HEALTH_DIR     default ~/.hermes/health/hae

ENDPOINTS
  POST /hae        authenticated; stores the payload. (Path is not enforced —
                   any POST with a valid token is accepted, so HAE's exact path
                   doesn't matter.)
  GET  /health     unauthenticated liveness check -> {"ok": true}.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("HAE_INGEST_PORT", "8789"))
TOKEN = os.environ.get("HAE_INGEST_TOKEN", "").strip()
REQUIRE_TOKEN = os.environ.get("HAE_REQUIRE_TOKEN", "false").strip().lower() in ("1", "true", "yes")
HEALTH_DIR = Path(
    os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae"))
)
RAW_DIR = HEALTH_DIR / "raw"
MAX_BODY = 25 * 1024 * 1024  # 25 MB cap — HAE payloads are small, this is a guard


def _utc_ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def _log(msg: str) -> None:
    print(
        f"[hae-ingest {datetime.datetime.now(datetime.timezone.utc).isoformat()}] {msg}",
        flush=True,
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authed(self) -> bool:
        # The Tailscale tunnel is the primary security boundary (only the
        # tailnet can reach this port). Token auth is optional defense-in-depth.
        auth = self.headers.get("Authorization", "").strip()
        if not auth:
            # No Authorization header. HAE doesn't reliably send one, so we
            # accept it on the strength of the tailnet boundary — UNLESS the
            # operator explicitly hard-requires a token.
            return not REQUIRE_TOKEN
        # A header IS present -> it must match (catches misconfiguration).
        # Accept "Bearer <token>" or a bare "<token>".
        return bool(TOKEN) and auth in (f"Bearer {TOKEN}", TOKEN)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.path.rstrip("/") in ("/health", "/healthz", ""):
            return self._send(200, {"ok": True, "service": "hae-ingest"})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authed():
            # Diagnostic for auth failures WITHOUT leaking secrets: never log the
            # configured token or the received credential — only their presence and
            # the header names sent (enough to spot a dropped/misnamed header).
            hdr_names = ", ".join(self.headers.keys())
            _log(
                f"401 unauthorized from {self.client_address[0]} path={self.path} "
                f"| auth_header_present={bool(self.headers.get('Authorization'))} "
                f"| token_configured={bool(TOKEN)} | header_names=[{hdr_names}]"
            )
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            _log(f"400 bad content-length={length} from {self.client_address[0]}")
            return self._send(400, {"ok": False, "error": "bad content-length"})

        body = self.rfile.read(length)
        try:
            json.loads(body)
            json_valid = True
        except Exception:
            json_valid = False

        ts = _utc_ts()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = RAW_DIR / f"{ts}.json"
        raw_path.write_bytes(body)
        (HEALTH_DIR / "last.json").write_bytes(body)

        _log(
            f"200 stored {len(body)}B json_valid={json_valid} "
            f"-> raw/{raw_path.name} path={self.path} from {self.client_address[0]}"
        )
        return self._send(
            200,
            {"ok": True, "bytes": len(body), "json_valid": json_valid, "stored": raw_path.name},
        )

    def log_message(self, *args):  # silence default stderr access log; we log explicitly
        return


def main() -> int:
    if REQUIRE_TOKEN and not TOKEN:
        _log("FATAL: HAE_REQUIRE_TOKEN=true but HAE_INGEST_TOKEN is unset; refusing to start.")
        return 1
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    auth_mode = "token-required" if REQUIRE_TOKEN else ("token-optional" if TOKEN else "tailnet-only")
    _log(f"listening on 0.0.0.0:{PORT}  health_dir={HEALTH_DIR}  auth={auth_mode}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
