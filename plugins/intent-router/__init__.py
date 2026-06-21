"""intent-router — auto-escalate coaching/planning/judgment turns to Sonnet.

Everyday logging and small talk stay on the cheap default model (Haiku). Turns that
need real reasoning — coaching, planning, advice, "why", decisions, reviews, trade-offs —
are routed to claude-sonnet-4-6 by setting a session-scoped model override in the
`pre_gateway_dispatch` hook (fires once per inbound user message, before the agent is
built). The next turn's agent is rebuilt on the chosen model automatically because the
agent-cache signature includes the model. When the conversation drops back to chatter/
logging, the router override is cleared and the session reverts to the cheap default.

Safe by construction:
  - The plugin manager wraps every hook callback in try/except, so a bad turn here can
    never break message dispatch; we also guard internally.
  - We NEVER clobber an explicit user `/model` choice: those overrides carry a full
    provider/api_key bundle and no `_source` tag; ours carries only {model, _source:"router"}.
  - Model-only override → same-provider (anthropic) escalation; the resolver applies the
    model on top of the existing pooled credential.
"""
from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger("plugins.intent-router")

COACH_MODEL = "claude-sonnet-4-6"
# Dedicated decision log — escalations don't surface in the gateway journal at the
# default level, so we append every routing decision here for verifiability + tuning.
_LOG_FILE = os.path.expanduser("~/.hermes/logs/intent_router.log")


def _audit(decision: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        with open(_LOG_FILE, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {decision:<8}  {text[:70]!r}\n")
    except Exception:  # noqa: BLE001
        pass

# Turns that deserve the smart tier (coaching / planning / judgment).
_ESCALATE = re.compile(
    r"\b(coach|advice|advise|should i|what should|"
    r"why (is|are|am|do|did|does|can|would|won'?t|isn'?t)|"
    r"plan|planning|strateg|analy[sz]e|review|critique|assess|evaluate|trade-?off|"
    r"decide|decision|recommend|feedback|form check|prioriti[sz]e|thoughts on|opinion|"
    r"help me (think|decide|figure|plan)|how (should|do) i|compare|pros and cons|worth it|"
    r"resume|cover letter)\b",
    re.I,
)
# Pure logging / chatter that must STAY on the cheap tier (matched at message start).
_LOG_ONLY = re.compile(
    r"^\s*(logged|ate|drank|had|did|finished|done|just (ate|did|drank|finished|hit|had)|"
    r"/log|water|weigh|morning weigh|"
    r"\d+\s*(lb|kg|ml|l|g|reps?|sets?|min|cal|kcal)\b|"
    r"hi|hey|yo|thanks|thank you|ty|ok|okay|cool|nice|got it|👍)",
    re.I,
)


def _route(event=None, gateway=None, session_store=None, **kwargs):
    """pre_gateway_dispatch callback: pick the model tier for this turn. Returns None
    (observer-style) — we mutate the gateway's session override directly."""
    try:
        if event is None or gateway is None:
            return None
        text = (getattr(event, "text", "") or "").strip()
        source = getattr(event, "source", None)
        if not text or source is None:
            return None
        overrides = getattr(gateway, "_session_model_overrides", None)
        if overrides is None:
            return None
        key = gateway._session_key_for_source(source)
        cur = overrides.get(key) or {}
        # Respect an explicit user /model override (full bundle, not router-set) — leave it.
        if cur and cur.get("_source") != "router":
            return None
        escalate = bool(_ESCALATE.search(text)) and not _LOG_ONLY.match(text)
        if escalate:
            overrides[key] = {"model": COACH_MODEL, "_source": "router"}
            logger.info("intent-router: escalated turn to %s | %r", COACH_MODEL, text[:60])
            _audit("SONNET", text)
        elif cur:
            overrides.pop(key, None)  # was router-set; revert to the cheap default tier
            logger.info("intent-router: reverted to default tier | %r", text[:60])
            _audit("revert", text)
        else:
            _audit("haiku", text)
    except Exception:  # noqa: BLE001 — never break dispatch over routing
        return None
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _route)
