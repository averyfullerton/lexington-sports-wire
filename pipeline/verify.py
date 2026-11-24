"""Check player names in a fact sheet against the game's ESPN box score.

Gamebooks list players as initials ("A.Armstrong"), and the fact step sometimes
expands those into a guessed first name ("Alex Armstrong" for Andrew Armstrong).
This fixes a name when exactly one player in the game has that last name and the
same first initial (so "Javontae Barnes" becomes Jovantae Barnes even with an Elijah
Barnes on the team), and reports anything it can't match.
"""

import re

import httpx

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/{league}/summary"
ESPN_LEAGUE = {"nfl": "nfl", "cfb": "college-football"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'.\-]+(?: [A-Z][a-zA-Z'\-]+){1,2}(?: (?:Jr\.|Sr\.|II|III|IV))?)")


def game_players(espn_id: str, league: str = "nfl") -> list[str]:
    r = httpx.get(SUMMARY.format(league=ESPN_LEAGUE[league]), params={"event": espn_id}, timeout=30)
    r.raise_for_status()
    names = set()
    for team in r.json()["boxscore"].get("players", []):
        for cat in team["statistics"]:
            for a in cat["athletes"]:
                names.add(a["athlete"]["displayName"])
    return sorted(names)


def _last(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p.lower() not in SUFFIXES]
    return parts[-1].lower() if parts else ""


def fix_names(text: str, players: list[str], teams: set[str]) -> tuple[str, list[tuple[str, str]]]:
    """Return text with guessed first names corrected, plus the list of (wrong, right)."""
    by_last: dict[str, list[str]] = {}
    for p in players:
        by_last.setdefault(_last(p), []).append(p)
    fixes = []
    for cand in set(NAME_RE.findall(text)):
        if cand in players or any(cand in t or t.startswith(cand) for t in teams):
            continue
        matches = by_last.get(_last(cand), [])
        if len(matches) > 1:  # shared last name (Jovantae and Elijah Barnes): narrow by first initial
            matches = [m for m in matches if m[0].lower() == cand[0].lower()]
        if len(matches) != 1:
            continue
        right = matches[0]
        first_c, first_r = cand.split()[0], right.split()[0]
        # Same last name, same first initial, different first name: a guessed expansion.
        if first_c != first_r and first_c[0].lower() == first_r[0].lower() and cand.split()[-1] in right:
            fixes.append((cand, right))
    for wrong, right in fixes:
        text = re.sub(r"\b" + re.escape(wrong) + r"\b", right, text)
    return text, fixes


def fix_fact_sheet(content: dict, players: list[str]) -> list[tuple[str, str]]:
    """Correct guessed names in place across every text field; returns the fixes made."""
    teams = {content.get("away_team", ""), content.get("home_team", "")}
    all_fixes = []

    def walk(v):
        if isinstance(v, str):
            new, fixes = fix_names(v, players, teams)
            all_fixes.extend(fixes)
            return new
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    content.update(walk(content))
    return sorted(set(all_fixes))


ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/{league}/teams/{abbr}/roster"
UNAVAILABLE_GROUPS = {"injuredReserveOrOut", "suspended"}


def team_roster(abbr: str, league: str = "nfl") -> dict:
    """{"active": [...names], "unavailable": [...names]} from the team's current ESPN roster.

    For college teams pass the ESPN team id as `abbr` (some school abbreviations aren't URL-safe).
    """
    r = httpx.get(ROSTER.format(league=ESPN_LEAGUE[league], abbr=abbr.lower()), timeout=30)
    r.raise_for_status()
    out = {"active": [], "unavailable": []}
    for group in r.json()["athletes"]:
        key = "unavailable" if group.get("position") in UNAVAILABLE_GROUPS else "active"
        out[key] += [a["displayName"] for a in group["items"]]
    return out


def drop_unavailable(content: dict, unavailable: list[str]) -> list[str]:
    """Remove players to watch who are out (injured reserve, out, suspended); returns their names."""
    out = {n.lower() for n in unavailable}
    keep, dropped = [], []
    for p in content.get("players_to_watch") or []:
        (dropped if p["name"].lower() in out else keep).append(p)
    content["players_to_watch"] = keep
    return [p["name"] for p in dropped]
