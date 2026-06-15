#!/usr/bin/env bash
# Hermes cron: health-meal-rescue — midday UNDER-EATING rescue (failure mode #1).
# Fires the pace check; coach.py stays SILENT (just the wakeAgent gate) unless he's
# behind on calories/protein AND the message budget allows. Sends itself via Bot API.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
exec /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode meal-rescue
