#!/usr/bin/env bash
#
# Deploy this repo's latest state to the Hermes VPS.
#
# Strategy: git push from Mac → git pull on VPS → reload Hermes.
# This is the UPDATE flow. For one-time setup, see docs/DEPLOY.md.
#
# Safe by design:
#   - Does not modify ~/.hermes/config.yaml on the VPS (that's a one-time manual merge).
#   - Does not modify ~/.hermes/cron/jobs.json (one-time manual append).
#   - Only pushes new code in this repo to the GitHub remote, then pulls on VPS.
#
# Usage:
#   ./scripts/deploy.sh                    # sync-back agent skill edits + push + pull + restart
#   ./scripts/deploy.sh --pull-only        # skip push (use when working from VPS only)
#   ./scripts/deploy.sh --no-restart       # push + pull but don't restart Hermes
#   ./scripts/deploy.sh --no-sync          # skip the agent-skill sync-back step (Step 0)
#
# Step 0 captures agent-authored skill edits from the VPS into the repo (3-way merge)
# before pushing, so the self-improvement loop is never lost and never blocks the pull.

set -euo pipefail

# Load local, gitignored deploy config if present (keeps your real VPS host out of
# version control). See .env.example for the keys deploy reads (VPS_HOST, etc.).
_DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$_DEPLOY_DIR/.env" ] && { set -a; . "$_DEPLOY_DIR/.env"; set +a; }

# ===== Config (override via env or .env) =====
VPS_HOST="${VPS_HOST:-hermes@your-vps-ip}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/your_ssh_key}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/home/hermes/sparsh-hermes-agent}"
HERMES_SERVICE="${HERMES_SERVICE:-hermes-gateway.service}"
# Root SSH target for systemctl (the 'hermes' user has no sudo). Derived from
# VPS_HOST by swapping the user part to root. Override in .env if VPS_HOST is an
# ssh-config alias or otherwise can't be parsed as user@host.
VPS_ROOT_HOST="${VPS_ROOT_HOST:-root@${VPS_HOST#*@}}"

# ===== Origin guard =====
# A deploy pushes this repo's whole history to `origin`, and the VPS pulls from its own
# `origin`. If either one points at the wrong repository (for example a public copy that
# reuses an old name), the push publishes history that was meant to stay private and the
# VPS would deploy someone else's tree. So both ends are checked before anything moves.
#   DEPLOY_REQUIRE_ORIGIN  owner/repo that origin MUST be (lowercase). Empty = no
#                          requirement; only the public-snapshot URL below is refused.
#   DEPLOY_DENY_ORIGIN     owner/repo that origin must NEVER be (the public snapshot).
# Allowed URL forms for the required repo are exactly git@github.com:<owner/repo>(.git)
# and https://github.com/<owner/repo>(.git), any case, optional trailing slash.
DEPLOY_REQUIRE_ORIGIN="${DEPLOY_REQUIRE_ORIGIN-}"  # e.g. your-user/your-private-repo (lowercase)
DEPLOY_DENY_ORIGIN="${DEPLOY_DENY_ORIGIN:-sparshk312/sparsh-hermes-agent}"

_norm_repo_url() {  # lowercase; drop scheme, user@, :port, .git, trailing slashes; ':' -> '/'
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | sed -E 's#^[a-z+]+://##; s#^[^@/]*@##; s#^([^/:]+):[0-9]+/#\1/#; s#:#/#; s#/+$##; s#\.git$##; s#/+$##'
}

_origin_url_ok() {  # $1 = a remote URL; exit 0 if a deploy may use it
  local url lower norm req_re
  url="$1"
  [ -n "$url" ] || return 1
  lower="$(printf '%s' "$url" | tr '[:upper:]' '[:lower:]')"
  norm="$(_norm_repo_url "$url")"
  [ "$norm" = "github.com/$DEPLOY_DENY_ORIGIN" ] && return 1
  if [ -n "$DEPLOY_REQUIRE_ORIGIN" ]; then
    req_re="$(printf '%s' "$DEPLOY_REQUIRE_ORIGIN" | sed 's/[.]/\\./g')"
    printf '%s\n' "$lower" | grep -Eq "^(git@github\.com:|https://github\.com/)${req_re}(\.git)?/?$" \
      || return 1
  fi
  return 0
}

