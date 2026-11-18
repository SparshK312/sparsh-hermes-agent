# Sparsh Hermes Agent — Health Layer

A personal assistant I run for myself. It lives in Telegram, runs on a small VPS, and reads and writes my Obsidian vault. I log meals, workouts, water, weight and supplements by text, voice or photo, and it keeps track of things I'm waiting on and checks back on them.

This repo is the layer I built on top of the open-source [Hermes Agent](https://github.com/NousResearch/hermes-agent) runtime: the skills, scripts, plugins, scheduled jobs, deploy tooling and tests. The runtime itself is installed separately.

## What it does

- **Logging by text, voice or photo.** A meal photo or a sentence becomes calories and macros in the vault. A screenshot of a workout becomes structured sets. Every write goes through one script (`scripts/vault/vault_log.py`), so the model never edits the files by hand.
- **Wearable data.** A Fitbit band syncs through the Google Health API every 20 minutes, and iPhone step data arrives over a private Tailscale tunnel. The two are merged into one daily record with fixed rules for which source wins.
- **Open loops.** It reads my notes for things that are unfinished (someone owes me a reply, I owe a step by a date, a plan with a condition) and keeps a list. An hourly check-in looks at the list, searches my inbox read-only for the person I'm waiting on, and sends at most one message, or nothing.
- **Messages that are checked before they are sent.** The check-in writes its own "what I checked" sentence in code. A model may reword it, and the rewording is rejected if it contains a name, number or date that isn't in the facts, a second question, or an offer to do something it can't do.
- **A morning brief** built from sleep, calendar, tasks and inbox, with a second pass that removes sentences the data doesn't support.
- **Cards.** A daily nutrition card, a weekly review, and a muscle-coverage map rendered from logged workouts.

## Guardrails

- **Spend.** One message can trigger at most 30 model steps. A plugin pauses the chat agent at a daily and a monthly dollar ceiling and answers without a model call. Scripts that call the API directly share a separate daily ceiling.
- **Message budget.** Unprompted messages are capped per day, held during quiet hours, limited to one topic a day, and back off when I'm not replying. Every send is recorded in one ledger.
- **Write guard.** A plugin blocks direct edits to the health files and to machine-written views, so data only changes through the scripts that validate it.
- **Fail closed where it matters.** A corrupt ledger blocks nudges. A corrupt store is refused, never treated as empty. A cap that drops data says so in the log.

## Repo layout

```
scripts/
  assistant/    send ledger and nudge budget, spend-guarded model client, open-loops store,
                extraction from the notes, the hourly check-in, wording checks
  vault/        the health writer, food memory, daily-note setup, log tooling
  fitness/      coach, cards, muscle volume and heat map
  hae/          wearable sync and merge, morning brief
  email/        read-only morning inbox triage
  monitor/      cost monitor, sync watchdog, upstream release watcher
  cron/         thin wrappers the scheduler runs
  patch/        idempotent patches applied to the runtime at deploy
  deploy.sh     test gates, push, pull on the VPS, copy, restart
plugins/        vault-write-guard, turn-context, assistant-context, spend-guard
skills/         the instructions the agent follows for each kind of request
tests/          pytest suite
```

## Tests

```
python3 -m venv .venv-tests && .venv-tests/bin/pip install pytest pyyaml
.venv-tests/bin/python -m pytest tests -q
```

There are about 380 pytest tests plus standalone invariant suites (around 1,800 assertions) under `scripts/*/test_*.py`. `deploy.sh` runs all of them and aborts on any failure, and CI runs them on every push.

Most tests exist because something broke. Each one is mutation-tested when it is written: the old behaviour is put back, and the test has to fail. A test that can't fail isn't counted.

## Deploy

`./scripts/deploy.sh` runs the gates, pushes, pulls on the VPS, copies scripts and plugins into place, re-applies the runtime patches and restarts the gateway. Host and key come from a local `.env` that is not committed. See `docs/DEPLOY.md` for first-time setup.

## Status

In daily use. Logging, the wearable sync, the open-loops list, the morning brief and the cost guard are live. The hourly check-in runs in shadow mode (it records what it would have sent) while I tune it.

## License

Personal use. Not yet licensed for redistribution.
