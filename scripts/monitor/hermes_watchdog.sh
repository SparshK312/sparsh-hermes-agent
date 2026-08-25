#!/usr/bin/env bash
#
# hermes_watchdog.sh — external liveness watchdog for hermes-gateway.
#
# WHY THIS EXISTS
# ---------------
# The fatal-error handler in gateway/run.py can stop executing partway through,
# leaving the gateway alive with zero adapters and nothing queued for reconnect.
# It catches `Exception`; asyncio.CancelledError is a `BaseException`, and
# `_await_adapter_cleanup_with_timeout` re-raises it. The escape happens after
# `self.adapters.pop()` and before the reconnect-queue insert, and asyncio does
# not log cancelled tasks, so it is completely silent.
#
# systemd's Restart=always only fires on process EXIT, so the unit reports
# `active (running)` throughout. This has happened twice: 2026-08-04 (stranded
# ~6 days) and 2026-08-21 (4 days). `"queued for background reconnection"` has
# never been logged in the retained journal.
#
# Upstream fixed it after v0.18.2 — hermes-agent #80598 and #90386 add
# queue-before-disconnect, an outer deadline, best-effort queueing on both
# CancelledError and Exception, and a stranded-platform check that exits.
# UPGRADING IS THE REAL FIX. This is a seatbelt for the interim.
#
# DESIGN NOTE — the failure mode of this system is GOING QUIET, not acting
# wrongly. Every path that decides "nothing to do" or "I give up" must still be
# able to reach a human. That is why the alert paths below are deliberately
# redundant and why UNKNOWN is treated as a reportable condition rather than a
# silent skip.
#
# Managed by https://github.com/SparshK312/sparsh-hermes-agent
# Installed by scripts/monitor/install_watchdog.sh.
#
# NOTE: `deploy.sh` does NOT touch /usr/local/bin, so a deploy cannot revert
# this. The inverse hazard is real though: deploy.sh mirrors scripts/monitor/
# into ~/.hermes/scripts/monitor/, planting a copy that NEVER EXECUTES. The
# live file is /usr/local/bin/hermes_watchdog.sh and only install_watchdog.sh
# updates it. Editing either the repo copy or the ~/.hermes copy alone does
# nothing.

set -uo pipefail   # NOT -e: a probe that fails must not abort the whole run

SERVICE="${SERVICE:-hermes-gateway.service}"
STATE_FILE="${STATE_FILE:-/home/hermes/.hermes/gateway_state.json}"
ENV_FILE="${ENV_FILE:-/home/hermes/.hermes/.env}"
LOG="${LOG:-/var/log/hermes-watchdog.log}"
VAR_DIR="${VAR_DIR:-/var/lib/hermes-watchdog}"
CONF_FILE="${CONF_FILE:-/etc/hermes-watchdog.conf}"

# Optional operator config (currently just DEADMAN_URL). Kept out of the repo
# because it holds a per-install secret URL.
# shellcheck disable=SC1090
[ -r "$CONF_FILE" ] && . "$CONF_FILE"

# --- Tunables -----------------------------------------------------------------
# Only these platforms can trigger a restart. Restarting the whole gateway
# because an unpaired WhatsApp bridge is down would kill a healthy Telegram to
# service a platform a restart cannot fix.
WATCH_PLATFORMS="${WATCH_PLATFORMS:-telegram}"
# How long a watched platform may be continuously non-connected before we act.
GRACE_SECONDS="${GRACE_SECONDS:-600}"
# Don't judge a gateway that is still starting up.
STARTUP_GRACE="${STARTUP_GRACE:-180}"
# Hard ceiling so this can never become the restart loop upstream removed the
# in-process exit to avoid.
MAX_RESTARTS_PER_HOUR="${MAX_RESTARTS_PER_HOUR:-3}"
# If Check A has been unable to read a verdict for this long, Check A is
# effectively disabled and a human needs to know.
UNKNOWN_ALERT_SECONDS="${UNKNOWN_ALERT_SECONDS:-3600}"
# Minimum gap between repeats of the same non-restart alert, so a stuck
# condition notifies hourly rather than every 2 minutes.
ALERT_REPEAT_SECONDS="${ALERT_REPEAT_SECONDS:-3600}"
# Health-gated dead-man's switch. Pinged ONLY on a green run, so the absence of
# pings means "unhealthy OR dead OR box gone" — see CONF_FILE for setup.
DEADMAN_URL="${DEADMAN_URL:-}"
# DRY_RUN=1 evaluates every check and logs the decision it WOULD take. It is
# side-effect free: no restart, no alert, no state written, no deadman ping.
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$VAR_DIR" 2>/dev/null

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >>"$LOG"; }

