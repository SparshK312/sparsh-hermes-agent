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
# UPGRADED 2026-08-25: the box now runs v0.20.5 and those fixes ARE present
# (_queue_retryable_fatal_platform is called before the disconnect await, tagged
# #80598). So the original justification for restart authority is largely gone.
# What remains useful is catching states upstream cannot recover from; what
# remains dangerous is false positives. Hence: `retrying` now alerts instead of
# restarting, and Check B has no restart authority at all.
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

# Single-instance. systemd serialises the timer, but a manual invocation can
# race it — the live log already shows two runs stamped the same second — and
# both mutate first_bad/restarts unguarded.
if [ "${_WD_LOCKED:-}" != "1" ] && command -v flock >/dev/null 2>&1; then
    export _WD_LOCKED=1
    exec flock -w 30 /var/lock/hermes-watchdog.lock "$0" "$@"
fi

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

if [ "$DRY_RUN" = "1" ]; then
  LOG=/dev/stdout          # a dry run must not write the log an operator is tailing
else
  mkdir -p "$VAR_DIR" 2>/dev/null
fi

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >>"$LOG"; }

now_epoch="$(date +%s)"

# --- State helpers ------------------------------------------------------------
# All reads validate; a corrupt file must degrade to 0, never abort the run
# (finding: `set -u` + arithmetic on a non-numeric string kills the script
# silently, and only on the detection branch).
read_num() {
  local f="$VAR_DIR/$1" v=""
  [ -f "$f" ] && v="$(cat "$f" 2>/dev/null)"
  # Bound the width: a 23-digit value wraps bash's signed 64-bit arithmetic to a
  # large negative number, which disarms both the restart and every alert.
  case "$v" in
    ''|*[!0-9]*) echo 0 ;;
    ?????????????*) echo 0 ;;
    *) echo "$v" ;;
  esac
}
write_num() {
  [ "$DRY_RUN" = "1" ] && return 0
  # Atomic: a partially-written counter reads back as 0, which silently resets
  # the grace window. Same temp+rename discipline gateway_state.json uses.
  printf '%s\n' "${2:-0}" >"$VAR_DIR/.$1.tmp" 2>/dev/null && \
    mv -f "$VAR_DIR/.$1.tmp" "$VAR_DIR/$1" 2>/dev/null
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
  # Strip surrounding quotes ONLY. The previous `tr -d` also deleted every space,
  # so `TELEGRAM_HOME_CHANNEL=123 # home` silently became `123#home` and the
  # alert went nowhere.
  tok="$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
         | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')"
  chat="$(grep -m1 '^TELEGRAM_HOME_CHANNEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
          | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')"
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
  # Same clock-step hazard: a negative delta never clears the throttle, so NO
  # alert of any kind would fire until the clock caught up.
  [ "$last" -gt "$now_epoch" ] && last=0
  if [ $(( now_epoch - last )) -ge "$ALERT_REPEAT_SECONDS" ]; then
    write_num "alert_$key" "$now_epoch"
    telegram_alert "$msg"
  fi
}

# --- Mode: --alert (used by the OnFailure= unit) ------------------------------
if [ "${1:-}" = "--alert-unit" ]; then
  # Report on the unit systemd names, not always the watchdog.
  failed_unit="${2:-unknown.service}"
  res="$(systemctl show "$failed_unit" -p Result --value 2>/dev/null)"
  code="$(systemctl show "$failed_unit" -p ExecMainStatus --value 2>/dev/null)"
  gw="$(systemctl is-active "$SERVICE" 2>/dev/null)"
  tail_lines="$(journalctl -u "$failed_unit" -n 6 --no-pager 2>/dev/null | tail -4 | tr '\n' ' ')"
  body="Hermes: ${failed_unit} FAILED (result=${res:-unknown}, exit=${code:-?}). Gateway is ${gw:-unknown}. Recent: ${tail_lines:-<no journal>}"
  log "UNIT FAILURE: $body"
  telegram_alert "$body"
  exit 0
fi

