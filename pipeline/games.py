"""Game facts via Parallel Task API + JSON schema.

For each game in the last completed NFL week (Thu-Mon) and the upcoming week, collect:
score, top performers, key plays, team records, storyline. Keep per-field `basis`
(citations + confidence); flag or drop low-confidence fields. Allowlisted sources only.
Output: data/last_week.json, data/next_week.json
"""

# JSON schema for the Task API output. Draft - confirm against current Parallel docs.
GAME_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "home_team": {"type": "string"},
        "away_team": {"type": "string"},
        "home_score": {"type": "integer"},
        "away_score": {"type": "integer"},
        "home_record": {"type": "string"},
        "away_record": {"type": "string"},
        "top_performers": {"type": "array", "items": {"type": "string"}},
        "key_plays": {"type": "array", "items": {"type": "string"}},
        "storyline": {"type": "string"},
    },
    "required": ["home_team", "away_team"],
}


def collect_last_week() -> list[dict]:
    raise NotImplementedError


def collect_next_week() -> list[dict]:
    raise NotImplementedError
