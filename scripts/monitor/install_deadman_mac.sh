#!/usr/bin/env bash
# install_deadman_mac.sh — install the Mac-side VPS dead-man's switch.
#
# The script is COPIED to ~/.hermes/bin rather than run from the repo, because
# ~/Documents is TCC-protected on macOS: a launchd agent running from there
# fails with "Operation not permitted" unless the user grants Full Disk Access.
# ~/.hermes is not protected, so this needs no permission prompt.
#
# Re-run after editing scripts/monitor/vps_deadman.sh — editing the repo copy
# alone does nothing.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.hermes/bin"
PLIST="$HOME/Library/LaunchAgents/com.sparsh.hermes-deadman.plist"

mkdir -p "$DEST" "$HOME/.hermes/logs"
install -m 0755 "$SRC/vps_deadman.sh" "$DEST/vps_deadman.sh"

sed "s|__SCRIPT__|$DEST/vps_deadman.sh|" "$SRC/com.sparsh.hermes-deadman.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "  ✓ installed to $DEST/vps_deadman.sh"
launchctl list | grep -i deadman || true