now_epoch="$(date +%s)"

# --- State helpers ------------------------------------------------------------
# All reads validate; a corrupt file must degrade to 0, never abort the run
# (finding: `set -u` + arithmetic on a non-numeric string kills the script
# silently, and only on the detection branch).
read_num() {
  local f="$VAR_DIR/$1" v=""
  [ -f "$f" ] && v="$(cat "$f" 2>/dev/null)"
  case "$v" in ''|*[!0-9]*) echo 0 ;; *) echo "$v" ;; esac
}
write_num() {
  [ "$DRY_RUN" = "1" ] && return 0
  echo "${2:-0}" >"$VAR_DIR/$1" 2>/dev/null
}
clear_state() {
  [ "$DRY_RUN" = "1" ] && return 0
  rm -f "$VAR_DIR/$1" 2>/dev/null
}

# --- Alerting -----------------------------------------------------------------
# Two channels on purpose. The Telegram channel is CORRELATED with the failure
# being reported (if Telegram or the network is the problem, the alert about it
# silently fails), so it can never be the only one.
telegram_alert() {
  local msg="$1" tok chat
  [ "$DRY_RUN" = "1" ] && { log "DRY-RUN would alert: $msg"; return 0; }
  [ -r "$ENV_FILE" ] || { log "alert skipped: $ENV_FILE unreadable"; return 1; }
  tok="$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"
  chat="$(grep -m1 '^TELEGRAM_HOME_CHANNEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ')"
  [ -n "$tok" ] && [ -n "$chat" ] || { log "alert skipped: token/channel not found"; return 1; }
  # Strip characters that would break curl's config-file quoting.
  msg="$(printf '%s' "$msg" | tr -d '"\\' | tr '\n' ' ')"
  # --config - keeps the bot token OFF the process command line (it would
  # otherwise be visible to any local user via `ps auxww`; /proc is mounted
  # without hidepid).
  if curl -sS -m 20 -o /dev/null --config - <<CURLCFG
url = "https://api.telegram.org/bot${tok}/sendMessage"
data-urlencode = "chat_id=${chat}"
data-urlencode = "text=${msg}"
CURLCFG
  then log "alerted home channel"; return 0
  else log "ALERT DELIVERY FAILED (this is why the dead-man's switch exists)"; return 1
  fi
}

# Rate-limited alert for conditions that persist, keyed so different conditions
# don't suppress each other.
alert_throttled() {
  local key="$1" msg="$2" last
  last="$(read_num "alert_$key")"
  if [ $(( now_epoch - last )) -ge "$ALERT_REPEAT_SECONDS" ]; then
    write_num "alert_$key" "$now_epoch"
    telegram_alert "$msg"
  fi
}

# --- Mode: --alert (used by the OnFailure= unit) ------------------------------
if [ "${1:-}" = "--alert" ]; then
  shift
  log "EXTERNAL ALERT: $*"
  telegram_alert "Hermes watchdog: $*"
  exit 0
fi

# --- Guard 1: is the service even supposed to be running? ---------------------
state="$(systemctl is-active "$SERVICE" 2>/dev/null)"
if [ "$state" != "active" ]; then
  log "skip: $SERVICE is '$state' (systemd Restart=always owns this case)"
  clear_state strikes; clear_state first_bad; clear_state first_unknown
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
# gateway/status.py::write_runtime_status writes atomically (mkstemp + fsync +
# os.replace) on every platform transition, synchronously, from two separate
# call sites that both land BEFORE the await that stalls. That is what makes
# this the check that catches the real bug.
verdict="$(
python3 - "$STATE_FILE" "$main_pid" "$WATCH_PLATFORMS" <<'PYEOF'
import json, sys, datetime

path, main_pid, watch = sys.argv[1], sys.argv[2], sys.argv[3]
watched = {w.strip().lower() for w in watch.split(",") if w.strip()}

def unknown(msg):
    print("VERDICT UNKNOWN " + msg); sys.exit(0)

