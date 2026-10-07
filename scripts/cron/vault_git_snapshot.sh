#!/usr/bin/env bash
# Hermes cron: hourly git snapshot of the VPS vault (no LLM). Added 2026-10-05.
#
# WHY: Obsidian Sync's version history is what filled the 1 GiB quota (full copy per
# save, ~30-day retention, counted against storage). History now lives HERE instead:
# unlimited, free, outside Sync. GIT_DIR is OUTSIDE the vault (~/.vault-git) so there is
# no .git inside the synced folder and Hermes's .hermes.md git-root walk is unaffected.
# Text only (md, csv, json, py, canvas) — the VPS-only csv/json (pantry, metrics,
# board-spend) finally get history too. No push; commit only when something changed.
set -uo pipefail
export GIT_DIR="$HOME/.vault-git" GIT_WORK_TREE="/home/hermes/vault"
LOG="$HOME/.hermes/health/vault_git_snapshot.log"
mkdir -p "$(dirname "$LOG")"
if [ ! -d "$GIT_DIR" ]; then
  git init -q && git config user.name "hermes-vault-snapshot" && git config user.email "hermes@localhost"
  # Whitelist: ignore everything, re-include directories and text types, then re-exclude
  # .obsidian/ (a directory excluded AFTER '!*/' cannot have files re-included).
  printf '%s\n' '*' '!*/' '!*.md' '!*.csv' '!*.json' '!*.py' '!*.canvas' \
    '.obsidian/' '*.bak' '*.CORRUPT*' > "$GIT_DIR/info/exclude"
fi
# A failed snapshot must be loud (audit 2026-10-07: errors went to /dev/null and the
# script always exited 0, so e.g. a stale index.lock would stop history silently).
# Telegram once when it starts failing and once when it recovers, never every hour.
FLAG="$HOME/.hermes/health/vault_git_snapshot.FAILING"
alert() { "$HOME/.local/bin/hermes" send -t telegram -q -s "$1" "$2" >/dev/null 2>&1 || true; }
fail() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAILED: $1" >> "$LOG"
  [ -f "$FLAG" ] || { touch "$FLAG"; alert "⚠️ Vault git snapshot failing" "vault_git_snapshot.sh: $1. Hourly vault history has stopped (log: ~/.hermes/health/vault_git_snapshot.log)."; }
  exit 1
}
cd "$GIT_WORK_TREE" || fail "cannot cd to $GIT_WORK_TREE"
err=$(git add -A 2>&1) || fail "git add: ${err:0:300}"
if ! git diff --cached --quiet; then
  n=$(git diff --cached --name-only | wc -l)
  err=$(git commit -q -m "snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ) ($n files)" 2>&1) || fail "git commit: ${err:0:300}"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) committed $n files" >> "$LOG"
fi
if [ -f "$FLAG" ]; then rm -f "$FLAG"; alert "✅ Vault git snapshot recovered" "Hourly vault history is being recorded again."; fi
tail -c 100000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit 0
