#!/usr/bin/env python3
"""
browser_fetch.py — rung 3 of the JD ladder. A real browser, used only when the
cheaper rungs have already failed.

THE LADDER (cost ascending; never skip a rung):
  1. ATS JSON API      — greenhouse / lever / ashby / workday / workable /
                         smartrecruiters. Fast, structured, free. Handles most roles.
  2. curl_cffi         — a real Chrome TLS/JA3 fingerprint. Beats edges that block on
                         the ClientHello rather than on headers (iCIMS behind AWS WAF
                         answers curl with 200 and httpx with 405).
  3. THIS              — headless Chromium via Playwright, for pages whose JD only
                         exists after JavaScript runs.

⚠️ **DELIBERATELY NOT IN THE REFRESH PATH.** A browser fetch is ~5s versus ~0.3s and
carries a 91 MB dependency, so `curate.py` never calls it. It is invoked by
`jd_backfill.py --browser`, occasionally, against the small residue that rungs 1 and 2
could not read.

🔴 **WHAT THIS DOES NOT SOLVE: Tesla (26 tier-S roles).** Tesla sits behind Akamai Bot
Manager. Measured 2026-08-28: plain headless 403; headless + stealth 403; headed real
Chrome 403; headed + stealth returned a genuine 200 with real page content ONCE and then
403 on every retry. Akamai scores IP reputation and adapts, so repeated attempts make it
worse, not better. Treat Tesla as unreadable by automation and decide it separately —
do not sink more time into fingerprint games here.
"""
from __future__ import annotations

import asyncio
import re
import sys

JD_RE = re.compile(r"(responsibilit|qualification|what you.ll do|requirements|"
                   r"minimum quali|what to expect|about the role)", re.I)
MIN_CHARS = 800          # below this it is nav chrome or a challenge page
NAV_TIMEOUT = 40_000
SETTLE_MS = 3_500        # SPA hydration; most boards paint the JD well inside this


async def fetch_rendered(urls: list[str], *, headless: bool = True,
                         concurrency: int = 3) -> dict[str, str]:
    """{url: extracted_text} for the pages that rendered something JD-shaped.

    One browser for the whole batch — launching per URL costs ~1.5s each and is the
    single biggest waste in a naive implementation. Missing/blocked pages are simply
    absent from the result rather than raising, so a caller can treat this as
    best-effort enrichment.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError:
        print("[browser] playwright/playwright-stealth not installed — skipping",
              file=sys.stderr)
        return {}

    out: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900},
                                        locale="en-US")

        async def one(url: str):
            async with sem:
                page = await ctx.new_page()
                try:
                    r = await page.goto(url, wait_until="domcontentloaded",
                                        timeout=NAV_TIMEOUT)
                    if r is not None and r.status in (404, 410):
                        return
                    await page.wait_for_timeout(SETTLE_MS)
                    txt = re.sub(r"\s+", " ", await page.inner_text("body")).strip()
                    if len(txt) >= MIN_CHARS and JD_RE.search(txt):
                        out[url] = txt
                except Exception:  # noqa: BLE001 - best effort by design
                    pass
                finally:
                    await page.close()

        await asyncio.gather(*[one(u) for u in urls])
        await browser.close()
    return out


if __name__ == "__main__":
    got = asyncio.run(fetch_rendered(sys.argv[1:]))
    for u, t in got.items():
        print(f"{len(t):>7}  {u}\n  {t[:200]}\n")
    print(f"{len(got)}/{len(sys.argv)-1} rendered")
