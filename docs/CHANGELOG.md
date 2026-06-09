# Changelog

All notable changes to the Sparsh Hermes Health Layer.

Format: `YYYY-MM-DD` · type · description.

Types: `add` · `change` · `fix` · `deploy` · `decision` · `chore`.

---

## 2026-05-27

### Hermes update + provider revert (cost down to ~$0.50/mo)

- **deploy** — Updated Hermes Agent from v0.14.0 (SHA `1566d71`) to main SHA `458a94e` via `hermes update --backup --yes`. 1,195 commits ingested. Pre-update snapshot at `~/.hermes/backups/pre-update-2026-05-27-121015.zip`. 7 post-update smoke tests all passed.
- **fix** — Picked up upstream PR #32963 ("recover Codex Responses streams with null output", merged 2026-05-27 02:37 UTC). Closes the broader Codex NoneType bug that broke morning crons + voice.
- **decision** — Reverted main `model.provider` from `openai` (paid API) → `openai-codex` (free OAuth via ChatGPT Pro). Validated text+voice work post-update on free path. Marginal monthly cost: ~$8-15 → **~$0.50** (Whisper only).
- **add** — Custom provider plugin `~/.hermes/plugins/model-providers/openai/__init__.py` (+ `plugin.yaml`) on VPS. Registers `openai` as a Hermes provider pointing at `api.openai.com/v1`. Currently inactive; kept as emergency fallback for Codex outages.

### Voice + photo fixes (both source patches)

- **add** — `scripts/patch/voice_wrapper_patch.py` — idempotent text-substitution patcher for `gateway/run.py`. Strips the `[The user sent a voice message~ Here's what they said: "..."]` envelope that breaks the openai-codex Responses API parser. `--check` / `--revert` modes. Auto-discovers via Python import path; `HERMES_GATEWAY_RUN` env var override for testing.
- **add** — `scripts/patch/image_routing_patch.py` — second source patcher for `agent/image_routing.py:372`. Drops the `"What do you see in this image?"` auto-prompt when photo arrives without caption. The placeholder biased the LLM into describe-image mode, bypassing log-food routing for meal photos.
- **change** — `scripts/deploy.sh` — now invokes both patchers idempotently after every `git pull` on the VPS. Deploy fails fast if either source has drifted to an unrecognizable shape (so we know to manually re-verify before voice/photo silently breaks).
- **change** — `HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS=15` set in VPS `~/.hermes/.env`. Widens Telegram's photo+caption merge window from 0.8s default so caption-after-photo arrives in the same MessageEvent → one LLM turn → log-food sees full context on first contact.

### Cron fixes

- **fix** — Mirrored 3 health-nudge cron scripts from repo to Hermes' allowed sandbox root `~/.hermes/scripts/health_*.sh` via `deploy.sh`. Before this, the scripts had NEVER successfully run since 2026-05-26 deploy (Hermes refuses scripts outside `~/.hermes/scripts/`).
- **change** — `config/cron_additions.json` script paths updated to point at the mirrored copies (`/home/hermes/.hermes/scripts/health_*.sh`).
- **change** — `~/.hermes/cron/jobs.json` (VPS): script paths updated to match.

### Test suite + CI

- **add** — `tests/` directory with 83 pytest tests covering:
  - YAML colon bug in skill descriptions (broke 3 skills on day 1)
  - Required frontmatter fields on every SKILL.md
  - `platforms` field includes `linux` (silent skill exclusion if missing)
  - Description length sanity (NL routing signal)
  - Skill `name` matches directory name
  - `version` is semver-ish
  - `config/mcp_servers.yaml` parses + has food-tracker + `tools.include: [search_food]` allowlist preserved
  - `config/cron_additions.json` parses + script paths point at allowed sandbox root
  - No 40-char alphanumeric API keys committed in config
  - All `scripts/**/*.sh` pass `bash -n`
  - All `scripts/**/*.py` compile
  - `scripts/deploy.sh` is executable
  - Each cron script has a shebang
  - Both source patchers round-trip on synthesized fixtures (unpatched → patch → patched → revert → unpatched)
- **add** — `.github/workflows/test.yml` GitHub Actions CI workflow. Runs on push + PR. Includes a fixture test that verifies the voice-wrapper patcher round-trips against the upstream shape. (Push pending — GitHub PAT needs `workflow` scope; run `GH_TOKEN= gh auth refresh -s workflow`.)
- **chore** — `.gitignore` adds `.venv-tests/` so the local test venv doesn't get committed.

### Skills