if [ "${1:-}" = "--alert" ]; then
  shift
  # Report OBSERVED state, never a canned assertion. This path is reachable by
  # hand (`systemctl start hermes-watchdog-failure.service`), and a fixed string
  # saying "the watchdog FAILED" then claims a failure that did not happen.
  wd_result="$(systemctl show hermes-watchdog.service -p Result --value 2>/dev/null)"
  wd_timer="$(systemctl is-active hermes-watchdog.timer 2>/dev/null)"
  gw="$(systemctl is-active "$SERVICE" 2>/dev/null)"
  if [ "$wd_result" = "success" ] && [ "$wd_timer" = "active" ]; then
    # Nothing is actually broken — say so plainly instead of alarming him.
    body="Watchdog alert path was triggered, but nothing looks wrong: last watchdog run=success, timer=${wd_timer}, gateway=${gw}. If you did not run this yourself, investigate. Context: $*"
  else
    body="Hermes watchdog problem — last watchdog run=${wd_result:-unknown}, timer=${wd_timer:-unknown}, gateway=${gw:-unknown}. Nothing may be monitoring the gateway. Context: $*"
  fi
  log "EXTERNAL ALERT: $body"
  telegram_alert "$body"
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
python3 - "$STATE_FILE" "$main_pid" "$WATCH_PLATFORMS" 2>>"$LOG" <<'PYEOF'
import json, sys, datetime

path, main_pid, watch = sys.argv[1], sys.argv[2], sys.argv[3]
watched = {w.strip().lower() for w in watch.split(",") if w.strip()}

def unknown(msg):
    print("VERDICT UNKNOWN " + msg); sys.exit(0)

def _crash(exc_type, exc, tb):
    # Any uncaught error must still produce a verdict the shell can classify.
    # Previously an AttributeError (e.g. `platforms` arriving as a list) exited
    # non-zero with no stdout, which the shell scored HEALTHY.
    print(f"VERDICT UNKNOWN probe crashed: {exc_type.__name__}: {exc}")
    sys.exit(0)

sys.excepthook = _crash

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
bad, retrying, operator = [], [], []
for name, info in platforms.items():
    if name.lower() not in watched:
        continue
    info = info or {}
    # A missing "state" key is not evidence of health.
    st = info.get("state", "absent")
    # "disabled" is an operator decision (revoked relay credential); "paused"
    # is an explicit `/platform pause`, which freezes updated_at BY DESIGN
    # (next_retry=inf) and would otherwise look permanently stale.
    if st == "connected":
        continue
    # Operator states: not faults. Tracked separately below so they cannot be
    # silent forever.
    if st in ("disabled", "paused"):
        operator.append(f"{name}={st}")
        continue
    # "retrying" = upstream's reconnect watcher owns this. Alert, never restart.
    if st == "retrying":
        retrying.append(f"{name}={st}")
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

if bad:
    print("VERDICT BAD " + "; ".join(bad))
elif retrying:
    print("VERDICT RETRYING " + "; ".join(retrying))
elif operator:
    print("VERDICT OPERATOR " + "; ".join(operator))
else:
    print("VERDICT OK")
PYEOF
)" || verdict=""

# FAIL CLOSED. Anything that leaves us without a parseable verdict — python3
# missing or upgraded, ENOMEM, an AppArmor denial, or an upstream change to the
# state-file shape — previously fell through to the "healthy" branch: cleared
# state, pinged the deadman GREEN, logged nothing, exited 0. That is precisely
# the going-quiet failure this script exists to prevent, and it failed GREEN.
# Upstream already namespaced platform keys in this release line, so schema
# drift is not hypothetical.
verdict_kind="$(printf '%s' "${verdict:-}" | awk '{print $2}')"
case "${verdict_kind:-}" in
  OK|BAD|UNKNOWN|RETRYING|OPERATOR) ;;
  *)
    verdict="VERDICT UNKNOWN probe produced no parseable verdict: $(printf '%s' "${verdict:-<empty>}" | tr '\n' ' ' | cut -c1-160)"
    verdict_kind="UNKNOWN"
    ;;
esac
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