_check_mac_origin() {  # every URL a plain `git push` could use from this checkout
  local fetch_url push_url cur br_remote br_push push_default bad=""
  fetch_url="$(git remote get-url origin 2>/dev/null || true)"
  push_url="$(git remote get-url --push origin 2>/dev/null || true)"
  cur="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  br_remote="$(git config --get "branch.$cur.remote" 2>/dev/null || true)"
  br_push="$(git config --get "branch.$cur.pushRemote" 2>/dev/null || true)"
  push_default="$(git config --get remote.pushDefault 2>/dev/null || true)"
  _origin_url_ok "$fetch_url" || bad="origin url '$fetch_url'"
  if [ -z "$bad" ]; then _origin_url_ok "$push_url" || bad="origin push url '$push_url'"; fi
  if [ -z "$bad" ] && [ -n "$br_remote" ] && [ "$br_remote" != "origin" ]; then bad="branch.$cur.remote=$br_remote"; fi
  if [ -z "$bad" ] && [ -n "$br_push" ] && [ "$br_push" != "origin" ]; then bad="branch.$cur.pushRemote=$br_push"; fi
  if [ -z "$bad" ] && [ -n "$push_default" ] && [ "$push_default" != "origin" ]; then bad="remote.pushDefault=$push_default"; fi
  if [ -n "$bad" ]; then
    echo "  ❌ ORIGIN GUARD: refusing to deploy ($bad)." >&2
    echo "     Required: ${DEPLOY_REQUIRE_ORIGIN:-<any>}; never: $DEPLOY_DENY_ORIGIN." >&2
    return 1
  fi
  return 0
}

# The same check, run ON THE VPS before it fetches (spliced into Step 2 below).
_vps_origin_check_script() {
  local req deny req_re
  req="$DEPLOY_REQUIRE_ORIGIN"; deny="$DEPLOY_DENY_ORIGIN"
  req_re="$(printf '%s' "$req" | sed 's/[.]/\\./g')"
  cat <<VPSCHECK
  _u=\$(git remote get-url origin 2>/dev/null || true)
  _l=\$(printf '%s' "\$_u" | tr '[:upper:]' '[:lower:]')
  _n=\$(printf '%s' "\$_l" | sed -E 's#^[a-z+]+://##; s#^[^@/]*@##; s#^([^/:]+):[0-9]+/#\\1/#; s#:#/#; s#/+\$##; s#\\.git\$##; s#/+\$##')
  if [ -z "\$_u" ] || [ "\$_n" = "github.com/$deny" ]; then
    echo "  ❌ VPS ORIGIN GUARD: origin is '\$_u'; refusing to fetch." >&2; exit 1
  fi
  if [ -n "$req" ] && ! printf '%s\n' "\$_l" | grep -Eq '^(git@github\\.com:|https://github\\.com/)${req_re}(\\.git)?/?\$'; then
    echo "  ❌ VPS ORIGIN GUARD: origin is '\$_u', expected $req; refusing to fetch." >&2; exit 1
  fi
VPSCHECK
}

# ===== Args =====
DO_PUSH=true
DO_RESTART=true
DO_SYNC=true
for arg in "$@"; do
  case "$arg" in
    --pull-only) DO_PUSH=false ;;
    --no-restart) DO_RESTART=false ;;
    --no-sync) DO_SYNC=false ;;
    --check-origin-only)
      # Mac-side guard only (the guard's own tests use this); no push, no ssh.
      cd "$(dirname "$0")/.."
      if _check_mac_origin; then echo "origin ok"; exit 0; fi
      exit 1
      ;;
    --print-vps-origin-check)
      # Print the snippet Step 2 runs on the VPS (the guard's own tests use this).
      _vps_origin_check_script
      exit 0
      ;;
    -h|--help)
      sed -n '3,18p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Pre-flight: refuse before Step 0 commits anything or Step 1 pushes anything.
if [ "$DO_PUSH" = "true" ]; then
  _check_mac_origin || exit 1
fi

echo "→ Repo: $REPO_ROOT"
echo "→ VPS:  $VPS_HOST → $VPS_REPO_PATH"
echo

# ===== Step 0: Capture agent-authored skill edits from the VPS (3-way merge) =====
# The agent edits skills/ directly on the box as it learns; those live only in the VPS
# working tree. Pull them back into the repo FIRST (so they're committed before we push
# and aren't clobbered by the pull). Conflicts abort the deploy for a manual merge — we
# never silently lose either side. Only relevant on a full push (sync needs a push to
# land in canonical); SYNC_RAN gates the safe stash+drop reconcile in Step 2.
SYNC_RAN=false
if [ "$DO_PUSH" = "true" ] && [ "$DO_SYNC" = "true" ]; then
  echo "[0/3] Capturing agent skill edits from the VPS..."
  if "$REPO_ROOT/scripts/sync_agent_skills.sh"; then
    SYNC_RAN=true
  else
    echo "  ✗ sync reported conflicts (or failed). Resolve the .SYNC_CONFLICT file(s),"
    echo "    commit, and re-run deploy. Aborting so no VPS edits are lost."
    exit 1
  fi
  echo
fi

