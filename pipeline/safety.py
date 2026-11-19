"""Kid-safety filter. Fails closed: when in doubt, drop it.

Layers:
1. Decisions API classification (classify_story) - anything but a confident "safe" is dropped.
2. Rewrite step returns SKIP for fundamentally mature stories (see write.py).
3. Keyword tripwire on final text (tripwire) - any hit blocks it.
Every drop is logged with its reason to data/drops.jsonl.
"""

import json
import re
from datetime import datetime, timezone

from pipeline.config import DATA_DIR

UNSAFE_CATEGORIES = {
    "arrest", "lawsuit", "suspension", "new_or_serious_injury",
    "death", "gambling", "abuse", "other_mature",
}

# Word-boundary matches, case-insensitive. Extend as the safety eval finds misses.
TRIPWIRE_TERMS = [
    "arrest", "arrested", "charged", "lawsuit", "sued", "suspend", "suspended", "suspension",
    "died", "death", "dead", "killed", "shooting", "gun", "assault", "abuse", "domestic",
    "dui", "drunk", "drug", "drugs", "jail", "prison", "police", "court", "trial",
    "bet", "bets", "betting", "gamble", "gambling", "sportsbook", "odds", "parlay", "wager",
    "concussion", "torn", "acl", "surgery", "carted off", "season-ending", "paralyzed",
    "injury", "injured", "injuries", "hospital", "ambulance",
    "damn", "hell", "sex", "sexual",
]
_TRIPWIRE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in TRIPWIRE_TERMS) + r")\b", re.IGNORECASE
)


def tripwire(text: str) -> list[str]:
    """Return the tripwire terms found in text. Empty list means it passed."""
    return sorted({m.group(0).lower() for m in _TRIPWIRE_RE.finditer(text)})


def log_drop(item_id: str, stage: str, reason: str, detail: dict | None = None) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "item": item_id,
        "stage": stage,
        "reason": reason,
        "detail": detail or {},
    }
    with (DATA_DIR / "drops.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


DECISIONS_URL = "https://api.parallel.ai/v1/decisions"
UNSAFE_THRESHOLD = 0.2  # any category at or above this probability makes the story unsafe

SCOPES = {
    # Story selection: is the article ABOUT something mature? Passing mentions are handled later,
    # because the kid text itself is checked with the strict "text" scope before it is shown.
    "topic": ("Judge ONLY the text in state.article_text; do not use other coverage. Answer true only if the "
              "MAIN subject of the article (what the story is about, or a major part of it) is the following, "
              "not a brief passing mention: "),
    # Final kid text: any mention at all counts.
    "text": ("Judge ONLY the text in state.article_text, the exact words a child would read. Do not use other "
             "coverage of the same game or people. Answer true if the text mentions this at all: "),
}

# One predicate ("noul") per unsafe category, all in one Decisions request.
SAFETY_QUESTIONS = {
    "arrest": "The article mentions an arrest, criminal charge, police investigation or jail involving anyone.",
    "lawsuit": "The article mentions a lawsuit, court case, or legal dispute.",
    "suspension": "The article mentions a suspension, fine for conduct, or league discipline of a player or coach.",
    "new_or_serious_injury": (
        "The article reports a NEW injury, a player being hurt, injury status (questionable, out, injured reserve), "
        "or any serious injury or health scare. A player who is healthy and back playing after an old injury "
        "does not count by itself."),
    "death": "The article mentions anyone's death or a life-threatening event.",
    "gambling": "The article mentions betting, gambling, odds, point spreads, sportsbooks or fantasy-money contests.",
    "abuse": "The article mentions abuse, violence off the field, harassment or domestic incidents.",
    "other_mature": (
        "The article includes other content not suitable for a 6-year-old: drugs, alcohol, sexual content, "
        "profanity, political controversy, firings, contract or money disputes, or anything frightening or sad."),
}


def classify_story(story: dict, scope: str = "text", timeout: float = 60) -> dict:
    """Classify a story with the Parallel Decisions API (REST; the SDK has no Decisions method yet).

    story: {"url", "title", "text"}. Returns {"label", "confident", "probabilities", "refusals",
    "citations", "off_allowlist_citations", "decision_id"}. The Decisions API has no source policy, so
    its own citations may come from anywhere; they are recorded here and never shown on the site.
    """
    import os

    import httpx

    from pipeline.config import is_allowed_url

    body = {
        "mode": "fast",
        "state": {"headline": story.get("title") or "", "url": story["url"],
                  "article_text": (story.get("text") or "")[:20000]},
        "questions": {k: {"type": "noul", "instructions": SCOPES[scope] + v,
                          "criteria": {"true": "article_text itself contains this", "false": "article_text does not contain this"}}
                      for k, v in SAFETY_QUESTIONS.items()},
    }
    r = httpx.post(DECISIONS_URL, headers={"x-api-key": os.environ["PARALLEL_API_KEY"]}, json=body, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    answers = d.get("answers", {})
    probs, refusals = {}, []
    for k in SAFETY_QUESTIONS:
        a = answers.get(k) or {"type": "refusal"}
        if a.get("type") != "noul":
            refusals.append(k)
        else:
            probs[k] = round(float(a["noul"]), 3)
    flagged = sorted((k for k, v in probs.items() if v >= UNSAFE_THRESHOLD), key=lambda k: -probs[k])
    cites = [c.get("url") for c in d.get("citations") or []]
    return {
        "label": flagged[0] if flagged else ("refusal" if refusals else "safe"),
        "confident": not flagged and not refusals,
        "flagged": flagged,
        "probabilities": probs,
        "refusals": refusals,
        "citations": cites,
        "off_allowlist_citations": [u for u in cites if u and not is_allowed_url(u)],
        "decision_id": d.get("decision_id"),
        "scope": scope,
    }


def is_safe(classification: dict) -> bool:
    """Fail closed: only a confident 'safe' passes."""
    return classification.get("label") == "safe" and classification.get("confident") is True
