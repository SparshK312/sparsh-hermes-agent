# DEPLOY.md — Hermes Health Layer

Deployment procedure for the `sparsh-hermes-agent` repo onto the Hetzner VPS where Hermes is running.

**Critical safety rule:** never modify the existing `daily-note-prefill` or `internship-watcher` cron entries. Never edit Hermes' core install. Only add **alongside**.

---

## One-time setup (do these once)

### Prerequisites

- VPS already has Hermes Agent v0.14.0+ running (it does — see vault Hermes VPS Runbook).
- SSH access to the VPS at `hermes@your-vps-ip` and `root@your-vps-ip` using `~/.ssh/hetzner_hermes`.
- Mac has `git` and `gh` installed (verified).
- GitHub account + ability to create private repos.

### Step 1 — Create the GitHub repo

From the Mac, in the repo directory:

```bash
cd "~/sparsh-hermes-agent"
git init
git add .
git commit -m "Initial commit: Phase 1 scaffold"

# Create private GitHub repo and push (via gh CLI)
gh repo create sparsh-hermes-agent --private --source=. --remote=origin --push
```

### Step 2 — Clone on the VPS

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip

# On VPS:
cd ~
git clone git@github.com:<YOUR-GH-USERNAME>/sparsh-hermes-agent.git
# (or https URL if SSH keys aren't set up on VPS yet)

