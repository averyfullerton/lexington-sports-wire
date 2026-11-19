"""Week schedule from ESPN's public scoreboard feed (espn.com is on the allowlist).

Only provides the list of games to research (teams, date, status). All facts shown
to readers still come from the Parallel step.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
EASTERN = ZoneInfo("America/New_York")


def week_games(season: int, week: int) -> list[dict]:
    r = httpx.get(SCOREBOARD, params={"seasontype": 2, "week": week, "year": season}, timeout=30)
    r.raise_for_status()
    games = []
    for e in r.json().get("events", []):
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c["team"] for c in comp["competitors"]}
        kickoff = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(EASTERN)
        games.append({
            "espn_id": e["id"],
            "away_team": teams["away"]["displayName"],
            "home_team": teams["home"]["displayName"],
            "away_abbr": teams["away"]["abbreviation"],
            "home_abbr": teams["home"]["abbreviation"],
            "game_date": kickoff.date().isoformat(),
            "game_day": f"{kickoff:%A}, {kickoff:%b} {kickoff.day}",
            "completed": comp["status"]["type"]["completed"],
        })
    return games


def latest_completed_week(season: int) -> int:
    """The most recent regular-season week whose games are all final."""
    r = httpx.get(SCOREBOARD, params={"seasontype": 2, "year": season}, timeout=30)
    r.raise_for_status()
    current = r.json()["week"]["number"]
    for week in (current, current - 1, current - 2):
        games = week_games(season, week)
        if games and all(g["completed"] for g in games):
            return week
    raise RuntimeError(f"no completed week near week {current}")
