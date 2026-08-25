#!/usr/bin/env bash
#
# hermes_watchdog.sh — external liveness watchdog for hermes-gateway.
#
# WHY THIS EXISTS
# ---------------
# On 2026-08-21 20:41 the Telegram polling adapter exhausted its retry ladder.
# gateway.run's fatal handler started, popped the adapter out of self.adapters,
# and then died at its one `await` (_safe_adapter_disconnect) before reaching
# the line that queues the platform for background reconnection. That handler
# catches `Exception`, which does NOT include asyncio.CancelledError, so the
# failure was completely silent: no traceback, no timeout warning, no queue.
#
# Result: the process stayed alive with ZERO adapters and NOTHING queued, so
# systemd (Restart=always, which only fires on process exit) reported
# `active (running)` for four days while the agent was deaf.
#
# Upstream fixed this after v0.18.2 — see hermes-agent issues #80598 and #90386,
# which add queue-before-disconnect, an outer deadline, best-effort queueing on
# CancelledError, and a "stranded platform" check that exits the process. Until
# this box is upgraded past v0.18.2, that recovery does not exist here.
#
# This watchdog is deliberately EXTERNAL to the gateway:
#   - it cannot be taken down by the same wedge it is watching for
#   - it survives `deploy.sh` (which rm -rf's parts of ~/.hermes)
#   - it survives a framework upgrade (no vendored-file patch to re-apply)
#
# It is a seatbelt, not the fix. The fix is upgrading the framework.
#
# Managed by https://github.com/SparshK312/sparsh-hermes-agent
# Installed by scripts/monitor/install_watchdog.sh — do not edit in place on the
# VPS; edit here and re-run the installer, or the next deploy silently reverts it.

set -uo pipefail   # NOT -e: a probe that fails must not abort the whole run

SERVICE="${SERVICE:-hermes-gateway.service}"
STATE_FILE="${STATE_FILE:-/home/hermes/.hermes/gateway_state.json}"
ENV_FILE="${ENV_FILE:-/home/hermes/.hermes/.env}"
LOG="${LOG:-/var/log/hermes-watchdog.log}"
VAR_DIR="${VAR_DIR:-/var/lib/hermes-watchdog}"
STRIKE_FILE="$VAR_DIR/strikes"
RESTART_FILE="$VAR_DIR/restarts"

# --- Tunables -----------------------------------------------------------------
# How long a platform may sit in a non-connected state before we act. The
# gateway's own reconnect watcher gets first refuse; this only fires when that
# has demonstrably not worked. 10 min is well past any transient blip (the
# Aug 21 retry ladder ran itself out in 10 minutes).
GRACE_SECONDS="${GRACE_SECONDS:-600}"
# Don't judge a gateway that is still starting up.
STARTUP_GRACE="${STARTUP_GRACE:-180}"
# Consecutive socket-probe failures before acting. At a 2-min timer that is 6
# minutes of the process holding no connection to Telegram.
SOCKET_STRIKES="${SOCKET_STRIKES:-3}"
# Hard ceiling so this can never become the restart loop upstream removed the
# in-process exit to avoid.
MAX_RESTARTS_PER_HOUR="${MAX_RESTARTS_PER_HOUR:-3}"
# DRY_RUN=1 evaluates every check and logs the decision it WOULD take, without
# restarting anything or sending a message. Used by the test matrix below and
# whenever the thresholds get retuned.
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$VAR_DIR" 2>/dev/null

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >>"$LOG"; }

read_strikes()  { [ -f "$STRIKE_FILE" ] && cat "$STRIKE_FILE" 2>/dev/null || echo 0; }
write_strikes() { echo "${1:-0}" >"$STRIKE_FILE" 2>/dev/null; }

# --- Guard 1: is the service even supposed to be running? ---------------------
# If it is stopped/failed, systemd's own Restart=always owns that case, and if a
# human stopped it deliberately we must not fight them.
state="$(systemctl is-active "$SERVICE" 2>/dev/null)"
if [ "$state" != "active" ]; then
  log "skip: $SERVICE is '$state' (systemd owns this case)"
  write_strikes 0
  exit 0
fi

# --- Guard 2: startup grace ---------------------------------------------------
started_us="$(systemctl show "$SERVICE" -p ExecMainStartTimestampMonotonic --value 2>/dev/null)"
now_us="$(cut -d' ' -f1 /proc/uptime | awk '{printf "%d", $1*1000000}')"
if [ -n "$started_us" ] && [ "$started_us" -gt 0 ] 2>/dev/null; then
  age=$(( (now_us - started_us) / 1000000 ))
  if [ "$age" -lt "$STARTUP_GRACE" ]; then
    log "skip: service started ${age}s ago (< ${STARTUP_GRACE}s startup grace)"
    exit 0
  fi
fi

main_pid="$(systemctl show "$SERVICE" -p MainPID --value 2>/dev/null)"

# --- Check A: the gateway's own published platform state ----------------------
# gateway.run writes this synchronously via gateway.status.write_runtime_status
# on every platform transition. On Aug 21 it flipped to "retrying" and froze
# there for four days — this check would have caught it in under 10 minutes.
verdict="$(
python3 - "$STATE_FILE" "$main_pid" "$GRACE_SECONDS" <<'PYEOF'
import json, sys, datetime