cd sparsh-hermes-agent
cp .env.example .env
nano .env   # paste USDA_API_KEY
chmod +x scripts/cron/*.sh
exit
```

### Step 3 — Start mcp-opennutrition Docker container on the VPS

```bash
ssh -i ~/.ssh/hetzner_hermes root@your-vps-ip
# Install Docker if missing:
which docker || (curl -fsSL https://get.docker.com | sh)

# Allow the hermes user to run docker (one-time):
usermod -aG docker hermes

exit
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip

# Pull + run the container:
docker pull deadletterq/mcp-opennutrition
docker run -d --name mcp-opennutrition --restart unless-stopped \
  -p 127.0.0.1:9113:3000 \
  deadletterq/mcp-opennutrition

# Verify:
docker ps | grep mcp-opennutrition
curl -s http://localhost:9113/health  # if it has a healthcheck; otherwise just check it's running
```

### Step 4 — Merge MCP servers into Hermes config

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip

# Back up the existing config FIRST:
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%Y%m%d-%H%M%S)

# Merge: open the existing config, add the `mcp_servers:` block from
# ~/sparsh-hermes-agent/config/mcp_servers.yaml.
# If a `mcp_servers:` key already exists, ADD entries under it.
nano ~/.hermes/config.yaml
# Paste the content of config/mcp_servers.yaml under the existing structure.

# Also ensure skills.external_dirs includes the repo's skills dir:
# Find the line `skills:` and under it, ensure:
#   external_dirs:
#     - /home/hermes/.claude/skills            # existing
#     - /home/hermes/sparsh-hermes-agent/skills  # ADD THIS
```

**Sanity-check the YAML:**
```bash
python3 -c "import yaml; yaml.safe_load(open('/home/hermes/.hermes/config.yaml'))" && echo OK
```

### Step 5 — Add cron entries

The Phase 1 cron entries live in `config/cron_additions.json`. Each is a *separate* job to append into the `jobs` array of `~/.hermes/cron/jobs.json` — do NOT overwrite the file.

```bash
# Back up first:
cp ~/.hermes/cron/jobs.json ~/.hermes/cron/jobs.json.bak.$(date +%Y%m%d-%H%M%S)

# Use jq to append each new job. From the home directory:
JOBS_TO_ADD=$(jq -c '.jobs_to_append[]' ~/sparsh-hermes-agent/config/cron_additions.json)

# Append each into the existing jobs.json's `jobs` array:
for job in $JOBS_TO_ADD; do
  jq --argjson new "$job" '.jobs += [$new] | .updated_at = (now | strftime("%Y-%m-%dT%H:%M:%S-04:00"))' \
    ~/.hermes/cron/jobs.json > /tmp/jobs.new && mv /tmp/jobs.new ~/.hermes/cron/jobs.json
done

# Verify:
jq '.jobs | length' ~/.hermes/cron/jobs.json   # should be 5 now (2 existing + 3 new)
jq '.jobs[].name' ~/.hermes/cron/jobs.json
```

### Step 6 — Restart Hermes

```bash
ssh -i ~/.ssh/hetzner_hermes root@your-vps-ip
systemctl restart hermes-gateway.service
sleep 2
systemctl status hermes-gateway.service
exit
```

### Step 7 — Verify skills loaded

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip
hermes skills list | grep -E "log-food|log-water|log-weight|log-vitamins|log-sleep|today-summary|week-summary|what-missed|meal-templates"
# Should show all 9.
exit
```

### Step 8 — Verify MCP servers connected

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip
hermes mcp list 2>/dev/null || tail -100 ~/.hermes/logs/gateway.log | grep -i mcp
# Look for food-tracker and opennutrition listed as "connected" / "registered".
exit
```

### Step 9 — Smoke test from Telegram

Send to `@SparshHermesBot`:

```
/water 500
```

Expected reply: `💧 +0.5L. Today: 0.5L / 2.5L target.`

Then check the daily note frontmatter on Mac (after Obsidian Sync pulls):

```bash
head -20 "<your-obsidian-vault>/04 - Daily Notes/$(date +%Y-%m-%d).md"
# Should show water_l: 0.5
```

---

## Updates (after one-time setup is done)

For ANY future code change (new skill, edit existing skill, new script):

```bash
# On Mac:
cd "~/sparsh-hermes-agent"
# ... make edits ...
git add -A && git commit -m "what changed"
./scripts/deploy.sh
```

`deploy.sh` does:
1. `git push` to GitHub
2. SSH to VPS, `git pull` in the repo
3. Restart `hermes-gateway.service`

Total time: ~10 seconds.

For changes that touch **only** cron scripts or skills (not config), the restart could be skipped — but it's cheap, so just always restart unless deploying mid-cron.

---

## Rollback procedure

### Rollback a code change

```bash
# Mac:
cd "~/sparsh-hermes-agent"
git revert HEAD               # or git reset --hard <prev-sha>
./scripts/deploy.sh
```

### Rollback the MCP server config

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip
cp ~/.hermes/config.yaml.bak.<TIMESTAMP> ~/.hermes/config.yaml
exit
ssh -i ~/.ssh/hetzner_hermes root@your-vps-ip
systemctl restart hermes-gateway.service
```

### Rollback the cron additions

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip
cp ~/.hermes/cron/jobs.json.bak.<TIMESTAMP> ~/.hermes/cron/jobs.json
# Restart not strictly needed — the cron ticker re-reads jobs.json every minute.
```

### Stop the mcp-opennutrition container

```bash
ssh -i ~/.ssh/hetzner_hermes hermes@your-vps-ip
docker stop mcp-opennutrition && docker rm mcp-opennutrition
```

---

## What this deploy does NOT touch

- `~/.hermes/SOUL.md`
- `~/.hermes/memories/MEMORY.md`
- The existing `daily-note-prefill` and `internship-watcher` cron entries (they remain untouched)
- The internship scraper Python scripts at `~/.hermes/scripts/internship_scraper.py`
- Google Calendar / Telegram bot tokens
- Obsidian Sync configuration

If any of those need to change, that's a separate procedure.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Skills don't show in `hermes skills list` | `skills.external_dirs` in config.yaml. Try `hermes /reload-skills`. |
| MCP tools not callable from agent | `hermes mcp list`; check `~/.hermes/logs/gateway.log` for MCP connection errors. |
| Cron not firing | `hermes cron list`. Check `last_status` field. Logs in `agent.log`. |
| `/water` reply is wrong format | The skill's description didn't match the trigger phrase precisely. Check `agent.log` for which skill was loaded. |
| Daily note has no `water_l` field | Daily note template needs the field. Verify `Templates/Daily Note.md` in vault. |
| mcp-opennutrition not responding | `docker ps`; `docker logs mcp-opennutrition`; check port 9113 is free. |
| `git pull` on VPS fails with auth | The GitHub repo is private. Set up an SSH deploy key or use HTTPS with a personal access token. |

---

## Phase 2/3/4 — future deploys

- Phase 2: switch `transcription.local.model: base` in config, add `log-meal-photo` skill, seed `07 - Health/Meal Templates/`.
- Phase 3: deploy `scripts/hae_ingest.py` as a systemd service behind nginx/Caddy with TLS. Add the HAE MCP. Configure HAE iOS app.
- Phase 4: modal skills (`startworkout`/`endworkout`/etc.), weekly + monthly rollup crons, `/health-coach` Claude Code skill.

Each phase has its own DEPLOY section that gets appended here.
