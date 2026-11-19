"""Playoff picture from ESPN's public standings feed (espn.com is on the allowlist).

Seeds 1-4 are division winners, 5-7 wild cards, 8-16 out of the playoffs.
"""

import httpx

STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
CONF_ABBR = {"American Football Conference": "AFC", "National Football Conference": "NFC"}


def playoff_picture(season: int) -> dict:
    r = httpx.get(STANDINGS, params={"season": season, "seasontype": 2}, timeout=30)
    r.raise_for_status()
    out = {}
    for conf in r.json()["children"]:
        teams = []
        for e in conf["standings"]["entries"]:
            stats = {s["name"]: s.get("displayValue") for s in e["stats"]}
            teams.append({
                "seed": int(stats["playoffSeed"]),
                "team": e["team"]["displayName"],
                "abbr": e["team"]["abbreviation"],
                "record": stats["overall"],
            })
        teams.sort(key=lambda t: t["seed"])
        out[CONF_ABBR[conf["name"]]] = {
            "division_winners": [t for t in teams if t["seed"] <= 4],
            "wild_card": [t for t in teams if 5 <= t["seed"] <= 7],
            "out": [t for t in teams if t["seed"] >= 8],
        }
    return out