try:
    with open(path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    unknown(f"state file missing: {path}")
except Exception as exc:
    unknown(f"state file unreadable: {exc}")

# A state file written by a previous process says nothing about this one.
try:
    if main_pid and int(main_pid) and int(data.get("pid", 0)) != int(main_pid):
        unknown(f"state file pid={data.get('pid')} != MainPID={main_pid}")
except Exception:
    pass

platforms = data.get("platforms") or {}
if not platforms:
    unknown("no platforms recorded in state file")

present = {k.lower() for k in platforms}
missing = watched - present
if missing:
    unknown("watched platform(s) absent from state file: " + ",".join(sorted(missing)))

now = datetime.datetime.now(datetime.timezone.utc)
bad = []
for name, info in platforms.items():
    if name.lower() not in watched:
        continue
    info = info or {}
    # A missing "state" key is not evidence of health.
    st = info.get("state", "absent")
    # "disabled" is an operator decision (revoked relay credential); "paused"
    # is an explicit `/platform pause`, which freezes updated_at BY DESIGN
    # (next_retry=inf) and would otherwise look permanently stale.
    if st in ("connected", "disabled", "paused"):
        continue
    raw = info.get("updated_at")
    when = "no timestamp"
    if raw:
        try:
            secs = int((now - datetime.datetime.fromisoformat(raw)).total_seconds())
            when = f"state written {secs}s ago"
        except Exception:
            when = "timestamp unparseable"
    bad.append(f"{name}={st} ({when})")

print("VERDICT BAD " + "; ".join(bad) if bad else "VERDICT OK")
PYEOF
)"

verdict_kind="$(printf '%s' "$verdict" | awk '{print $2}')"
verdict_detail="$(printf '%s' "$verdict" | cut -d' ' -f3-)"

# --- Check B: Telegram socket presence — OBSERVATION ONLY, NEVER RESTARTS -----
# Demoted from restart authority after two independent findings:
#   1. The old hardcoded prefixes were STRING prefixes, not CIDRs. `149.154.`
#      also covers Techwareca and Net By Net; `91.108.`/`95.161.` are largely
#      eTelecom. Any ordinary outbound HTTPS during an agent turn could match
#      and silently reset the strike counter.
#   2. Telegram announces prefixes absent from the old list, and its own
#      published cidr.txt was last modified in 2021. A re-IP would have
#      restarted a HEALTHY service, three times, then gone silent.
# Resolving the name at check time has no list to rot. Kept as a log line
# because it is useful forensics; it is not trustworthy enough to act on, and
# a half-open socket stays ESTABLISHED for up to tcp_keepalive_time (7200s)
# anyway, so its absence is meaningful but its presence proves little.
tg_sockets=-1
if [ -n "$main_pid" ] && [ "$main_pid" -gt 0 ] 2>/dev/null && command -v ss >/dev/null 2>&1; then
  tg_ips="$( { getent ahosts api.telegram.org 2>/dev/null | awk '{print $1}'
               printf '149.154.166.110\n149.154.167.220\n'; } | sort -u )"
  if [ -n "$tg_ips" ]; then
    tg_sockets="$(
      ss -tnH 2>/dev/null -p \
        | grep -F "pid=${main_pid}," \
        | awk '$1=="ESTAB"{print $5}' \
        | sed -E 's/^\[([^]]*)\]:[0-9]+$/\1/; t; s/:[0-9]+$//' \
        | grep -Fxc -f <(printf '%s\n' "$tg_ips")
    )"
    [ -z "$tg_sockets" ] && tg_sockets=0
  fi
fi

# --- Decide -------------------------------------------------------------------
reason=""

if [ "$verdict_kind" = "UNKNOWN" ]; then
  # An UNKNOWN verdict means Check A is DISABLED. Previously this logged only
  # when strikes were non-zero — i.e. never — so Check A could be dead forever
  # behind a perfectly clean log. Track it and escalate.
  first_unknown="$(read_num first_unknown)"
  [ "$first_unknown" -eq 0 ] && { first_unknown="$now_epoch"; write_num first_unknown "$now_epoch"; }
  unknown_for=$(( now_epoch - first_unknown ))
  alert_throttled "unknown" "Hermes watchdog cannot evaluate the gateway (${unknown_for}s and counting): ${verdict_detail}. Check A is effectively DISABLED until this is fixed."
  log "UNKNOWN for ${unknown_for}s: $verdict_detail (telegram_sockets=$tg_sockets)"
  clear_state first_bad
  # Deliberately no restart: we do not know anything is wrong.
  exit 0
