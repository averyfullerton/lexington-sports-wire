"""Kid writing step (OpenAI API). Writes ONLY from verified facts.

Rules (first-grade level): short everyday sentences (~8-12 words), exactly 5 sentences
per game paragraph, never mean ("had a tough day", "will want a do-over"),
never invent facts - if a fact is missing or low-confidence, leave it out.
Stories that are fundamentally mature return SKIP; never soften them.
"""

import re

SKIP = "SKIP"


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def game_recap(facts: dict) -> str:
    """5-sentence recap of a completed game from verified facts. TODO."""
    raise NotImplementedError


def game_preview(facts: dict) -> str:
    """5-sentence preview of an upcoming game from verified facts. TODO."""
    raise NotImplementedError


def big_story(story: dict) -> str:
    """First-grade rewrite of a storyline, or SKIP if fundamentally mature. TODO."""
    raise NotImplementedError
