# Tests

Lightweight validation suite that catches the bug classes we've actually
hit in this project. Designed to run in CI on every push/PR without
requiring a Hermes install.

## What's checked

~85 tests, honestly split: **~27 behavior tests** (the load-bearing logic that would
silently corrupt data or drop a message if it regressed — these are the ones that
matter) plus **structural tripwires** (syntax / config / skill well-formedness — cheap
guards that catch a broken commit before it deploys, but only check shape, not behavior).
We deliberately keep this ratio honest: one consolidated well-formedness check per skill
(not six), and no no-op placeholder tests.

**Behavior tests (~27):**

| File | What it catches |
|---|---|
| `test_coach.py` | structured-output **validator** (rejects ungrounded numbers / >2 actions / missing question), midday intake-**pace** math (the under-eating trigger), per-lift **progression** parsing, message **budget** (quiet hours / daily cap / dedup) |
| `test_brief_gate.py` | fire-once, mark-**only**-on-confirmed-send (a failed send re-fires, never vanishes), templated fallback non-empty + low-sleep flag + completed-deadline exclusion, exact section-header match |
| `test_fitness.py` | volume-landmark bucketing, set counting + fractional secondary credit, empty-stub not counted, unmapped exercise surfaced (not silently dropped) |
| `test_hae.py` | kJ→kcal, sleep-stage extraction, daily-grouped→per-day rows, and the manual-edit-protection invariant (HAE never clobbers a hand-corrected sleep value) |

**Structural tripwires (shape only):**

| File | What it catches |
|---|---|
| `test_skills.py` | one consolidated check per skill: a malformed `SKILL.md` (bad YAML, missing field, no `linux` platform, weak description, bad semver, name≠dir) that would silently drop the skill from Hermes' registry |
| `test_config.py` | malformed `mcp_servers.yaml` / `cron_additions.json`, missing cron fields, `tools.include` allowlist drift, cron paths outside the sandbox, accidental secret-in-repo, `SOUL.public.md` structure/routing targets |
| `test_scripts.py` | `bash -n` + `py_compile` (syntax), shebangs, `deploy.sh` ships SOUL via scp, source-patchers intact |

## What's NOT checked (and why)

- **Skill ROUTING** (does a given message pick the right skill) — would need a running Hermes + mocked LLM. Validated by sending real Telegram messages. (The skills' *structure* is checked; their runtime *selection* is not.)
- **Cron agent composition** — the context-aware nudges' STATE blocks + suppression are tested, but the agent's phrasing of the final message is verified live post-deploy.
- **Card/chart rendering** — cairosvg/matplotlib output is a presentation concern; the fitness *math* behind it is tested, the pixels are eyeballed.
- **MCP connectivity** — requires the food-tracker MCP running. Live on the VPS, not here.
- **Cron firing** — Hermes runs the scheduler, not pytest. Verified by watching `hermes cron list` post-deploy.

## Running

```bash
cd /path/to/sparsh-hermes-agent
pip install pytest pyyaml
pytest -v
```

## CI

GitHub Actions runs the full suite on every push and PR via
`.github/workflows/test.yml`. Failing tests block merge.
