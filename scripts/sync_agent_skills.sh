#!/usr/bin/env bash
#
# sync_agent_skills.sh — pull agent-authored skill edits from the VPS back into this
# repo, via a 3-way merge, so the self-improvement loop is never lost and never blocks
# a deploy.
#
# The agent edits skills/*/SKILL.md (+ adds references/*.md) directly on the VPS as it
# learns. Those live only in the VPS working tree (the deploy key is read-only, so the
# VPS can't push). This script captures them:
#
#   base   = the VPS's committed version (HEAD = last deploy, the common ancestor)
#   theirs = the VPS working file (base + the agent's edits)
#   ours   = this repo's file (base + any edits I made locally)
#   -> git merge-file 3-way merges ours+theirs over base.
#
# Non-overlapping edits merge cleanly and get committed automatically. A genuine
# overlap (I edited the same lines the agent did) is reported as a CONFLICT (exit 1,
# a .SYNC_CONFLICT file written) instead of silently clobbering either side. New files
# the agent added are copied in. Deleted-on-VPS files are left alone (reported).
#
# Usage:
#   scripts/sync_agent_skills.sh            # capture + commit (run before a push)
#   scripts/sync_agent_skills.sh --dry-run  # report what it WOULD capture, no writes
#
# Exit: 0 = clean (captured or nothing to do) · 1 = conflicts/needs manual merge.
set -uo pipefail

_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$_DIR/.env" ] && { set -a; . "$_DIR/.env"; set +a; }
VPS_HOST="${VPS_HOST:-hermes@your-vps-ip}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/hetzner_hermes}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/home/hermes/sparsh-hermes-agent}"
REPO="$_DIR"
DRY=false
[ "${1:-}" = "--dry-run" ] && DRY=true

ssh_vps() { ssh -i "$VPS_SSH_KEY" "$VPS_HOST" "$@"; }

cd "$REPO"
echo "[sync] checking VPS for agent skill edits…"
mapfile -t entries < <(ssh_vps "cd '$VPS_REPO_PATH' && git status --porcelain -uall -- skills/")
if [ "${#entries[@]}" -eq 0 ]; then
  echo "[sync] none — VPS skills tree is clean"
  exit 0
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
captured=0; conflicts=(); skipped=()

for line in "${entries[@]}"; do
  [ -z "$line" ] && continue
  code="${line:0:2}"; path="${line:3}"
  case "$code" in
    *D*) skipped+=("$path (deleted on VPS — left as-is)"); continue ;;
  esac
  if ! ssh_vps "cat '$VPS_REPO_PATH/$path'" > "$TMP/theirs" 2>/dev/null; then
    skipped+=("$path (unreadable on VPS)"); continue
  fi
  if [ -f "$REPO/$path" ]; then
    ssh_vps "cd '$VPS_REPO_PATH' && git show HEAD:'$path' 2>/dev/null" > "$TMP/base" || : > "$TMP/base"
    cp "$REPO/$path" "$TMP/ours"
    if git merge-file -q -p "$TMP/ours" "$TMP/base" "$TMP/theirs" > "$TMP/merged" 2>/dev/null; then
      if ! cmp -s "$TMP/merged" "$REPO/$path"; then
        $DRY || cp "$TMP/merged" "$REPO/$path"
        captured=$((captured+1)); echo "[sync]  merged  $path"
      fi
    else
      conflicts+=("$path")
      $DRY || cp "$TMP/merged" "$REPO/$path.SYNC_CONFLICT"
      echo "[sync]  CONFLICT $path"
    fi
  else
    $DRY || { mkdir -p "$(dirname "$REPO/$path")"; cp "$TMP/theirs" "$REPO/$path"; }
    captured=$((captured+1)); echo "[sync]  new     $path"
  fi
done

[ "${#skipped[@]}" -gt 0 ] && printf '[sync]  skipped %s\n' "${skipped[@]}"

if [ "${#conflicts[@]}" -gt 0 ]; then
  echo "[sync] ⚠ ${#conflicts[@]} CONFLICT(S): I and the agent both edited these lines."
  echo "[sync]   Resolve each (a .SYNC_CONFLICT copy holds the merge markers), then re-run."
  exit 1
fi

if $DRY; then
  echo "[sync] dry-run: would capture $captured file(s)"
  exit 0
fi

if [ -n "$(git status --porcelain -- skills/)" ]; then
  git add skills/
  git commit -q -m "sync: capture agent-authored skill edits from VPS"
  echo "[sync] captured $captured file(s) → committed"
else
  echo "[sync] nothing new to capture"
fi
exit 0