# ===== Step 0b: Board invariants — MUST PASS OR THE DEPLOY ABORTS =====
# Added 2026-09-08. Five defects shipped in one afternoon, every one already covered by a
# prose rule in the vault's CLAUDE.md -- one of those rules written that same morning and
# violated that same afternoon. Prose did not hold; a gate that blocks the deploy does.
# The suite is pure-function, runs in under a second, and is mutation-tested: reintroduce
# any of the five defects and it goes red.
# ⚠️ scripts/internship/test_curated_roundtrip.py is the cautionary tale -- it existed for
# months, was wired into NOTHING, and silently passed for ~8 weeks against an empty store.
# A test nobody runs is not a check, it is a comment.
INV="$REPO_ROOT/scripts/internship/test_board_invariants.py"
if [ -f "$INV" ]; then
  echo "[0b/3] Board invariants..."
  INV_PY="${INV_PY:-$HOME/path/to/vault/.venv/bin/python}"
  [ -x "$INV_PY" ] || INV_PY="$(command -v python3)"
  # 🔴 NOT `cmd | tail`: a pipeline's exit status is the LAST command's, so `| tail`
  # always succeeds and the gate silently passes. That exact mistake was made writing
  # this gate. Capture, then test the real status.
  # ⚠️ 2026-11-11: `OUT="$(cmd)"; RC=$?` under `set -e` exits AT THE ASSIGNMENT when cmd
  # fails, so the deploy did abort, but with the test's output and the message below
  # never printed. `if OUT=...; then` is exempt from set -e and keeps both.
  if INV_OUT="$("$INV_PY" "$INV" 2>&1)"; then INV_RC=0; else INV_RC=$?; fi
  echo "$INV_OUT" | tail -3
  if [ "$INV_RC" -ne 0 ]; then
    echo "  ❌ BOARD INVARIANTS FAILED (exit $INV_RC) — deploy aborted."
    echo "     Fix the defect. Do not skip this; every one of these tests exists because"
    echo "     the defect it covers already shipped once."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi
# Role watcher (fall_watch.py, hot_watch seen-set, careers-site pager, runner exit codes).
# Same gate, same rule: added 2026-11-11, every assertion mutation-tested.
FWT="$REPO_ROOT/scripts/internship/test_fall_watch.py"
if [ -f "$FWT" ]; then
  echo "[0b/3] Fall-watch invariants..."
  FW_PY="${INV_PY:-$HOME/path/to/vault/.venv/bin/python}"
  [ -x "$FW_PY" ] || FW_PY="$(command -v python3)"
  if FWT_OUT="$("$FW_PY" "$FWT" 2>&1)"; then FWT_RC=0; else FWT_RC=$?; fi
  echo "$FWT_OUT" | tail -3
  if [ "$FWT_RC" -ne 0 ]; then
    echo "  ❌ FALL-WATCH INVARIANTS FAILED (exit $FWT_RC) — deploy aborted."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi

# ===== Step 0c: Nudge invariants — MUST PASS OR THE DEPLOY ABORTS =====
# Added 2026-09-25. The nudges were asserting "nothing happened" on days the record showed
# activity, because each was reading a file that
# had quietly stopped being the source of truth. These tests run the real scripts against
# fixture vaults, so they fail on BEHAVIOUR, not wording. Same rule as 0b: a test wired
# into nothing is a comment (see test_curated_roundtrip, green for 8 weeks against an
# empty store).
NUDGE="$REPO_ROOT/scripts/cron/test_nudge_invariants.py"
if [ -f "$NUDGE" ]; then
  echo "[0c/3] Nudge invariants..."
  # 🔴 Capture then test the status. NOT `cmd | tail` — a pipeline's exit status is the
  # last command's, so the gate would always pass. That mistake was made writing Step 0b.
  NUDGE_RC=0; NUDGE_OUT="$(python3 "$NUDGE" 2>&1)" || NUDGE_RC=$?
  echo "$NUDGE_OUT" | tail -3
  if [ "$NUDGE_RC" -ne 0 ]; then
    echo "  ❌ NUDGE INVARIANTS FAILED (exit $NUDGE_RC) — deploy aborted."
    echo "     A nudge is about to tell him nothing happened on a day something did."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi

# ===== Step 0d: Vault log invariants — MUST PASS OR THE DEPLOY ABORTS =====
# Added 2026-10-05 with the one-file-per-day log (log_append.py / log_shard.py). Every
# agent writes the log through log_append.py; a broken writer would silently scatter or
# drop entries on both machines, so it is gated like the board and the nudges.
LOGT="$REPO_ROOT/scripts/vault/test_log_append.py"
if [ -f "$LOGT" ]; then
  echo "[0d/3] Vault log invariants..."
  LOGT_RC=0; LOGT_OUT="$(python3 "$LOGT" 2>&1)" || LOGT_RC=$?
  echo "$LOGT_OUT" | tail -2
  if [ "$LOGT_RC" -ne 0 ]; then
    echo "  ❌ VAULT LOG INVARIANTS FAILED (exit $LOGT_RC) — deploy aborted."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi

# ===== Step 0e: Wearable (Fitbit Air + Apple) merge invariants =====
# 2026-10-08. metrics.csv is now DERIVED from apple.csv + fitbit.csv; a broken merge would
# silently mix the sources (Air HRV read as Apple HRV, Apple data lost on the first run).
WEAR="$REPO_ROOT/scripts/hae/test_wearable_invariants.py"
if [ -f "$WEAR" ]; then
  echo "[0e/3] Wearable merge invariants..."
  WEAR_RC=0; WEAR_OUT="$(python3 "$WEAR" 2>&1)" || WEAR_RC=$?
  echo "$WEAR_OUT" | tail -2
  if [ "$WEAR_RC" -ne 0 ]; then
    echo "  ❌ WEARABLE INVARIANTS FAILED (exit $WEAR_RC) — deploy aborted."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi

