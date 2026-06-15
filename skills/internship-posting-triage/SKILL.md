---
name: internship-posting-triage
description: Classify a job posting (URL or scraped JSON) against Sparsh's PEY rotation pipeline. Filters for Fall 2026 / Winter 2027 / Summer 2027 SWE + AI/ML intern roles in US/Canada. Outputs a Pipeline.md row, a triage verdict (apply now / wait / skip), and optionally a draft cover line referencing his Claude Ambassador / Shopify resume hooks. Used by the daily 8:30 AM internship scraper cron and on-demand from Claude Code when reviewing a posting.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [internship, scraping, pipeline, career]
    category: productivity
---

# internship-posting-triage

## When to Use

- Daily Hermes scraper cron (8:30 AM Toronto) feeds scraped postings JSON in batches
- Ad-hoc: Claude Code or Hermes is given a job posting URL and asked "should I apply?"
- Bulk processing: import a list of URLs and get a triaged spreadsheet

## Inputs

Either:
- **URL**: a single job posting page. Skill fetches and parses.
- **JSON object** with shape: `{ company, title, url, location, description, posted_date, application_deadline? }`

## Watchlist (must be one of these companies)

```
FAANG+:    Meta, Apple, Netflix, Microsoft, Nvidia
AI labs:   Anthropic, OpenAI, xAI, Cohere, Mistral, Google DeepMind
Fintech:   Stripe, Plaid, Mercury, Ramp, Brex, Robinhood
Startups:  Vercel, Linear, Notion, Figma, Scale AI, Databricks,
           Perplexity, Cursor (Anysphere), Replit
Active:    Amazon, Mercor, Google, Shopify (already in pipeline)
```

If the company is not on this list, **default verdict: skip**, but note the company in case Sparsh wants to add it.

## Filters (must pass all)

