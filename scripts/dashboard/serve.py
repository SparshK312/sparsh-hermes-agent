#!/usr/bin/env python3
"""
serve.py — Hermes ops + health dashboard (Phase 1).

A single-page, READ-ONLY web dashboard served on the VPS over Tailscale only.
Shows agent-ops (cost / usage / cache / tool-errors / cron health / routing) from
~/.hermes/state.db + cron/jobs.json + logs, and health data (macros / weight /
sleep / steps + the latest fitness chart PNGs) from the vault.

DESIGN
  - Pure stdlib (http.server + sqlite3 + json + csv + re) → runs under system
    python3, no venv, no third-party deps. Mirrors hae_ingest.py.
  - Strictly READ-ONLY. state.db is opened with mode=ro (uri) so it can never
    lock or mutate the live 169MB DB. Every other source is a file read. This
    service cannot break the live agent — it only observes.
  - Charts are NOT rendered here; we serve the PNGs the existing fitness crons
    already write to `07 - Health/Charts/`. So no matplotlib/cairosvg needed.
  - Every tile is wrapped so one failing source degrades to an error card
    instead of taking down the page.

SECURITY — the Tailscale tunnel is the boundary (same model as hae-ingest):
  ufw default-deny on the public iface + `ufw allow in on tailscale0 to any
  port 8790` → only the tailnet (Mac + phone) can reach it. No auth layer here.

ENV
  DASHBOARD_PORT   default 8790
  HERMES_VAULT     default /home/hermes/vault

ENDPOINTS
  GET /              the dashboard HTML (auto-refreshes every 60s)
  GET /charts/<f>    stream a PNG from the vault Charts dir (basename-validated)
  GET /health        liveness -> {"ok": true}
"""
from __future__ import annotations

import csv
import datetime
import html
import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("DASHBOARD_PORT", "8790"))
HOME = Path.home()
HERMES_HOME = HOME / ".hermes"
STATE_DB = HERMES_HOME / "state.db"
JOBS_JSON = HERMES_HOME / "cron" / "jobs.json"
ROUTER_LOG = HERMES_HOME / "logs" / "intent_router.log"
VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
DAILY_DIR = VAULT / "04 - Daily Notes"
METRICS_CSV = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
CHARTS_DIR = VAULT / "07 - Health" / "Charts"
COACH_MEM = VAULT / "07 - Health" / "Coach Memory.md"

# Daily nutrition targets (mirror vault_log.py KCAL_TARGET / Coach Memory block).
KCAL_TARGET = 2400
PROTEIN_TARGET = 140
MONTHLY_CEILING = 50.0  # self-funded budget ceiling

# Anthropic per-MTok pricing (mirror monitor/cost_monitor.py PRICING); used only
# as a fallback when a session's estimated_cost_usd is missing.
PRICING = {"haiku": (1.0, 5.0, 0.10, 1.25), "sonnet": (3.0, 15.0, 0.30, 3.75),
           "opus": (5.0, 25.0, 0.50, 6.25)}
DEFAULT_PRICE = (3.0, 15.0, 0.30, 3.75)


# ----------------------------------------------------------------------------
# data helpers
# ----------------------------------------------------------------------------
def db_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)


def _price_for(model: str):
    m = (model or "").lower()
    for k, v in PRICING.items():
        if k in m:
            return v
    return DEFAULT_PRICE


def _fallback_cost(model, inp, out, cr, cw, reas) -> float:
    pi, po, pcr, pcw = _price_for(model)
    inp, out, cr, cw, reas = (x or 0 for x in (inp, out, cr, cw, reas))
    return (inp * pi + (out + reas) * po + cr * pcr + cw * pcw) / 1_000_000


