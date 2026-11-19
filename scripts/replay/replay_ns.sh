#!/bin/bash
# Run ONE command as the hermes user inside a private mount namespace where the live vault,
# the live ~/.hermes and the live repo skills folder are replaced by sandbox copies.
# Nothing done inside can reach the live files. Run as root. Usage: replay_ns.sh <cmd...>
set -euo pipefail
exec unshare -m --propagation private bash -c '
  set -euo pipefail
  R=/tmp/hreplay; L=/home/hermes/.hermes
  mkdir -p $R/orig
  mount --bind $L $R/orig
  mount --bind $R/work/vault /home/hermes/vault
  mount --bind $R/work/repo_skills /home/hermes/sparsh-hermes-agent/skills
  mount --bind $R/work/dot $L
  for d in hermes-agent venvs scripts bin; do mount --bind $R/orig/$d $L/$d; mount -o remount,bind,ro $L/$d; done
  cd /home/hermes/vault
  exec runuser -u hermes -- env HOME=/home/hermes USER=hermes LOGNAME=hermes \
     PATH=/home/hermes/.hermes/hermes-agent/venv/bin:/home/hermes/.local/bin:/usr/local/bin:/usr/bin:/bin "$@"
' _ "$@"