# ===== Step 0f: Health cards + coach check-in invariants =====
# 2026-10-17. The morning / weekly / monthly cards and the coach's evidence: a missing day
# drawn as zero, a device change read as progress, or the coach losing its own history
# would all look fine in a message and be wrong.
CARDS_T="$REPO_ROOT/scripts/fitness/test_health_cards.py"
if [ -f "$CARDS_T" ]; then
  echo "[0f/3] Health card + coach invariants..."
  CARDS_RC=0; CARDS_OUT="$(python3 "$CARDS_T" 2>&1)" || CARDS_RC=$?
  echo "$CARDS_OUT" | tail -2
  if [ "$CARDS_RC" -ne 0 ]; then
    echo "  ❌ HEALTH CARD INVARIANTS FAILED (exit $CARDS_RC) — deploy aborted."
    exit 1
  fi
  echo "  ✓ invariants hold"
  echo
fi

# ===== Step 0g: Health logging (vault_log / pantry) + exercise playbook =====
# 2026-11-16. The pytest suite holds the tests for the health WRITE path (pantry units,
# frontmatter quoting, vitamins merge, workout drop-guard, remove-meal). It ran only in
# CI, i.e. AFTER a deploy had already shipped. test_exercise_playbook.py ran nowhere and
# sat red for weeks. Both gate the deploy now. pytest + PyYAML live in .venv-tests
# (gitignored); a missing venv ABORTS, because a gate that skips itself is not a gate:
#   python3 -m venv .venv-tests && .venv-tests/bin/pip install pytest pyyaml
PLAYBOOK_T="$REPO_ROOT/scripts/fitness/test_exercise_playbook.py"
TEST_PY="$REPO_ROOT/.venv-tests/bin/python"
echo "[0g/3] Health logging tests + exercise playbook..."
PB_RC=0; PB_OUT="$(python3 "$PLAYBOOK_T" 2>&1)" || PB_RC=$?
echo "$PB_OUT" | tail -6
if [ "$PB_RC" -ne 0 ]; then
  echo "  ❌ EXERCISE PLAYBOOK TEST FAILED (exit $PB_RC) — deploy aborted."
  exit 1
fi
if [ ! -x "$TEST_PY" ]; then
  echo "  ❌ $TEST_PY is missing — deploy aborted. Create it:"
  echo "     python3 -m venv .venv-tests && .venv-tests/bin/pip install pytest pyyaml"
  exit 1
fi
PT_RC=0; PT_OUT="$(cd "$REPO_ROOT" && "$TEST_PY" -m pytest tests -q 2>&1)" || PT_RC=$?
echo "$PT_OUT" | tail -12
if [ "$PT_RC" -ne 0 ]; then
  echo "  ❌ PYTEST SUITE FAILED (exit $PT_RC) — deploy aborted."
  exit 1
fi
echo "  ✓ tests pass"
echo

# ===== Step 1: Push (Mac → GitHub) =====
if [ "$DO_PUSH" = "true" ]; then
  echo "[1/3] Pushing local changes to GitHub..."
  if [ -n "$(git status --porcelain)" ]; then
    echo "  ⚠ Working tree has uncommitted changes. Commit first:"
    git status --short
    exit 1
  fi
  git push
  echo "  ✓ pushed"
else
  echo "[1/3] Skipped (--pull-only)"
fi
echo

# ===== Step 2: Pull (GitHub → VPS) =====
echo "[2/3] Pulling on VPS..."
ssh -i "$VPS_SSH_KEY" "$VPS_HOST" "
  set -euo pipefail
  cd '$VPS_REPO_PATH'
  echo \"  cwd: \$(pwd)\"