def _fm_field(text: str, key: str):
    """First `key: value` in a note's YAML frontmatter."""
    m = re.search(rf"^{re.escape(key)}:\s*([^\n]+)", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# agent-ops tiles
# ----------------------------------------------------------------------------
def tile_spend() -> str:
    con = db_ro()
    try:
        now = datetime.datetime.now()
        sod = datetime.datetime(now.year, now.month, now.day).timestamp()
        som = datetime.datetime(now.year, now.month, 1).timestamp()
        d30 = now.timestamp() - 30 * 86400

        def spend(since):
            rows = con.execute(
                "SELECT model,input_tokens,output_tokens,cache_read_tokens,"
                "cache_write_tokens,reasoning_tokens,estimated_cost_usd,started_at "
                "FROM sessions WHERE started_at>=?", (since,)).fetchall()
            total = 0.0
            for r in rows:
                est = r[6]
                total += est if (est and est > 0) else _fallback_cost(*r[:6])
            return total

        today, mtd, last30 = spend(sod), spend(som), spend(d30)
    finally:
        con.close()
    pct = min(100, int(mtd / MONTHLY_CEILING * 100)) if MONTHLY_CEILING else 0
    bar_color = "#e5534b" if pct >= 90 else "#d9a441" if pct >= 70 else "#57ab5a"
    return _card("💸 Spend", f"""
      <div class="stat-row">
        <div class="stat"><div class="big">${today:.2f}</div><div class="lbl">today</div></div>
        <div class="stat"><div class="big">${mtd:.2f}</div><div class="lbl">month-to-date</div></div>
        <div class="stat"><div class="big">${last30:.2f}</div><div class="lbl">last 30d</div></div>
      </div>
      <div class="meter"><div class="fill" style="width:{pct}%;background:{bar_color}"></div></div>
      <div class="lbl">MTD vs ${MONTHLY_CEILING:.0f} ceiling · {pct}%</div>""")


def tile_daily_cost() -> str:
    con = db_ro()
    try:
        rows = con.execute(
            "SELECT date(started_at,'unixepoch','localtime') d,"
            "ROUND(SUM(estimated_cost_usd),4) c "
            "FROM sessions GROUP BY d ORDER BY d DESC LIMIT 14").fetchall()
    finally:
        con.close()
    rows = list(reversed(rows))
    mx = max((r[1] or 0) for r in rows) or 1
    bars = "".join(
        f'<div class="bcol" title="{r[0]}: ${r[1] or 0:.2f}">'
        f'<div class="bar" style="height:{int((r[1] or 0)/mx*60)+2}px"></div>'
        f'<div class="bx">{r[0][5:]}</div></div>' for r in rows)
    return _card("📈 Daily cost (14d)", f'<div class="bars">{bars}</div>')


def tile_by(dimension: str, label: str) -> str:
    con = db_ro()
    try:
        rows = con.execute(
            f"SELECT COALESCE({dimension},'(none)') k, COUNT(*) n,"
            "ROUND(SUM(estimated_cost_usd),3) c FROM sessions "
            "GROUP BY k ORDER BY c DESC").fetchall()
    finally:
        con.close()
    body = "".join(
        f"<tr><td>{html.escape(str(r[0]))}</td><td>{r[1]}</td>"
        f"<td>${r[2] or 0:.2f}</td></tr>" for r in rows)
    return _card(f"🧠 By {label}", f'<table><tr><th>{label}</th><th>sess</th><th>$</th></tr>{body}</table>')


def tile_cache_calls() -> str:
    con = db_ro()
    try:
        r = con.execute(
            "SELECT ROUND(100.0*SUM(cache_read_tokens)/"
            "NULLIF(SUM(input_tokens+cache_read_tokens+cache_write_tokens),0),1),"
            "SUM(api_call_count) FROM sessions "
            "WHERE started_at>=?", (datetime.datetime.now().timestamp() - 30 * 86400,)).fetchone()
        today = con.execute(
            "SELECT SUM(api_call_count) FROM sessions WHERE started_at>=?",
            (datetime.datetime(*datetime.datetime.now().timetuple()[:3]).timestamp(),)).fetchone()
    finally:
        con.close()
    return _card("⚡ Efficiency (30d)", f"""
      <div class="stat-row">
        <div class="stat"><div class="big">{r[0] or 0:.0f}%</div><div class="lbl">cache hit</div></div>
        <div class="stat"><div class="big">{r[1] or 0:,}</div><div class="lbl">API calls 30d</div></div>
        <div class="stat"><div class="big">{today[0] or 0:,}</div><div class="lbl">API calls today</div></div>
      </div>""")


def tile_tool_errors() -> str:
    con = db_ro()
    try:
        since = datetime.datetime.now().timestamp() - 30 * 86400
        rows = con.execute(
            "SELECT content FROM messages WHERE role='tool' AND timestamp>=?",
            (since,)).fetchall()
    finally:
        con.close()
    total = len(rows)
    errs = 0
    for (content,) in rows:
        try:
            obj = json.loads(content) if content else {}
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("error") not in (None, "", False):
            errs += 1
        elif isinstance(obj.get("exit_code"), int) and obj["exit_code"] != 0:
            errs += 1
        elif obj.get("success") is False or obj.get("is_error") is True:
            errs += 1
    rate = (errs / total * 100) if total else 0
    color = "#e5534b" if rate > 5 else "#57ab5a"
    return _card("🧯 Tool errors (30d)",
                 f'<div class="stat-row"><div class="stat">'
                 f'<div class="big" style="color:{color}">{rate:.1f}%</div>'
                 f'<div class="lbl">{errs} of {total} tool calls</div></div></div>')


_DAYS = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _human_sched(expr: str) -> str:
    """Turn a cron expr into plain English (best-effort; falls back to raw)."""
    try:
        mn, hr, dom, mon, dow = expr.split()
    except ValueError:
        return expr

    def clock(h, m):
        h, m = int(h), int(m)
        ap = "AM" if h < 12 else "PM"
        return f"{(h % 12) or 12}:{m:02d} {ap}"

    if hr == "*" and mn == "0":
        return "Hourly"
    if mn.startswith("*/") and "-" in hr:
        return f"Every {mn[2:]}m · {hr.replace('-', '–')} h"
    if mn.isdigit() and hr.isdigit():
        t = clock(hr, mn)
        if dom != "*" and mon != "*":
            return f"{_MONTHS[int(mon)]} {dom} · {t}"
        if dow == "*":
            return f"Daily · {t}"
        if dow == "1-5":
            return f"Weekdays · {t}"
        return f"{' & '.join(_DAYS.get(d, d) for d in dow.split(','))} · {t}"
    return expr


def tile_cron() -> str:
    jobs = json.loads(JOBS_JSON.read_text()).get("jobs", [])
    healthy_n = down_n = 0
    rows = []
    for j in jobs:
        enabled = j.get("enabled", False)
        st = j.get("last_status")
        err = j.get("last_error") or j.get("last_delivery_error")
        healthy = st in ("ok", None) and not err
        if enabled and not healthy:
            badge, down_n = '<span class="dot red"></span>', down_n + 1
        elif not enabled or st is None:
            badge = '<span class="dot grey"></span>'
        else:
            badge, healthy_n = '<span class="dot green"></span>', healthy_n + 1
        last = (j.get("last_run_at") or "")[:16].replace("T", " ")
        rows.append(
            f"<tr><td>{badge}{html.escape(j.get('name','?'))}</td>"
            f"<td>{html.escape(_human_sched(j.get('schedule',{}).get('expr','')))}</td>"
            f"<td>{last[5:] if last else '—'}</td></tr>")
    summary = f'<div class="lbl">{healthy_n} healthy · {down_n} need attention</div>'
    return _card("⏰ Automations", summary
                 + '<table><tr><th>job</th><th>runs</th><th>last</th></tr>'
                 + "".join(rows) + "</table>")


# (tile_routing removed 2026-07-18 — showed unapplied escalation intent; see render_page note.)


# ----------------------------------------------------------------------------
# health tiles
# ----------------------------------------------------------------------------
def _today_note() -> str:
    p = DAILY_DIR / f"{datetime.date.today().isoformat()}.md"
    return p.read_text(errors="replace") if p.exists() else ""


def _latest_metrics_row():
    if not METRICS_CSV.exists():
        return {}
    with METRICS_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else {}


def tile_macros() -> str:
    note = _today_note()
    kcal = _num(_fm_field(note, "kcal")) or 0
    prot = _num(_fm_field(note, "protein_g")) or 0
    carbs = _num(_fm_field(note, "carbs_g")) or 0
    fat = _num(_fm_field(note, "fat_g")) or 0
    kp = min(100, int(kcal / KCAL_TARGET * 100)) if KCAL_TARGET else 0
    pp = min(100, int(prot / PROTEIN_TARGET * 100)) if PROTEIN_TARGET else 0
    return _card("🍽️ Today's fuel", f"""
      <div class="stat-row">
        <div class="stat"><div class="big">{kcal:.0f}</div><div class="lbl">/ {KCAL_TARGET} kcal</div></div>
        <div class="stat"><div class="big">{prot:.0f}g</div><div class="lbl">/ {PROTEIN_TARGET}g protein</div></div>
      </div>
      <div class="meter"><div class="fill" style="width:{kp}%;background:#d9a441"></div></div>
      <div class="meter"><div class="fill" style="width:{pp}%;background:#6cb6ff"></div></div>
      <div class="lbl">{carbs:.0f}g carbs · {fat:.0f}g fat</div>""")


def tile_body() -> str:
    note = _today_note()
    m = _latest_metrics_row()
    weight = _fm_field(note, "weight")
    sleep = _fm_field(note, "sleep_hours") or m.get("sleep_total_h")
    steps = _fm_field(note, "steps") or m.get("steps")
    rhr = m.get("resting_hr")
    hrv = m.get("hrv_ms")

    def cell(v, unit, lbl):
        val = f"{v}{unit}" if v not in (None, "", "None") else "—"
        return f'<div class="stat"><div class="big">{val}</div><div class="lbl">{lbl}</div></div>'
    return _card("🫀 Body", '<div class="stat-row">'
                 + cell(weight, " lb", "weight")
                 + cell(sleep, "h", "sleep")
                 + cell(steps, "", "steps")
                 + "</div><div class='stat-row'>"
                 + cell(rhr, "", "RHR")
                 + cell(hrv, "ms", "HRV")
                 + "</div>")


def tile_coach() -> str:
    if not COACH_MEM.exists():
        return _card("🏋️ Coach", '<div class="lbl">no coach memory</div>')
    text = COACH_MEM.read_text(errors="replace")
    m = re.search(r"##+\s*Adherence snapshot.*?\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL | re.IGNORECASE)
    snap = m.group(1).strip() if m else ""
    snap = html.escape(snap[:600]) if snap else "no snapshot yet"
    return _card("🏋️ Coach — adherence", f'<div class="mono">{snap}</div>')


def _latest_chart(pattern: str):
    if not CHARTS_DIR.exists():
        return None
    files = sorted(CHARTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].name if files else None


def tile_charts() -> str:
    families = [("coverage-*.png", "Muscle coverage"), ("trends*.png", "Trends"),
                ("fuel-*.png", "Fuel"), ("week-food-*.png", "Week in food")]
    blocks = []
    for pat, label in families:
        name = _latest_chart(pat)
        if name:
            blocks.append(
                f'<figure><figcaption>{label}</figcaption>'
                f'<img loading="lazy" src="/charts/{html.escape(name)}" alt="{label}"></figure>')
    return _card("📊 Latest charts",
                 f'<div class="charts">{"".join(blocks)}</div>'
                 if blocks else '<div class="lbl">no charts yet</div>')


# ----------------------------------------------------------------------------
# page assembly
# ----------------------------------------------------------------------------
def _card(title: str, body: str) -> str:
    return f'<section class="card"><h2>{title}</h2>{body}</section>'


def _safe(fn) -> str:
    try:
        return fn()
    except Exception as e:  # one bad source must not take down the page
        return _card("⚠️ error", f'<div class="mono">{html.escape(fn.__name__)}: {html.escape(str(e))}</div>')


CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,system-ui,sans-serif;padding:16px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:#8b949e;font-size:12px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px}
.card h2{font-size:13px;margin:0 0 10px;color:#e6edf3;font-weight:600}
.stat-row{display:flex;gap:16px;flex-wrap:wrap}
.stat{flex:1;min-width:70px}
.big{font-size:22px;font-weight:700;color:#e6edf3}
.lbl{font-size:11px;color:#8b949e}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#8b949e;font-weight:500;padding:2px 4px;border-bottom:1px solid #30363d}
td{padding:3px 4px;border-bottom:1px solid #21262d}
.meter{height:6px;background:#21262d;border-radius:4px;margin:6px 0;overflow:hidden}
.fill{height:100%;border-radius:4px}
.bars{display:flex;align-items:flex-end;gap:3px;height:80px}
.bcol{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}
.bar{width:100%;background:#388bfd;border-radius:2px 2px 0 0}
.bx{font-size:8px;color:#6e7681;margin-top:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.dot.green{background:#57ab5a}.dot.red{background:#e5534b}.dot.grey{background:#6e7681}
.logline{font:11px/1.4 ui-monospace,monospace;color:#8b949e;padding:2px 0;border-bottom:1px solid #21262d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mono{font:11px/1.5 ui-monospace,monospace;white-space:pre-wrap;color:#adbac7}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:10px}
figure{margin:0}figcaption{font-size:11px;color:#8b949e;margin-bottom:4px}
img{width:100%;border-radius:6px;background:#fff}
"""


def render_page() -> bytes:
    now = datetime.datetime.now().strftime("%a %b %-d, %-I:%M %p")
    # All tiles are zero-arg callables so _safe() can wrap each one; tile_by
    # takes args, so bind them with lambdas.
    # Lead with what Sparsh likes (cost + visuals); technical internals demoted.
    # Routing tile removed 2026-07-18: it showed intent-router *decisions* that
    # aren't actually applied (escalation is logged-but-broken — all turns ran on
    # Haiku). Re-add once Phase 6 makes routing real + verifiable.
    tiles = [tile_spend, tile_macros, tile_body, tile_charts,
             tile_daily_cost, lambda: tile_by("model", "model"),
             lambda: tile_by("source", "source"), tile_cache_calls,
             tile_coach, tile_cron, tile_tool_errors]
    body = "".join(_safe(t) for t in tiles)
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Hermes Dashboard</title><style>{CSS}</style></head><body>
<h1>Hermes</h1><div class="sub">updated {now} · auto-refresh 60s</div>
<div class="grid">{body}</div></body></html>"""
    return doc.encode()


# ----------------------------------------------------------------------------
# server
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, render_page())
        if path == "/health":
            return self._send(200, json.dumps({"ok": True}).encode(),
                              "application/json")
        if path.startswith("/charts/"):
            name = os.path.basename(path[len("/charts/"):])
            if not re.fullmatch(r"[\w.\-]+\.png", name):
                return self._send(404, b"not found", "text/plain")
            fp = CHARTS_DIR / name
            if not fp.exists():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, fp.read_bytes(), "image/png")
        return self._send(404, b"not found", "text/plain")

    def log_message(self, *a):  # silence default access log
        return


def main() -> int:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[dashboard] listening on 0.0.0.0:{PORT}  vault={VAULT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