1. **Period match** — role description mentions one of:
   - `Fall 2026` (Sept–Dec 2026)
   - `Winter 2027` / `Spring 2027` (Jan–Apr 2027) — note that "Spring" at US companies often means Jan–Apr
   - `Summer 2027` (May–Aug 2027)
   - 4-month internship is preferred. **12-month or 16-month placements: skip** (doesn't fit 4-rotation plan).
2. **Location** — US or Canada. Remote-OK roles count if the company is US/Canada-based.
3. **Role family** — must match one of:
   - SWE / Software Engineer Intern / SDE Intern
   - AI / ML / Machine Learning Intern / Research Engineer Intern
   - Data Engineer / Data Scientist Intern (lower priority but accept)
4. **Authorization** — if posting requires US citizenship/clearance (`US Person`, `Top Secret`, `ITAR`), **skip** — Sparsh is Canadian.

## Triage Verdict Logic

```
IF company is on watchlist
   AND all 4 filters pass
   AND posted_date < 7 days ago
   AND not already in postings_seen.json:
       VERDICT = "apply now"

IF company is on watchlist AND filters pass BUT posted_date > 7 days:
       VERDICT = "wait" (likely already missed early-bird; investigate before applying)

IF filters fail:
       VERDICT = "skip" (with reason)

IF already in postings_seen.json:
       VERDICT = "duplicate" (don't re-process, don't re-ping)
```

## Outputs

### 1. Triage record (JSON)

```json
{
  "verdict": "apply now",
  "company": "Stripe",
  "role": "Software Engineer Intern, Fall 2026",
  "url": "https://stripe.com/jobs/listing/...",
  "location": "South San Francisco, CA",
  "period": "Fall 2026",
  "role_family": "SWE",
  "posted_date": "2026-05-02",
  "deadline": "2026-06-15",
  "filter_pass": true,
  "duplicate": false,
  "notes": "Direct path; no referral signal observed.",
  "resume_hooks": ["Shopify PEY (relevant)", "Claude Ambassador (AI angle)"]
}
```

### 2. Pipeline.md row (for `00 - Dashboard/Internship Pipeline.md` "New Postings" section)

```
- **2026-05-02** [Stripe](https://stripe.com/jobs/listing/...) — SWE Intern, Fall 2026 · South SF · ⏳ apply now
```

### 3. Draft cover line (optional, only when `verdict == "apply now"`)

One sentence opener referencing the closest resume hook:
- If AI lab: lead with Claude Ambassador
- If Shopify-adjacent commerce / payments: lead with Shopify PEY
- If founder-angle company (early-stage, exec-heavy hiring): lead with Call Fusion → Perfecti acqui-hire
- Otherwise: lead with Shopify PEY (most universally credible)

Example: *"I'm currently on PEY at Shopify (Toronto, May–Aug 2026) and previously sold my voice-AI startup Call Fusion to Perfecti Technologies — Stripe's developer experience for payments has been a long-time reference for the API-first work I want to keep doing."*

### 4. Duplicate check side effect

Append the posting's canonical ID (URL minus query params) to `06 - Internships/Internship Pipeline/postings_seen.json` so the next scraper run skips it.

`postings_seen.json` schema:
```json
{
  "seen": [
    { "id": "stripe.com/jobs/listing/abc123", "first_seen": "2026-05-02", "verdict": "apply now" },
    ...
  ]
}
```

## Procedure

1. **Dedupe check** — load `postings_seen.json`, check canonical URL against `seen[].id`. If match, return verdict `duplicate` and stop.
2. **Fetch + parse** — if input is a URL, fetch via `scrapling` skill (Cloudflare-aware) → extract description, location, posted date.
3. **Watchlist check** — match company against the 28-company list (case-insensitive, trim trailing Inc/LLC).
4. **Run filters** — period, location, role family, authorization. Track which fails and why.
5. **Compute verdict** — see logic above.
6. **Generate Pipeline row** — only if `verdict ∈ {apply now, wait}`.
7. **Generate cover line** — only if `verdict == apply now`.
8. **Append to postings_seen.json** — always (even on `skip`, to avoid re-processing every scraper run).
9. **Return JSON** — caller (cron job, Claude Code) decides whether to ping Telegram, write to Pipeline.md, etc.

## Pitfalls

1. **Period inference is fuzzy** — postings often say "12-week internship starting June" without naming a season. Use start month: May/June → Summer, Aug/Sept → Fall, Jan/Feb → Winter. If start month is missing entirely, mark `period: "unknown"` and let the user decide.
2. **"Software Engineer Intern" with no period in title** — read full description, look for "expected start", "duration", "graduation requirements". Don't reject just because title is generic.
3. **Mercor's marketplace model** — Mercor postings are often for *clients*, not Mercor itself. Treat any "via Mercor" role as Mercor-adjacent and attach `referral_source: Mercor` in the triage record.
4. **Anthropic / OpenAI off-cycle hires** — these labs don't always run formal intern programs. Look for "Member of Technical Staff" / "Research Engineer (early career)" — flag these as `verdict: investigate` rather than auto-classifying.
5. **Stripe / Vercel posting tricks** — both companies use job boards that hide details behind login walls or load via JS. Use `scrapling` with browser mode, not raw HTTP, when those domains appear.
6. **Already-applied check** — before generating "apply now" verdict, also check `Internship Pipeline.md` Active Companies table to avoid duplicate-applying. (Postings_seen.json catches scraper dupes; Pipeline.md catches "Sparsh already applied directly".)
7. **Time zones in deadlines** — "deadline 11:59 PM" without TZ usually means the company's local TZ. For Pipeline.md, write deadlines in the company's TZ with explicit suffix: `2026-06-15 23:59 PT`.
8. **Batch cron input** — when the watcher feeds a pre-deduped JSON batch with `canonical_id` already present, still append every posting to `postings_seen.json` after triage, but only write `apply now`/`wait` items into `## New Postings`. For URLs, normalize by stripping query params before dedupe; for JSON, trust the provided `canonical_id`.
9. **Batch count mismatches** — if the parsed posting count does not match the wake-gate `new_count`, reconcile the raw batch before triaging or writing history. Small shortfalls usually mean a few postings were dropped during reconstruction, not that the batch should be accepted as-is.

## References

- `references/cron-batch-triage.md` — batch-cron input/output conventions, canonical ID handling, and what gets written where.
- `references/batch-triage-verification.md` — count-sanity and write-order checklist for pre-deduped watcher batches.

## Resume Hooks (from Sparsh's record)

For cover line generation:

- **Anthropic / Claude Ambassador** — use for AI labs, model providers, AI-tooling companies
- **Shopify PEY 2026 (Toronto)** — current; use for commerce, payments, dev tools, anywhere "real-world startup speed" matters
- **Perfecti / Call Fusion acqui-hire (Sept 2025)** — use for founder-friendly early-stage companies, voice-AI, agents
- **UofT ECE + AI Engineering Minor + Eng Business Minor** — use as default credibility tail
- **3.70 sessional GPA Summer 2025 + ran Call Fusion concurrently** — use when "can you handle workload?" is the implicit screen

## Verification

Run dry-run on a known good posting:

```
hermes chat -q "Triage this URL: https://www.amazon.jobs/en/jobs/<known-amazon-fall-2026-intern-url>. Just return the JSON, don't ping anything."
```

Expected:
- `verdict: "duplicate"` (already in pipeline as Amazon)
- `company: "Amazon"`
- `filter_pass: true`

Run on a known reject (12-month role):

```
hermes chat -q "Triage this URL: https://<some-12-month-coop-posting>. Just return the JSON."
```

Expected:
- `verdict: "skip"`
- `notes` cites period mismatch / 12-month duration
