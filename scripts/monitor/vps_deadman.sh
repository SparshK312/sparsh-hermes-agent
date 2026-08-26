#!/usr/bin/env bash
#
# vps_deadman.sh — the OUTSIDE observer. Runs on the Mac, watches the VPS.
#
# WHY THIS EXISTS
# ---------------
# Everything else that monitors Hermes runs ON the box it is monitoring, so it
# structurally cannot report: the box being dead, the network being gone, the
# watchdog itself being dead, or systemd never firing the timer. The watchdog's
# own alert path is worse than that — it notifies by calling api.telegram.org
# from the VPS, which is correlated with the very failures it reports.
#
# This is the uncorrelated channel. It runs on different hardware, on a
# different network, and alerts through a path that does not touch the VPS at
# all. The usual answer here is a hosted dead-man's switch (healthchecks.io);
# this needs no account and no third party.
#
# LIMITATION, STATED HONESTLY: the Mac sleeps and moves networks, so this is
# best-effort, not a guarantee. A missed check is not evidence of health — it
# is just no evidence. It is strictly better than nothing and strictly worse
# than a hosted switch that is always awake.
#
# Managed by https://github.com/SparshK312/sparsh-hermes-agent
set -uo pipefail

VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/hetzner_hermes}"
VPS_HOST="${VPS_HOST:-hermes@94.130.176.108}"
ENV_FILE="${ENV_FILE:-$HOME/.hermes/.env}"
STATE_DIR="${STATE_DIR:-$HOME/.hermes/deadman}"
LOG="${LOG:-$HOME/.hermes/logs/vps-deadman.log}"
# Two consecutive misses before alerting: one transient wifi drop on the Mac
# must not page him about a healthy VPS.
FAIL_THRESHOLD="${FAIL_THRESHOLD:-2}"
ALERT_REPEAT_SECONDS="${ALERT_REPEAT_SECONDS:-10800}"   # 3h between repeats
DRY_RUN="${DRY_RUN:-0}"

[ "$DRY_RUN" = "1" ] && LOG=/dev/stdout
mkdir -p "$STATE_DIR" 2>/dev/null
[ "$DRY_RUN" = "1" ] || mkdir -p "$(dirname "$LOG")" 2>/dev/null
log() {
  local line; line="$(date '+%Y-%m-%d %H:%M:%S%z') $*"
  # Under DRY_RUN, LOG is /dev/stdout — tee would then emit every line twice.
  if [ "$DRY_RUN" = "1" ]; then printf '%s\n' "$line"
  else printf '%s\n' "$line" | tee -a "$LOG" >&2; fi
}

read_num() {
  local v=""; [ -f "$STATE_DIR/$1" ] && v="$(cat "$STATE_DIR/$1" 2>/dev/null)"
  case "$v" in ''|*[!0-9]*) echo 0 ;; ?????????????*) echo 0 ;; *) echo "$v" ;; esac
}
# DRY_RUN suppresses OUTBOUND ALERTS, not bookkeeping. Blocking state writes
# made the failure ladder untestable — every dry run reported "FAIL 1/2"
# forever and the recovery path could never be exercised. State is scoped by
# STATE_DIR, so a test points that at a temp dir; DRY_RUN also sends the log to
# stdout so it cannot pollute the real one.
write_num() {
  printf '%s\n' "${2:-0}" >"$STATE_DIR/.$1.tmp" 2>/dev/null && mv -f "$STATE_DIR/.$1.tmp" "$STATE_DIR/$1" 2>/dev/null
}

notify() {
  local msg="$1"
  # Channel 1: macOS notification — fully local, works with no network at all.
  osascript -e "display notification \"${msg//\"/}\" with title \"Hermes VPS\" sound name \"Basso\"" 2>/dev/null || true
  # Channel 2: Telegram FROM THE MAC. Independent of the VPS entirely, so it
  # still lands when the VPS is the thing that is down.
  local tok chat
  tok="$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
        | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/')"
  chat="$(grep -m1 '^TELEGRAM_HOME_CHANNEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
         | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/')"
  [ -n "$tok" ] && [ -n "$chat" ] || { log "notify: no token/channel in $ENV_FILE"; return 1; }
  curl -sS -m 20 -o /dev/null --config - <<CURLCFG
url = "https://api.telegram.org/bot${tok}/sendMessage"
data-urlencode = "chat_id=${chat}"
data-urlencode = "text=${msg}"
CURLCFG
}

# --- Probe --------------------------------------------------------------------
# One SSH round trip that answers all three questions at once. BatchMode so a
# key problem fails fast instead of hanging on a password prompt.
probe="$(ssh -i "$VPS_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 \
             -o StrictHostKeyChecking=accept-new "$VPS_HOST" '
  gw=$(systemctl is-active hermes-gateway 2>/dev/null)
  tg=$(python3 -c "
import json
try:
    d=json.load(open(\"/home/hermes/.hermes/gateway_state.json\"))
    print(d[\"platforms\"][\"telegram\"][\"state\"])
except Exception as e:
    print(\"unreadable\")
" 2>/dev/null)
  wd=$(systemctl is-active hermes-watchdog.timer 2>/dev/null)
  echo "gw=$gw tg=$tg wd=$wd"
' 2>/dev/null)"
rc=$?

healthy=0
case "$probe" in
  "gw=active tg=connected wd=active") healthy=1 ;;
esac

fails="$(read_num fails)"
if [ "$healthy" = "1" ]; then
  if [ "$fails" -ne 0 ]; then
    log "RECOVERED after $fails failed check(s): $probe"
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN would alert: recovered after $fails failed check(s)"
    else
      notify "Hermes VPS is reachable and healthy again (was failing $fails check(s))."
    fi
  fi
  write_num fails 0
  # Heartbeat once a day so this log can distinguish healthy from not-running.
  hb="$(read_num heartbeat)"; now="$(date +%s)"
  [ "$hb" -gt "$now" ] && hb=0
  if [ $(( now - hb )) -ge 86400 ]; then write_num heartbeat "$now"; log "ok: $probe"; fi
  exit 0
fi

fails=$(( fails + 1 ))
write_num fails "$fails"
detail="${probe:-<no response, ssh rc=$rc>}"
log "FAIL $fails/$FAIL_THRESHOLD: $detail"

[ "$fails" -lt "$FAIL_THRESHOLD" ] && exit 0

now="$(date +%s)"; last="$(read_num last_alert)"
[ "$last" -gt "$now" ] && last=0
if [ $(( now - last )) -lt "$ALERT_REPEAT_SECONDS" ]; then
  log "alert suppressed (last was $(( (now - last) / 60 ))m ago)"
  exit 0
fi
write_num last_alert "$now"

msg="Hermes VPS is not answering. $fails consecutive checks failed from the Mac. Last probe: ${detail}. The VPS cannot tell you this itself — check: ssh -i ~/.ssh/hetzner_hermes root@94.130.176.108 'systemctl status hermes-gateway'"
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN would alert: $msg"
else
  notify "$msg" && log "alerted" || log "ALERT DELIVERY FAILED"
fi
exit 0
