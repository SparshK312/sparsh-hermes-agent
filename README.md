# Sparsh Hermes Agent — Health Layer

A personal AI health agent. It lives in Telegram, takes food/water/weight/sleep/workout logs by **text, voice, or photo**, writes everything into an Obsidian vault, runs daily accountability nudges, and pulls **Apple Watch** data (sleep, HRV, steps, calories) straight into the vault over a private network. Built as a layer of skills + scripts + config on top of the open-source [Hermes Agent](https://github.com/NousResearch/hermes-agent) framework, deployed 24/7 on a Hetzner VPS.

### Highlights
- **Multi-modal capture** — log a meal by typing, sending a voice note, or snapping a photo; vision + a nutrition MCP turn it into macros automatically.
- **Apple Watch → vault bridge** — Health Auto Export pushes wearable data over a Tailscale private tunnel to a small ingest listener on the VPS, and a morning cron folds it into that day's note. Sleep/HRV/resting-HR with zero manual entry.
- **Near-zero running cost (~$0.50/mo)** — routes the LLM through Codex OAuth (covered by an existing ChatGPT Pro plan); only Whisper voice transcription costs anything.
- **Safety-minded** — MCP tools are locked to an allowlist, and the agent's write scope is sandboxed so it can't mutate arbitrary files.
- **Real engineering hygiene** — a 117-test pytest suite runs in CI on every push, with idempotent source patches that survive framework upgrades.
- **Weekly fitness visuals** — builds an anatomical muscle-coverage heat-map card from logged workouts and sends it to Telegram.

> Note: this is the layer *I* built — the skills, the wearable bridge, the deploy/patch tooling, the crons, the tests. The underlying agent runtime is the Hermes Agent framework (linked above), installed separately.

## What this repo is

**Operational code only** — skills, scripts, and config snippets that extend the user's deployed Hermes Agent.

This repo does **not** contain:
- The Hermes Agent itself (installed separately at `~/.hermes/` on the VPS via the official installer)
- Tracking data (lives in the Obsidian vault, separately synced via Obsidian Sync)
- Profile / training plan / coaching docs (live in the Obsidian vault at `07 - Health/`)

## Architecture (one-line)

```
Telegram → Hermes (Hetzner VPS) → food/water/etc. skills → vault frontmatter + per-meal markdown
                                ↑                       ↑
                       voice (Whisper local)   wearable data (HAE Premium → REST POST)
```

Full architecture lives in the vault at `07 - Health/Architecture.md`.

## Repo layout

```
.
├── README.md                                 (this file)
├── .env.example                              (placeholder for secrets — never commit .env)
├── .gitignore
├── skills/                                   (Hermes auto-loads via skills.external_dirs)
│   ├── log-food/SKILL.md                     (v1.2.0 — first-class photo input)
│   ├── log-water/SKILL.md
│   ├── log-weight/SKILL.md
│   ├── log-vitamins/SKILL.md                 (reads 07 - Health/Supplements.md in vault)
│   ├── log-sleep/SKILL.md
│   ├── log-workout/SKILL.md                  (v2.0.0 — structured per-workout file + daily-note back-compat)
│   ├── today-summary/SKILL.md
│   ├── week-summary/SKILL.md                 (v1.1.0 — vault-only reads, no MCP)
│   ├── what-missed/SKILL.md
│   └── meal-templates/SKILL.md
├── scripts/
│   ├── deploy.sh                             (push → pull → mirror crons → apply patches → restart)
│   ├── cron/                                 (mirrored into ~/.hermes/scripts/ on deploy)
│   │   ├── morning_weigh_in_nudge.sh
│   │   ├── lunch_check.sh
│   │   └── evening_summary.sh
│   ├── patch/                                (idempotent source patchers, auto-applied on deploy)
│   │   ├── voice_wrapper_patch.py            (strips broken voice envelope from gateway/run.py)
│   │   └── image_routing_patch.py            (drops empty-caption auto-prompt from image_routing.py)
│   └── setup/
│       └── setup_reply_keyboard.py           (one-shot — installs 3x3 keyboard + Bot Menu Button)
├── config/
│   ├── SOUL.public.md                        (generic system-prompt template; the personal SOUL.md is gitignored + scp'd to the VPS by deploy.sh)
│   ├── mcp_servers.yaml                      (food-tracker with tools.include allowlist = search_food only)
│   └── cron_additions.json                   (3 health-nudge crons w/ paths to mirrored scripts)
├── tests/                                    (83 pytest tests — run pre-push)
│   ├── test_skills.py                        (YAML + required fields + linux platform)
│   ├── test_config.py                        (mcp_servers structural + cron sandbox paths)
│   ├── test_scripts.py                       (bash -n + py_compile + patcher round-trip)
│   ├── test_plugin.py                        (custom plugin source parses)
│   ├── conftest.py
│   └── README.md
├── .github/workflows/test.yml                (CI — pytest suite + bash syntax + patcher round-trips)
└── docs/
    ├── DEPLOY.md                             (step-by-step VPS deploy procedure)
    └── CHANGELOG.md                          (full history through 2026-05-27)
```

## Quick deploy

```bash
# After editing code:
git add -A && git commit -m "what changed"
./scripts/deploy.sh
# → push to GitHub → pull on VPS → mirror cron scripts → auto-apply both source patches → restart gateway
```

First-time VPS setup is in [docs/DEPLOY.md](docs/DEPLOY.md).

## Run tests

```bash
python3 -m venv .venv-tests
.venv-tests/bin/pip install pytest pyyaml
.venv-tests/bin/pytest tests/ -v
# Expect: all tests pass (117 at time of writing)
```

## Architecture (one-line)

```
Telegram → Hermes (Hetzner VPS, free Codex OAuth via ChatGPT Pro)
           ↓
   voice via OpenAI Whisper API (~$0.50/mo)   |   text / photo direct
           ↓
   25 useful skills + food-tracker MCP (search_food only) + vault writes
           ↓
   <your-obsidian-vault>/   (Obsidian Sync to phone + VPS)
```

**Two source patches** to Hermes are auto-applied by `deploy.sh` (idempotent, survive `hermes update`):

1. `gateway/run.py` — strip voice-message wrapper that breaks Codex Responses API parser
2. `agent/image_routing.py` — drop empty-caption auto-prompt that breaks photo+caption routing

Both have corresponding round-trip tests in CI.

Plus one env var: `HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS=15` (widens Telegram photo+caption merge window from 0.8s default).

Full architecture + decision history lives in the Obsidian vault at `07 - Health/Architecture.md`. Live state in `07 - Health/Phase 1 Deploy - Live Status.md` (canonical "read me first" doc for resuming this work).

## Status

**Fully working.** Text, voice, photo, Apple Watch sync, and all cron paths route correctly through free Codex OAuth. Marginal monthly cost: **~$0.50** (OpenAI Whisper API only — everything else is covered by an existing ChatGPT Pro subscription).

Test suite passing locally + green in GitHub Actions CI on every push.

Two known minor issues, both upstream (Hermes side, not ours):
- Telegram voice batching can occasionally drop in-flight responses on rapid taps (Hermes issue #31328, still open)
- Hermes fallback chain has a few edge-case bugs not relevant to our setup

See `docs/CHANGELOG.md` for the full evolution.

## License

Personal use. Not yet licensed for redistribution.
