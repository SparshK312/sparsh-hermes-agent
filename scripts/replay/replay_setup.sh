#!/bin/bash
# Build the pristine sandbox copies (run as the hermes user). Touches nothing live: read-only copies.
set -euo pipefail
R=/tmp/hreplay; L=/home/hermes/.hermes
rm -rf $R/pristine; mkdir -p $R/pristine/dot $R/cases $R/out
rsync -a --exclude .git --exclude .obsidian /home/hermes/vault/ $R/pristine/vault/
cd $L
cp -p config.yaml SOUL.md auth.json context_length_cache.yaml models_dev_cache.json $R/pristine/dot/ 2>/dev/null || true
for d in memories plugins skills assistant health hooks; do [ -e $d ] && rsync -a $d $R/pristine/dot/; done
mkdir -p $R/pristine/dot/cache && rsync -a cache/images $R/pristine/dot/cache/
mkdir -p $R/pristine/dot/{hermes-agent,venvs,scripts,bin,logs,sessions}
# env: keep model + tool keys, DROP anything that can message him or read his mail
grep -vE '^(TELEGRAM_|EMAIL_|SLACK_|WHATSAPP_)' .env > $R/pristine/dot/.env
grep -cE '^TELEGRAM_' $R/pristine/dot/.env || true
du -sh $R/pristine/vault $R/pristine/dot | tr '\n' ' '; echo; ls $R/pristine/dot | tr '\n' ' '