if [ "$verdict_kind" = "RETRYING" ]; then
  # Upstream is actively reconnecting. Never restart — but never go silent
  # either: if it is STILL retrying an hour later, that is worth knowing.
  first_bad="$(read_num first_bad)"
  [ "$first_bad" -eq 0 ] && { first_bad="$now_epoch"; write_num first_bad "$now_epoch"; }
  for_secs=$(( now_epoch - first_bad )); [ "$for_secs" -lt 0 ] && for_secs=0
  log "retrying: $verdict_detail (${for_secs}s) — upstream reconnect watcher owns this, not restarting"
  if [ "$for_secs" -ge "$ALERT_REPEAT_SECONDS" ]; then
    alert_throttled "retrying" "Hermes has been reconnecting for ${for_secs}s: ${verdict_detail}. Not restarting (upstream's reconnect watcher owns retryable failures). Worth a look if this persists."
  fi
  exit 0
fi

if [ "$verdict_kind" = "OPERATOR" ]; then
  # /platform pause|disable is reachable from chat and from agent tool calls.
  # After it, Hermes is deaf and every monitoring surface reads healthy. Do not
  # restart (that would fight a deliberate choice) and do NOT ping the deadman.
  first_bad="$(read_num first_bad)"
  [ "$first_bad" -eq 0 ] && { first_bad="$now_epoch"; write_num first_bad "$now_epoch"; }
  for_secs=$(( now_epoch - first_bad )); [ "$for_secs" -lt 0 ] && for_secs=0
  log "operator-disabled: $verdict_detail (${for_secs}s) — not restarting, deadman NOT pinged"
  if [ "$for_secs" -ge "$ALERT_REPEAT_SECONDS" ]; then
    alert_throttled "operator" "Hermes platform is ${verdict_detail} and has been for ${for_secs}s — you are not reachable there. Resume with /platform resume, or this stays silent indefinitely."
  fi
  exit 0
fi

if [ "$verdict_kind" = "BAD" ]; then
  # Measure OUR OWN observed duration, not the age of the last state write.
  # The reconnect watcher's backoff cap (300s) is shorter than GRACE (600s) and
  # every retry refreshes updated_at, so a genuinely-down-but-retrying platform
  # would never have aged past the grace under the old logic.
  first_bad="$(read_num first_bad)"
  [ "$first_bad" -eq 0 ] && { first_bad="$now_epoch"; write_num first_bad "$now_epoch"; }
  bad_for=$(( now_epoch - first_bad ))
  # An NTP step correction, VM snapshot restore or host migration can move the
  # clock backwards, making this negative — which never reaches GRACE_SECONDS,
  # so the restart is disarmed indefinitely. Re-anchor instead.
  if [ "$bad_for" -lt 0 ]; then
    log "clock moved backwards (bad_for=${bad_for}s); re-anchoring the bad-since marker"
    first_bad="$now_epoch"; write_num first_bad "$now_epoch"; bad_for=0
  fi
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
  # One throttled green line per hour, so the log can distinguish "healthy" from
  # "not running at all". journald has the Starting/Finished pairs, but nobody
  # reads those during an incident.
  hb="$(read_num heartbeat)"
  [ "$hb" -gt "$now_epoch" ] && hb=0
  if [ $(( now_epoch - hb )) -ge 3600 ]; then
    write_num heartbeat "$now_epoch"
    log "ok: $WATCH_PLATFORMS connected (telegram_sockets=$tg_sockets)"
  fi
  if [ -n "$DEADMAN_URL" ] && [ "$DRY_RUN" != "1" ]; then
    curl -fsS -m 10 -o /dev/null "$DEADMAN_URL" 2>/dev/null \
      || log "deadman ping failed (non-fatal)"
  elif [ -z "$DEADMAN_URL" ]; then
    # Refuse to be silently unconfigured. Weekly, not daily: the Mac-side
    # switch (scripts/monitor/vps_deadman.sh, launchd every 15 min) already
    # covers this from genuinely uncorrelated hardware, so it is a known and
    # partially-mitigated gap rather than an incident. It is still only
    # best-effort — the Mac sleeps and changes networks.
    ALERT_REPEAT_SECONDS=604800 \
      alert_throttled "nodeadman" "Hermes watchdog has no DEADMAN_URL set. The Mac dead-man's switch covers this while the Mac is awake and online; a hosted check (healthchecks.io, ~2 min to set up) would cover it always. Set it in /etc/hermes-watchdog.conf."
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