$(_vps_origin_check_script)
  git fetch --all --quiet
  if ! git merge-base --is-ancestor HEAD origin/main; then
    echo '  ❌ VPS HEAD is not an ancestor of origin/main (unrelated or diverged history); refusing to pull.' >&2
    exit 1
  fi
  before=\$(git rev-parse --short HEAD)
  # If Step 0 captured the agent's skill edits into canonical, the VPS working tree is
  # now redundant — stash + drop it so the ff-only pull isn't blocked (nothing is lost;
  # it's already committed upstream). Without a sync run we DON'T discard (would lose
  # uncommitted edits) — a dirty tree then surfaces as a normal pull error instead.
  if [ \"$SYNC_RAN\" = \"true\" ] && [ -n \"\$(git status --porcelain -- skills/)\" ]; then
    # Pathspec-scoped: Step 0 only inspects skills/, so stashing the whole tree
    # would discard un-captured work elsewhere (that is how the Abs muscle-tracking
    # edits and the IMAP timeout fix became orphans in ~/.hermes/scripts/).
    git stash push -u -m \"deploy-redundant-after-sync \$(date +%Y-%m-%d)\" -- skills/ >/dev/null 2>&1 \\
      && echo '  (stashed VPS skill edits — already captured upstream)'
    git pull --ff-only
    # Deliberately NOT dropped. The old 'git stash drop || true' dropped stash@{0}
    # unconditionally — and if the push above stashed nothing, that silently ate a
    # REAL earlier stash (this box carries two, from 2026-05-27 and 2026-06-10).
    # A retained stash costs nothing and is the only recovery path.
    echo '  (stash retained — inspect with: git stash list)'
  else
    git pull --ff-only
  fi
  after=\$(git rev-parse --short HEAD)
  if [ \"\$before\" = \"\$after\" ]; then
    echo \"  → no new commits (HEAD: \$after)\"
  else
    echo \"  → updated \$before → \$after\"
    git log --oneline \$before..\$after
  fi
  # Ensure cron scripts are executable in the repo
  chmod +x scripts/cron/*.sh 2>/dev/null || true
  # Mirror cron scripts into Hermes' allowed scripts root.
  # Hermes' cron dispatcher refuses to run scripts outside ~/.hermes/scripts/
  # (security sandbox: 'resolved script path is outside the allowed scripts
  # directory'). The repo lives outside that root, so we copy each invocation
  # in. The on-VPS jobs.json points at these mirrored paths, not the repo path.
  # Naming convention: prefix 'health_' so they don't collide with bundled
  # Hermes scripts (e.g. internship_scraper.py).
  mkdir -p ~/.hermes/scripts
  rm -f ~/.hermes/scripts/health_morning_weigh_in_nudge.sh ~/.hermes/scripts/trends_report.sh   # retired 2026-11-15 (no cron, no skill)
  cp scripts/cron/lunch_check.sh           ~/.hermes/scripts/health_lunch_check.sh
  cp scripts/cron/evening_summary.sh       ~/.hermes/scripts/health_evening_summary.sh
  cp scripts/cron/hae_sync.sh              ~/.hermes/scripts/health_hae_sync.sh
  cp scripts/cron/prep_nudge.sh            ~/.hermes/scripts/prep_nudge.sh
  chmod +x ~/.hermes/scripts/health_*.sh ~/.hermes/scripts/prep_nudge.sh
  # The framework's own interpreter. Was hard-coded in two places; from v0.22 the package
  # manager can move the venv, so fall back to the shebang of the hermes launcher and
  # fail loudly if neither exists (2026-11-16, upgrade pre-work).
  HPY=/home/hermes/.hermes/hermes-agent/venv/bin/python3
  if [ ! -x \"\$HPY\" ]; then
    HPY=\$(head -1 \"\$(command -v hermes || echo /home/hermes/.local/bin/hermes)\" 2>/dev/null | sed 's/^#!//')
  fi
  [ -x \"\$HPY\" ] || { echo '  ❌ cannot find the Hermes interpreter (venv moved?) — deploy aborted'; exit 1; }
  # Hermes-only vault context (2026-10-05). Hermes loads the FIRST of .hermes.md >
  # AGENTS.md > CLAUDE.md from its cwd (/home/hermes/vault); until now that was the Mac's
  # 56 KB CLAUDE.md — ~63% of every Hermes prompt, mostly Mac-only and stale. A dotfile, so
  # Obsidian Sync never touches it. Gated: a flagged file would load as [BLOCKED].
  cp vault-context/.hermes.md /home/hermes/vault/.hermes.md
  \"\$HPY\" scripts/vault/check_context_file.py /home/hermes/vault/.hermes.md
  # HAE wearable-bridge python (Phase 3): the always-on listener (run by the
  # hae-ingest.service systemd unit) + the raw->CSV processor + the CSV->daily-
  # note-frontmatter ingester (both run by the hae-sync cron). Mirrored here so
  # the deployed copies stay in lockstep with the repo.
  cp scripts/hae/hae_ingest.py             ~/.hermes/scripts/hae_ingest.py
  cp scripts/hae/hae_process.py            ~/.hermes/scripts/hae_process.py
  cp scripts/hae/hae_daily_ingest.py       ~/.hermes/scripts/hae_daily_ingest.py
  cp scripts/hae/health_morning_brief_gate.py ~/.hermes/scripts/health_morning_brief_gate.py
  # Fitbit Air (2026-10-08): flat beside hae_process.py (they import each other; a
  # sub-package would not import here). Credentials live in ~/.hermes/google_health/,
  # never under scripts/ (this step overwrites scripts).
  cp scripts/hae/google_health.py         ~/.hermes/scripts/google_health.py
  cp scripts/hae/wearable_merge.py        ~/.hermes/scripts/wearable_merge.py
  cp scripts/hae/fitbit_sync.py           ~/.hermes/scripts/fitbit_sync.py
  cp scripts/cron/fitbit_sync.sh          ~/.hermes/scripts/fitbit_sync.sh
  chmod +x ~/.hermes/scripts/fitbit_sync.sh
  chmod +x ~/.hermes/scripts/health_morning_brief_gate.py

  # Mirror the fitness package (muscle-coverage card + report engine + body-SVG
  # asset) as a subdir, and the cron wrapper. fitness_report.py runs under the
  # dedicated ~/.hermes/venvs/fitness venv (cairosvg); the weekly cron calls the .sh.
  rm -rf ~/.hermes/scripts/fitness && cp -r scripts/fitness ~/.hermes/scripts/fitness
  # The deterministic vault writer the log-* skills call (food/water/weight/sleep/
  # vitamins). Invoked as 'python3 .../vault/vault_log.py <cmd> <flags>' — matches no
  # dangerous-command pattern, so it never trips the approval gate and runs in cron.
  rm -rf ~/.hermes/scripts/vault && cp -r scripts/vault ~/.hermes/scripts/vault
  # The assistant core (2026-11-16): the shared send ledger + nudge budget (outbox.py) and
  # the spend-guarded model client (llm.py). coach_engine and the morning brief import it
  # from here; without this line every nudge goes silent ("outbox unavailable").
  rm -rf ~/.hermes/scripts/assistant && cp -r scripts/assistant ~/.hermes/scripts/assistant
  mkdir -p ~/.hermes/assistant
  cp scripts/cron/loop_extract.sh ~/.hermes/scripts/loop_extract.sh && chmod +x ~/.hermes/scripts/loop_extract.sh
  cp scripts/cron/assistant_tick.sh ~/.hermes/scripts/assistant_tick.sh && chmod +x ~/.hermes/scripts/assistant_tick.sh
  # Cost monitor (Anthropic spend guardrail). Pure stdlib python + cron wrapper;
  # reads state.db, Telegram-alerts on daily/MTD spend thresholds. No venv, no agent.
  # The watchdog is NOT deployed by this path — it lives in /usr/local/bin and is
  # installed by scripts/monitor/install_watchdog.sh. Copying it here would plant a
  # never-executed decoy for someone to debug at 2am. Exclude it explicitly.
  rm -rf ~/.hermes/scripts/monitor && mkdir -p ~/.hermes/scripts/monitor
  # ALLOWLIST, not a denylist. The previous exclusion listed the watchdog files
  # by name; db_snapshot.py was added to this directory a day later, was not
  # excluded, and became exactly the "never-executed decoy" the comment warns
  # about. Anything that runs from /usr/local/bin must NOT be mirrored here.
  for _f in cost_monitor.py sync_watchdog.py upstream_watch.py; do
    [ -f "scripts/monitor/\$_f" ] && cp "scripts/monitor/\$_f" ~/.hermes/scripts/monitor/
  done
  cp scripts/cron/cost_monitor.sh          ~/.hermes/scripts/cost_monitor.sh
  cp scripts/cron/upstream_watch.sh ~/.hermes/scripts/upstream_watch.sh && chmod +x ~/.hermes/scripts/upstream_watch.sh
  # daily-note-prefill is a --no-agent script since 2026-11-16 (scripts/vault/daily_prefill.py).
  cp scripts/cron/daily_prefill.sh         ~/.hermes/scripts/daily_prefill.sh
  chmod +x ~/.hermes/scripts/daily_prefill.sh
  chmod +x ~/.hermes/scripts/cost_monitor.sh
  # Ops+health dashboard (Phase 1). Pure-stdlib read-only web page served over the
  # tailnet by hermes-dashboard.service; opens state.db mode=ro, serves vault charts.
  rm -rf ~/.hermes/scripts/dashboard && cp -r scripts/dashboard ~/.hermes/scripts/dashboard
  # The Curated Board engine (scraper + sources + brand-first harvest + fit pass).
  # This is the LIVE internship system — do not confuse it with the retired
  # frontier-triage watcher below.
  rm -rf ~/.hermes/scripts/internship && cp -r scripts/internship ~/.hermes/scripts/internship
  # The two board cron wrappers are invoked by hermes cron as ~/.hermes/scripts/<name>,
  # one level ABOVE the package. Without these lines the repo copies were never-executed
  # decoys and cron kept running whatever had last been scp'd by hand (found 2026-09-05).
  cp scripts/internship/run_curate_vps.sh ~/.hermes/scripts/run_curate_vps.sh
  cp scripts/internship/run_hot_watch.sh  ~/.hermes/scripts/run_hot_watch.sh
  cp scripts/internship/run_fall_watch.sh ~/.hermes/scripts/run_fall_watch.sh
  cp scripts/internship/run_revive_dead.sh ~/.hermes/scripts/run_revive_dead.sh
  # Weekly coverage digest (2026-09-12): what the tier table hid this week.
  cp scripts/internship/run_coverage_digest.sh ~/.hermes/scripts/run_coverage_digest.sh
  chmod +x ~/.hermes/scripts/run_curate_vps.sh ~/.hermes/scripts/run_hot_watch.sh ~/.hermes/scripts/run_fall_watch.sh ~/.hermes/scripts/run_revive_dead.sh ~/.hermes/scripts/run_coverage_digest.sh
  # Morning email triage (2026-10-04). Until today NOTHING copied scripts/email/: the
  # VPS ran a hand-placed Sep-4 copy, so the fix to its 60-row My Applications cap
  # (commit 12e183e) deployed and changed nothing. Same "never-executed decoy" shape
  # as the 2026-09-05 wrappers above. The wrapper is now in the repo too.
  mkdir -p ~/.hermes/scripts/email
  cp scripts/email/email_triage.py scripts/email/email_audit.py ~/.hermes/scripts/email/
  chmod +x ~/.hermes/scripts/email/email_triage.py
  cp scripts/cron/email_triage.sh ~/.hermes/scripts/email_triage.sh
  chmod +x ~/.hermes/scripts/email_triage.sh
  # Vault-sync watchdog (2026-10-05): pending uploads + error rate + Mac heartbeat.
  cp scripts/cron/sync_watchdog.sh ~/.hermes/scripts/sync_watchdog.sh
  chmod +x ~/.hermes/scripts/sync_watchdog.sh
  # Hourly git snapshot of the vault (2026-10-05): history outside Obsidian Sync's quota.
  cp scripts/cron/vault_git_snapshot.sh ~/.hermes/scripts/vault_git_snapshot.sh
  chmod +x ~/.hermes/scripts/vault_git_snapshot.sh
  # RETIRED 2026-08-26: internship_watch.sh -> internship_triage.py, the legacy
  # frontier watcher. Its cron job (internship-watcher) was disabled and last ran
  # 2026-06-21; the job record is removed and both files are deleted. Nothing
  # imports internship_triage — the remaining mentions in fit_pass.py,
  # applicant_profile.py and internship_scraper.py are comments only (verified).
  # It was also one of the OpenRouter callers, so retiring it removes one script
  # from that migration rather than porting a dead one.
  cp scripts/cron/fitness_report.sh        ~/.hermes/scripts/fitness_report.sh
  cp scripts/cron/coach.sh                 ~/.hermes/scripts/coach.sh
  cp scripts/cron/coach_meal.sh            ~/.hermes/scripts/coach_meal.sh
  cp scripts/cron/coach_workout.sh         ~/.hermes/scripts/coach_workout.sh
  # New gated check-ins (water / dinner / internship accountability) — coach.py --mode wrappers.
  cp scripts/cron/coach_water.sh           ~/.hermes/scripts/coach_water.sh
  cp scripts/cron/coach_dinner.sh          ~/.hermes/scripts/coach_dinner.sh
  cp scripts/cron/coach_dark.sh            ~/.hermes/scripts/coach_dark.sh && chmod +x ~/.hermes/scripts/coach_dark.sh
  cp scripts/cron/coach_internship.sh      ~/.hermes/scripts/coach_internship.sh
  cp scripts/cron/coach_refresh.sh         ~/.hermes/scripts/coach_refresh.sh
  # Nutrition visuals (daily fuel card + weekly Week-in-Food report) — nutrition_card.py wrappers.
  cp scripts/cron/fuel_card.sh             ~/.hermes/scripts/fuel_card.sh
  cp scripts/cron/week_food.sh             ~/.hermes/scripts/week_food.sh
  cp scripts/cron/health_monthly.sh        ~/.hermes/scripts/health_monthly.sh
  cp scripts/cron/health_cards.sh          ~/.hermes/scripts/health_cards.sh
  chmod +x ~/.hermes/scripts/fitness_report.sh ~/.hermes/scripts/coach.sh ~/.hermes/scripts/coach_meal.sh ~/.hermes/scripts/coach_workout.sh ~/.hermes/scripts/coach_water.sh ~/.hermes/scripts/coach_dinner.sh ~/.hermes/scripts/coach_internship.sh ~/.hermes/scripts/coach_refresh.sh ~/.hermes/scripts/fuel_card.sh ~/.hermes/scripts/week_food.sh ~/.hermes/scripts/health_monthly.sh ~/.hermes/scripts/health_cards.sh
  # User plugins (auto-loaded via plugins.enabled in config.yaml).
  mkdir -p ~/.hermes/plugins
  rm -rf ~/.hermes/plugins/intent-router   # retired 2026-11-15: off since Aug 26; history is in git
  rm -rf ~/.hermes/plugins/turn-context     && cp -r plugins/turn-context     ~/.hermes/plugins/turn-context        # inject today's date every turn
  rm -rf ~/.hermes/plugins/vault-write-guard && cp -r plugins/vault-write-guard ~/.hermes/plugins/vault-write-guard  # force health logging through vault_log
  rm -rf ~/.hermes/plugins/assistant-context && cp -r plugins/assistant-context ~/.hermes/plugins/assistant-context   # follow-up continuity + 'he wrote' stamp
  rm -rf ~/.hermes/plugins/spend-guard && cp -r plugins/spend-guard ~/.hermes/plugins/spend-guard   # hard daily/monthly ceiling on chat spend
  # --- Observations README before the fail-fast patches ---------------------
  # Deployed BEFORE the fail-fast source patches below: the patchers exit
  # non-zero (under `set -e`, aborting the rest of this block) if upstream Hermes
  # source has drifted, so anything that must ship reliably goes first.
  # (SOUL.md ships separately via scp in Step 2b — it's gitignored, so it is NOT
  # in this pulled repo copy.)

  # Ensure the agent's self-observation directory exists with its README.
  # This is the safe write-target for agent-captured learnings (per SOUL's
  # 'Write scope' rule). The README explains the contract to the agent on
  # every read. Idempotent: re-runs each deploy so guidance updates flow.
  echo
  echo '  ensuring ~/.hermes/memories/observations/ exists...'
  mkdir -p ~/.hermes/memories/observations
  cp config/observations-README.md ~/.hermes/memories/observations/README.md
  echo \"  → observations dir ready (README.md \$(wc -c < ~/.hermes/memories/observations/README.md) bytes)\"

  # NOTE (2026-07-18, v0.18.2 upgrade): the voice-wrapper patch is RETIRED —
  # upstream fixed the underlying bug (gateway/run.py now emits the transcript
  # de-wrapped), so the patcher's anchor no longer exists and running it would
  # fail-fast and abort this deploy. scripts/patch/voice_wrapper_patch.py deleted.
  # Apply the image-routing patch to Hermes' agent/image_routing.py. Drops
  # the 'What do you see in this image?' auto-prompt that fires when a photo
  # has no caption — that prompt biases the LLM into describe-image mode and
  # makes our log-food skill get bypassed (meal photos go to ## Notes instead
  # of updating frontmatter macros). Same idempotent / fail-fast contract.
  echo
  echo '  applying image-routing patch to Hermes...'
  \"\$HPY\" scripts/patch/image_routing_patch.py
  # The Gmail body patch (recursive MIME walk + HTML->text) was hand-applied on Aug 29 and
  # wired into nothing, so a framework upgrade or skill refresh would have reverted it
  # silently. Idempotent: prints 'already patched' and exits 0 when it is in place.
  echo '  applying gmail body patch to the google-workspace skill...'
  /usr/bin/python3 scripts/patch/gmail_body_patch.py
"
echo

# ===== Step 2b: Deploy SOUL.md (gitignored → ships via scp, not git) =====
# The personalized SOUL.md is kept out of the public repo, so it cannot ride the
# git pull. scp the local copy straight to the VPS, backing up the current one
# first. Falls back to the committed generic config/SOUL.public.md if the
# personal SOUL.md isn't present on this machine (so a fresh clone still deploys
# *something* valid rather than nothing).
SOUL_SRC="config/SOUL.md"
[ -f "$SOUL_SRC" ] || SOUL_SRC="config/SOUL.public.md"
echo "[2b/3] Deploying SOUL ($SOUL_SRC) to VPS..."
ssh -i "$VPS_SSH_KEY" "$VPS_HOST" '[ -f ~/.hermes/SOUL.md ] && cp ~/.hermes/SOUL.md ~/.hermes/SOUL.md.bak || true'
scp -i "$VPS_SSH_KEY" "$SOUL_SRC" "$VPS_HOST:.hermes/SOUL.md"
echo "  ✓ SOUL deployed (rollback: cp ~/.hermes/SOUL.md.bak ~/.hermes/SOUL.md)"

# fit_rubric.md is gitignored (personal) but REQUIRED at runtime:
# fit_pass.py loads it from its own directory, and Step 2 above just rm -rf'd that
# directory. It can never arrive via git pull, so it ships here like SOUL.md does.
if [ -f "scripts/internship/fit_rubric.md" ]; then
  scp -q -i "$VPS_SSH_KEY" "scripts/internship/fit_rubric.md" \
    "$VPS_HOST:.hermes/scripts/internship/fit_rubric.md"
  echo "  ✓ fit_rubric.md shipped (gitignored — cannot ride the pull)"
else
  echo "  ⚠ scripts/internship/fit_rubric.md missing locally; fit_pass will fall back"
fi
echo

# ===== Step 3: Restart Hermes =====
if [ "$DO_RESTART" = "true" ]; then
  echo "[3/3] Restarting $HERMES_SERVICE..."
  # The 'hermes' user does NOT have sudo per the runbook. Use root SSH for systemctl.
  ssh -i "$VPS_SSH_KEY" "$VPS_ROOT_HOST" "
    set -euo pipefail
    systemctl restart $HERMES_SERVICE
    sleep 2
    systemctl is-active $HERMES_SERVICE
    # Restart the HAE ingest listener too (deploy may have updated hae_ingest.py).
    # Tolerate absence on hosts where Phase 3 isn't installed.
    if systemctl list-unit-files hae-ingest.service >/dev/null 2>&1; then
      systemctl restart hae-ingest.service && systemctl is-active hae-ingest.service
    fi
    # Same for the dashboard: Step 2 rm -rf's ~/.hermes/scripts/dashboard and copies a
    # new serve.py in, but the running unit kept executing the old code until someone
    # restarted it by hand. Mirroring without restarting is a no-op.
    if systemctl list-unit-files hermes-dashboard.service >/dev/null 2>&1; then
      systemctl restart hermes-dashboard.service && systemctl is-active hermes-dashboard.service
    fi
  "
  echo "  ✓ restarted"
else
  echo "[3/3] Skipped (--no-restart)"
fi

echo
echo "Done."
