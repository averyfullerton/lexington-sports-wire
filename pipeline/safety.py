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


def classify_story(story: dict) -> dict:
    """Classify a story with the Parallel Decisions API.

    Returns {"label": "safe" | <UNSAFE_CATEGORIES member>, "confident": bool, "basis": ...}.
    TODO: implement with the parallel-api skill after checking current docs.
    """
    raise NotImplementedError


def is_safe(classification: dict) -> bool:
    """Fail closed: only a confident 'safe' passes."""
    return classification.get("label") == "safe" and classification.get("confident") is True
