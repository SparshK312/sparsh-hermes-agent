# Tests

Lightweight validation suite that catches the bug classes we've actually
hit in this project. Designed to run in CI on every push/PR without
requiring a Hermes install.

## What's checked

The suite is a mix of **structural checks** (YAML/shape/syntax — most of the count,
scales with skill count) and **behavior tests** (the load-bearing logic that would
silently corrupt data or drop a message if it regressed). The behavior tests are the
ones that matter most; they're called out below.

| File | What it catches | Kind |
|---|---|---|
| `test_skills.py` | YAML colon-in-description bug (broke 3 skills day 1), missing required fields, `platforms` not including `linux` (skill silently excluded), description too short for NL routing, version not semver, name/dir mismatch | structural (×12 skills) |
| `test_config.py` | Malformed `mcp_servers.yaml` / `cron_additions.json`, missing required cron fields, `tools.include` allowlist drift (vault-as-canonical guardrail), cron script paths outside Hermes' allowed root (sandbox block), accidental secret-in-repo, `SOUL.public.md` structure/routing-target presence | structural |
| `test_scripts.py` | bash syntax errors (`bash -n`), Python syntax errors (`py_compile`), missing shebangs in cron scripts, `deploy.sh` not executable + ships SOUL via scp, source patchers broken | structural |
| `test_hae.py` | **Behavior:** kJ→kcal, sleep-stage extraction, daily-grouped→per-day rows, frontmatter edit preserves body/fields, and the manual-edit-protection invariant (HAE never clobbers a hand-corrected sleep value) | behavior |
| `test_brief_gate.py` | **Behavior:** fire-once, mark-**only**-on-confirmed-send (a failed send re-fires, never vanishes), templated fallback always non-empty + flags low sleep + excludes completed deadlines, exact section-header match | behavior |
| `test_fitness.py` | **Behavior:** volume-landmark bucketing, set counting + fractional secondary credit, empty-stub not counted as a workout, unmapped exercise surfaced (not silently dropped) | behavior |
| `test_plugin.py` | Custom plugin files (when committed) parse as valid Python | structural |

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