- **add** — `skills/log-workout/SKILL.md` v1.0.0. Formalizes voice/text workout logging. Writes concise `lifted` label to daily-note frontmatter + detailed exercise breakdown to Health section's `**Workout:**` line. Vault-only by construction (no MCP tools to misuse).
- **change** — `skills/log-food/SKILL.md` → v1.2.0. Promoted photo input from "Phase 2" caveat to first-class. Added Step 0 (vision_analyze pre-processing that synthesizes `items[]` from images before proceeding to the standard template-match → nutrient-lookup → clarify → vault-write flow). Added Pitfall #11 (photo→notes-only anti-pattern with WRONG vs RIGHT path spelled out) and Pitfall #12 (recurring photographable meals get more eager template-creation prompts).
- **change** — `skills/log-food/SKILL.md` → v1.1.0 (earlier in the day). Strict mandatory clarify-before-write. Description sharpened so the NL router picks log-food over meal-templates for meal descriptions.
- **change** — `skills/week-summary/SKILL.md` → v1.1.0. Same forbidden-tools guardrail as log-food. Vault-only aggregation (read 7 daily notes' frontmatter, never query MCP `get_summary`).

### MCP allowlist (structural enforcement)

- **change** — `config/mcp_servers.yaml` — added `tools.include: [search_food]` allowlist on food-tracker MCP. **Architecture-critical.** Hermes filters at the agent-exposure layer, so the LLM physically can't call the storage-side tools (`log_food`, `get_summary`, `set_goals`, `get_daily_log`, `delete_entry`) that would drift from the vault. Replaces the advisory-only "🛑 Forbidden tools" section in skill descriptions which the LLM kept ignoring.

### Skills cleanup

- **chore** — Disabled 76 bundled Hermes skills via `skills.disabled` in `~/.hermes/config.yaml`. Down from 97 enabled → 32 (25 useful + 7 system tools). Kept: 11 custom + google-workspace + 5 GitHub skills + claude-code, codex, notion, linear, obsidian, plan, hermes-agent. ~55-65% reduction in per-turn input tokens.

### Reply keyboard

- **add** — `scripts/setup/setup_reply_keyboard.py` — one-shot Python script that installs a persistent 3×3 reply keyboard in Sparsh's bot chat via Telegram Bot API. Stdlib only. Also sets Bot Menu Button to `MenuButtonCommands` for slash-command autocomplete. Supports `--remove` to clear.

### Vault docs

- **chore** — Backfilled today's missing meal macros into vault: `04 - Daily Notes/2026-05-27.md` frontmatter (kcal 1330, P 63, C 138, F 53) + `07 - Health/Food Log/2026-05-27.md` with breakfast + partial lunch + coffee entries. Required because log-food bypass bug (now fixed) skipped frontmatter writes earlier in the day.
- **chore** — Created `07 - Health/Supplements.md` (Vit D 2000 IU, Multivitamin, B-12 1000 mcg, Creatine 5g). Read by log-vitamins skill.

---

## 2026-05-26

### Initial deploy + Phase 1 build

- **add** — Repo initialized at `~/Documents/Development/sparsh hermes agent/`.
- **add** — Repo scaffold: README, .gitignore, .env.example, directory structure (skills/, scripts/, config/, docs/).
- **add** — `config/mcp_servers.yaml` — Phase 1 MCP servers: `food-tracker-mcp` (USDA-backed) + `mcp-opennutrition` (300k food DB, local Docker).
- **add** — `config/cron_additions.json` — Phase 1 cron nudges (morning weigh-in, lunch check, evening summary), all `script`-mode (no LLM turn cost).
- **add** — Phase 1 skills (9): `log-food`, `log-water`, `log-weight`, `log-vitamins`, `log-sleep`, `today-summary`, `week-summary`, `what-missed`, `meal-templates`.
- **add** — `scripts/deploy.sh` — non-destructive rsync deploy to VPS.
- **add** — `docs/DEPLOY.md` — full VPS deployment procedure with rollback steps.
- **decision** — Architecture: skip MacroFactor / Foodnoms (full agent-first via Hermes + vault). HAE Premium for wearable bridge (Phase 3). Local faster-whisper for voice (Phase 2). gpt-5.4-mini vision via Codex OAuth, test live; fall back to OpenAI API if needed.
- **deploy** — Phase 1 deployed to Hermes VPS end-to-end. 6 distinct bugs hit + fixed during deploy:
  - **fix** — YAML colons in skill descriptions broke 3 skills (commit `ede365a`). Single-quote-wrap descriptions; double inner apostrophes.
  - **fix** — food-tracker-mcp's dotenvx prints `◇ injected env...` banner to stdout, breaking MCP stdio. Set `DOTENV_CONFIG_QUIET=true` in env block.
  - **fix** — food-tracker-mcp hardcodes SQLite path to `../../data.db` relative to install dir. Global npm install made it non-writable. User-local install at `~/npm-local/`.
  - **fix** — Hermes does NOT substitute `${VAR}` in MCP env blocks. Literal secret values required.
  - **fix** — Telegram disallows hyphens in slash commands. Hermes' resolver maps `/log_water` → `log-water`; both forms work.
  - **fix** — opennutrition MCP fails with `pop from empty deque` (streamable-http transport mismatch). Deferred to Phase 2.
- **verified** — First-ever vault round-trip: `/log_water 500` → `water_l: 0.5` in today's daily note. Then natural-language "drank 500ml of water" → `water_l: 1.0`.
- **add** — Vault doc `07 - Health/Phase 1 Deploy - Live Status.md` (the canonical handoff doc — comprehensively rewritten 2026-05-27).