fi
clear_state first_unknown

if [ "$verdict_kind" = "BAD" ]; then
  # Measure OUR OWN observed duration, not the age of the last state write.
  # The reconnect watcher's backoff cap (300s) is shorter than GRACE (600s) and
  # every retry refreshes updated_at, so a genuinely-down-but-retrying platform
  # would never have aged past the grace under the old logic.
  first_bad="$(read_num first_bad)"
  [ "$first_bad" -eq 0 ] && { first_bad="$now_epoch"; write_num first_bad "$now_epoch"; }
  bad_for=$(( now_epoch - first_bad ))
  if [ "$bad_for" -ge "$GRACE_SECONDS" ]; then
    reason="$verdict_detail — non-connected for ${bad_for}s"
  else
    log "watching: $verdict_detail — non-connected for ${bad_for}s (< ${GRACE_SECONDS}s grace)"
    exit 0
  fi
else
  # Healthy.
  if [ "$(read_num first_bad)" -ne 0 ]; then
    log "ok: all watched platforms connected again; clearing bad-since marker"
  fi
  clear_state first_bad
  if [ "$tg_sockets" = "0" ]; then
    # Observation only — this alone never restarts anything.
    log "note: state says connected but 0 sockets matched api.telegram.org (forensics only, no action)"
  fi
  # Health-gated dead-man's switch: ping ONLY when green, so silence upstream
  # means unhealthy, dead watchdog, or dead box — all three of which the
  # on-box checks structurally cannot report.
  if [ -n "$DEADMAN_URL" ] && [ "$DRY_RUN" != "1" ]; then
    curl -fsS -m 10 -o /dev/null "$DEADMAN_URL" 2>/dev/null \
      || log "deadman ping failed (non-fatal)"
  fi
  exit 0
fi

# --- Rate limit ---------------------------------------------------------------
cutoff=$(( now_epoch - 3600 ))
recent=""
[ -f "$VAR_DIR/restarts" ] && recent="$(awk -v c="$cutoff" '$1 ~ /^[0-9]+$/ && $1 > c' "$VAR_DIR/restarts" 2>/dev/null)"
recent_count="$(printf '%s' "$recent" | grep -c . )"

if [ "$recent_count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
  # CRITICAL PATH. This used to log one line and exit — with the notification
  # code below the exit, i.e. unreachable. That made the watchdog's own
  # give-up state identical to the outage it exists to prevent.
  log "SUPPRESSED restart ($reason) — $recent_count restarts already in the last hour (cap $MAX_RESTARTS_PER_HOUR). RESTARTS ARE NOT FIXING THIS."
  alert_throttled "suppressed" "Hermes is DOWN and restarting is not fixing it. ${reason}. Watchdog has given up after ${recent_count} restarts this hour and needs you to look. Check: journalctl -u hermes-gateway -n 100"
  exit 0
fi

# --- Act ----------------------------------------------------------------------
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN would restart $SERVICE — $reason (restarts in last hour: $recent_count/$MAX_RESTARTS_PER_HOUR)"
  exit 0
fi

log "RESTARTING $SERVICE — $reason"
# Written BEFORE the restart so a killed script still counts its attempt.
{ printf '%s\n' "$recent"; echo "$now_epoch"; } | grep -E '^[0-9]+$' >"$VAR_DIR/restarts" 2>/dev/null
clear_state strikes; clear_state first_bad

# Snapshot the evidence. The Aug-21 postmortem had to infer what the state file
# said because nothing preserved it before it was overwritten.
cp -f "$STATE_FILE" "$VAR_DIR/last_unhealthy_state.json" 2>/dev/null

if systemctl restart "$SERVICE" 2>>"$LOG"; then
  sleep 25
  after="$(systemctl is-active "$SERVICE" 2>/dev/null)"
  log "restart issued; service is now '$after'"
else
  after="restart-failed"
  log "ERROR: systemctl restart failed"
fi

telegram_alert "Watchdog restarted Hermes. Reason: ${reason}. Service is now '${after}'. Restarts this hour: $((recent_count + 1))/${MAX_RESTARTS_PER_HOUR}."
exit 0
