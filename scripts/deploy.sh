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
#   ./scripts/deploy.sh                    # push + pull + restart
#   ./scripts/deploy.sh --pull-only        # skip push (use when working from VPS only)
#   ./scripts/deploy.sh --no-restart       # push + pull but don't restart Hermes

set -euo pipefail

# Load local, gitignored deploy config if present (keeps your real VPS host out of
# version control). See .env.example for the keys deploy reads (VPS_HOST, etc.).
_DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$_DEPLOY_DIR/.env" ] && { set -a; . "$_DEPLOY_DIR/.env"; set +a; }

# ===== Config (override via env or .env) =====
VPS_HOST="${VPS_HOST:-hermes@your-vps-ip}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/hetzner_hermes}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/home/hermes/sparsh-hermes-agent}"
HERMES_SERVICE="${HERMES_SERVICE:-hermes-gateway.service}"

# ===== Args =====
DO_PUSH=true
DO_RESTART=true
for arg in "$@"; do
  case "$arg" in
    --pull-only) DO_PUSH=false ;;
    --no-restart) DO_RESTART=false ;;
    -h|--help)
      sed -n '3,18p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "→ Repo: $REPO_ROOT"
echo "→ VPS:  $VPS_HOST → $VPS_REPO_PATH"
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
  echo '  cwd: $(pwd)'
  git fetch --all --quiet
  before=\$(git rev-parse --short HEAD)
  git pull --ff-only
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
  cp scripts/cron/morning_weigh_in_nudge.sh ~/.hermes/scripts/health_morning_weigh_in_nudge.sh
  cp scripts/cron/lunch_check.sh           ~/.hermes/scripts/health_lunch_check.sh
  cp scripts/cron/evening_summary.sh       ~/.hermes/scripts/health_evening_summary.sh
  cp scripts/cron/hae_sync.sh              ~/.hermes/scripts/health_hae_sync.sh
  chmod +x ~/.hermes/scripts/health_*.sh
  # HAE wearable-bridge python (Phase 3): the always-on listener (run by the
  # hae-ingest.service systemd unit) + the raw->CSV processor + the CSV->daily-
  # note-frontmatter ingester (both run by the hae-sync cron). Mirrored here so
  # the deployed copies stay in lockstep with the repo.
  cp scripts/hae/hae_ingest.py             ~/.hermes/scripts/hae_ingest.py
  cp scripts/hae/hae_process.py            ~/.hermes/scripts/hae_process.py
  cp scripts/hae/hae_daily_ingest.py       ~/.hermes/scripts/hae_daily_ingest.py
  cp scripts/hae/health_morning_brief_gate.py ~/.hermes/scripts/health_morning_brief_gate.py
  chmod +x ~/.hermes/scripts/health_morning_brief_gate.py

  # Mirror the fitness package (muscle-coverage card + report engine + body-SVG
  # asset) as a subdir, and the cron wrapper. fitness_report.py runs under the
  # dedicated ~/.hermes/venvs/fitness venv (cairosvg); the weekly cron calls the .sh.
  rm -rf ~/.hermes/scripts/fitness && cp -r scripts/fitness ~/.hermes/scripts/fitness
  cp scripts/cron/fitness_report.sh        ~/.hermes/scripts/fitness_report.sh
  chmod +x ~/.hermes/scripts/fitness_report.sh
  # Apply the voice-wrapper patch to Hermes' gateway/run.py. Strips the
  # [The user sent a voice message~ ...] wrapper that breaks openai-codex
  # voice handling. Idempotent — safe to call after every deploy. If the
  # upstream source drifts and the patcher can't find the expected block,
  # this exits non-zero and the deploy fails fast (so we know to manually
  # re-verify before voice silently breaks).
  echo
  echo '  applying voice-wrapper patch to Hermes...'
  /home/hermes/.hermes/hermes-agent/venv/bin/python3 scripts/patch/voice_wrapper_patch.py
  # Apply the image-routing patch to Hermes' agent/image_routing.py. Drops
  # the 'What do you see in this image?' auto-prompt that fires when a photo
  # has no caption — that prompt biases the LLM into describe-image mode and
  # makes our log-food skill get bypassed (meal photos go to ## Notes instead
  # of updating frontmatter macros). Same idempotent / fail-fast contract.
  echo
  echo '  applying image-routing patch to Hermes...'
  /home/hermes/.hermes/hermes-agent/venv/bin/python3 scripts/patch/image_routing_patch.py
  # Deploy SOUL.md (Hermes system prompt) from repo → ~/.hermes/SOUL.md.
  # SOUL is loaded fresh every Telegram message — no restart needed, but it
  # is load-bearing: every routing decision the agent makes runs through it.
  # Keep one rolling backup at ~/.hermes/SOUL.md.bak so a bad deploy can be
  # rolled back with: cp ~/.hermes/SOUL.md.bak ~/.hermes/SOUL.md
  echo
  echo '  deploying SOUL.md to ~/.hermes/...'
  if [ -f ~/.hermes/SOUL.md ]; then
    cp ~/.hermes/SOUL.md ~/.hermes/SOUL.md.bak
  fi
  cp config/SOUL.md ~/.hermes/SOUL.md
  echo \"  → SOUL.md \$(wc -c < ~/.hermes/SOUL.md) bytes\"
  # Ensure the agent's self-observation directory exists with its README.
  # This is the safe write-target for agent-captured learnings (per SOUL's
  # 'Write scope' rule). The README explains the contract to the agent on
  # every read. Idempotent: re-runs each deploy so guidance updates flow.
  echo
  echo '  ensuring ~/.hermes/memories/observations/ exists...'
  mkdir -p ~/.hermes/memories/observations
  cp config/observations-README.md ~/.hermes/memories/observations/README.md
  echo \"  → observations dir ready (README.md \$(wc -c < ~/.hermes/memories/observations/README.md) bytes)\"
"
echo

# ===== Step 3: Restart Hermes =====
if [ "$DO_RESTART" = "true" ]; then
  echo "[3/3] Restarting $HERMES_SERVICE..."
  # The 'hermes' user does NOT have sudo per the runbook. Use root SSH for systemctl.
  ssh -i "$VPS_SSH_KEY" "root@${VPS_HOST#*@}" "
    set -euo pipefail
    systemctl restart $HERMES_SERVICE
    sleep 2
    systemctl is-active $HERMES_SERVICE
    # Restart the HAE ingest listener too (deploy may have updated hae_ingest.py).
    # Tolerate absence on hosts where Phase 3 isn't installed.
    if systemctl list-unit-files hae-ingest.service >/dev/null 2>&1; then
      systemctl restart hae-ingest.service && systemctl is-active hae-ingest.service
    fi
  "
  echo "  ✓ restarted"
else
  echo "[3/3] Skipped (--no-restart)"
fi

echo
echo "Done."
