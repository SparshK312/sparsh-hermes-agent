# Tests

Lightweight validation suite that catches the bug classes we've actually
hit in this project. Designed to run in CI on every push/PR without
requiring a Hermes install.

## What's checked

| File | What it catches |
|---|---|
| `test_skills.py` | YAML colon-in-description bug (broke 3 skills day 1), missing required fields, `platforms` not including `linux` (skill silently excluded), description too short for NL routing, version not semver, name/dir mismatch |
| `test_config.py` | Malformed `mcp_servers.yaml` / `cron_additions.json`, missing required cron fields, `tools.include` allowlist drift (vault-as-canonical guardrail), cron script paths outside Hermes' allowed root (sandbox block), accidental secret-in-repo |
| `test_scripts.py` | bash syntax errors (`bash -n`), Python syntax errors (`py_compile`), missing shebangs in cron scripts, `deploy.sh` not executable, voice-wrapper patcher broken |
| `test_plugin.py` | Custom plugin files (when committed) parse as valid Python |

## What's NOT checked (and why)

- **Behavior tests / skill routing** — would need a running Hermes + mocked LLM. Too expensive vs. value. Real behavior validation comes from sending actual Telegram messages.
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