path, main_pid, grace = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    with open(path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    print("UNKNOWN state file missing"); sys.exit(0)
except Exception as exc:
    print(f"UNKNOWN state file unreadable: {exc}"); sys.exit(0)

# A state file written by a previous process tells us nothing about this one.
try:
    if main_pid and int(main_pid) and int(data.get("pid", 0)) != int(main_pid):
        print(f"UNKNOWN state file pid={data.get('pid')} != MainPID={main_pid}")
        sys.exit(0)
except Exception:
    pass

platforms = data.get("platforms") or {}
if not platforms:
    print("UNKNOWN no platforms recorded in state file"); sys.exit(0)

now = datetime.datetime.now(datetime.timezone.utc)
bad = []
for name, info in platforms.items():
    st = (info or {}).get("state")
    if st == "connected":
        continue
    # "disabled" is an operator decision (e.g. a revoked relay credential),
    # not a fault. Restarting would not change it and would mask the state.
    if st == "disabled":
        continue
    age = None
    raw = (info or {}).get("updated_at")
    if raw:
        try:
            age = (now - datetime.datetime.fromisoformat(raw)).total_seconds()
        except Exception:
            age = None
    if age is None or age >= grace:
        shown = "unknown" if age is None else f"{int(age)}s"
        bad.append(f"{name}={st} for {shown}")

print(("UNHEALTHY " + "; ".join(bad)) if bad else "HEALTHY")
PYEOF
)"

verdict_kind="${verdict%% *}"
verdict_detail="${verdict#* }"

# --- Check B: does the process actually hold a connection to Telegram? --------
# Guards the documented wedge where PTB's Updater reports running while the
# long-poll task is stuck on a dead httpx connection — the state file would
# still say "connected". Long polling holds a persistent HTTPS connection, so
# zero matching sockets is meaningful; strikes absorb the reconnect gap.
tg_sockets=-1
if [ -n "$main_pid" ] && [ "$main_pid" -gt 0 ] 2>/dev/null && command -v ss >/dev/null 2>&1; then
  tg_sockets="$(
    ss -tnp 2>/dev/null \
      | grep -F "pid=${main_pid}," \
      | grep -E '^ESTAB' \
      | awk '{print $5}' \
      | sed 's/^\[//' \
      | grep -cE '^(149\.154\.|91\.108\.|95\.161\.|2001:67c:4e8:|2001:b28:f23)'
  )"
  [ -z "$tg_sockets" ] && tg_sockets=-1
fi

strikes="$(read_strikes)"
reason=""

if [ "$verdict_kind" = "UNHEALTHY" ]; then
  # Check A carries its own 10-minute grace, so it acts on the first hit.
  reason="platform state: $verdict_detail"
  write_strikes 0
elif [ "$tg_sockets" = "0" ]; then
  strikes=$(( strikes + 1 ))
  write_strikes "$strikes"
  log "warn: no ESTABLISHED Telegram socket on pid=$main_pid (strike $strikes/$SOCKET_STRIKES; state check=$verdict_kind)"
  if [ "$strikes" -ge "$SOCKET_STRIKES" ]; then
    reason="no Telegram socket for $strikes consecutive probes (pid=$main_pid)"
  fi
else
  if [ "$strikes" -ne 0 ]; then
    log "ok: recovered (state=$verdict_kind, telegram_sockets=$tg_sockets); strikes reset"
  fi
  write_strikes 0
  exit 0
fi

[ -z "$reason" ] && exit 0

# --- Rate limit ---------------------------------------------------------------
now_epoch="$(date +%s)"
cutoff=$(( now_epoch - 3600 ))
recent=""
if [ -f "$RESTART_FILE" ]; then
  recent="$(awk -v c="$cutoff" '$1 > c' "$RESTART_FILE" 2>/dev/null)"
fi
recent_count="$(printf '%s' "$recent" | grep -c . )"
if [ "$recent_count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
  log "$([ "$DRY_RUN" = 1 ] && echo "DRY-RUN ")SUPPRESSED restart ($reason) — already restarted $recent_count times in the last hour (cap $MAX_RESTARTS_PER_HOUR). Needs a human."
  exit 0
fi

# --- Act ----------------------------------------------------------------------
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN would restart $SERVICE — $reason (restarts in last hour: $recent_count/$MAX_RESTARTS_PER_HOUR)"
  exit 0
fi

log "RESTARTING $SERVICE — $reason"
{ printf '%s\n' "$recent"; echo "$now_epoch"; } | grep -v '^$' >"$RESTART_FILE" 2>/dev/null
write_strikes 0

if systemctl restart "$SERVICE" 2>>"$LOG"; then
  sleep 25
  after="$(systemctl is-active "$SERVICE" 2>/dev/null)"
  log "restart issued; service is now '$after'"
else
  after="restart-failed"
  log "ERROR: systemctl restart failed"
fi

# --- Tell Sparsh -------------------------------------------------------------
# The whole failure mode was that it happened silently. A restart he never hears
# about is the same bug with a shorter outage.
if [ -r "$ENV_FILE" ]; then
  tok="$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"
  chat="$(grep -m1 '^TELEGRAM_HOME_CHANNEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"
  if [ -n "$tok" ] && [ -n "$chat" ]; then
    curl -s -m 20 -o /dev/null \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "text=Watchdog restarted Hermes. Reason: ${reason}. Service is now '${after}'. (Restarts in the last hour: $((recent_count + 1))/${MAX_RESTARTS_PER_HOUR}.)" \
      "https://api.telegram.org/bot${tok}/sendMessage" \
      && log "notified home channel" || log "notify failed (non-fatal)"
  else
    log "notify skipped: token/channel not found in $ENV_FILE"
  fi
fi

exit 0
