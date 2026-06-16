"""
Config for internship_scraper. Edit this to add/remove sources.

Each source is a community-maintained GitHub README of internships,
parseable as either an HTML <table> (SimplifyJobs) or a markdown
pipe table (vanshb03, speedyapply).

To add a new source:
  1. Add an entry below with name, url, format ("html_table" | "markdown_table")
  2. If format differs, extend the parsers in internship_scraper.py

Coverage notes (verified 2026-05-16):
- simplify-offseason: 286 roles across Fall 2025/Winter 2026/Spring 2026 (and
  later terms; the header is stale but Fall 2026/Winter 2027 entries appear).
  Uses HTML <table> embedded in the markdown file.
- vansh-offseason: "Spring & Fall 2026 Tech Internships by Ouckah & Vansh".
  Standard markdown pipe tables.
- speedyapply-ai: AI/ML-specific, smaller volume but high relevance.
  Markdown pipe tables with extra Salary column.
"""

SOURCES = [
    {
        "name": "simplify-offseason",
        "url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README-Off-Season.md",
        "format": "html_table",
    },
    {
        "name": "vansh-offseason",
        "url": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/OFFSEASON_README.md",
        "format": "markdown_table",
    },
    {
        "name": "speedyapply-ai",
        "url": "https://raw.githubusercontent.com/speedyapply/2026-AI-College-Jobs/main/README.md",
        "format": "markdown_table",
    },
    {
        # General SWE variant (broadens beyond the AI-only list). Same format.
        "name": "speedyapply-swe",
        "url": "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md",
        "format": "markdown_table",
    },
]

# Skip postings older than this many days. Tune up if you want a longer window.
MAX_AGE_DAYS = 14

# State file (read-only here; the LLM writes after triage). Lives in the vault
# so it survives Hermes reinstalls and is visible in Obsidian.
POSTINGS_SEEN_PATH = (
    "/home/hermes/vault/"
    "06 - Internships/Internship Pipeline/postings_seen.json"
)

# Fetch timeout per source (seconds). Hermes' overall script timeout is 120s.
FETCH_TIMEOUT_SECONDS = 30
