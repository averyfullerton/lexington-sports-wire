"""College football: Florida and Kentucky games plus every game with an AP Top 10 team.

    uv run python -m pipeline.cfb            # auto-detect the week
    uv run python -m pipeline.cfb --week 12  # or pick the last completed week

Same facts-first pipeline as the NFL side, reusing its modules:
  - schedule, AP Top 25, box scores, rosters, stats: ESPN's public college-football feeds
  - game facts: Parallel Task + JSON schema (games.py), allowlist only, low-confidence fields dropped
  - recaps and previews: OpenAI (write.py with league="cfb")
  - player cards: only two per game (players.py with league="cfb")
Output: data/cfb/last_week.json, next_week.json, rankings.json, players/.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import cache

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from parallel import Parallel

from pipeline.config import CONFIG_DIR, DATA_DIR
from pipeline.games import collect_game, fact_path, game_input
from pipeline.players import build_card, slugify
from pipeline.safety import log_drop
from pipeline.schedule import EASTERN
from pipeline.verify import drop_unavailable, fix_fact_sheet, game_players
from pipeline.write import (EXAMPLE_KIDS, EXAMPLE_TOPICS, OPENERS, PREVIEW_CLOSERS, PREVIEW_OPENERS, game_preview,
                            game_recap, usable_facts)

LEAGUE = "cfb"
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
OUT_DIR = DATA_DIR / "cfb"
RAW_GAMES = DATA_DIR / "raw" / "cfb_games"
RAW_PLAYERS = DATA_DIR / "raw" / "cfb_players"
CARDS_DIR = OUT_DIR / "players"
TOP_N = 10
CARDS_PER_GAME = 2
PINNED = json.loads((CONFIG_DIR / "cfb_teams.json").read_text())  # keyed by ESPN abbreviation


def _rank(c: dict) -> int | None:
    r = (c.get("curatedRank") or {}).get("current")
    return r if r and r <= 25 else None


def _record(c: dict) -> str | None:
    total = next((r for r in c.get("records") or [] if r.get("type") == "total"), None)
    return (total or (c.get("records") or [{}])[0]).get("summary")


def scoreboard(season: int, week: int | None = None) -> dict:
    params = {"groups": 80, "limit": 400, "seasontype": 2}  # groups=80: FBS
    if week:  # with no week (and no year), ESPN returns the current week
        params |= {"week": week, "dates": season}
    r = httpx.get(f"{SITE}/scoreboard", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def week_games(season: int, week: int) -> list[dict]:
    """Every game in the week that has Florida, Kentucky or an AP Top 10 team.

    Finished games use the rank at kickoff (ESPN's scoreboard). Upcoming games use the current AP poll:
    the scoreboard can still show last week's ranks for them after the new poll is out.
    """
    current = {t["abbr"]: t["rank"] for t in ap_top25()["teams"]}
    games = []
    for e in scoreboard(season, week).get("events", []):
        comp = e["competitions"][0]
        side = {c["homeAway"]: c for c in comp["competitors"]}
        done = comp["status"]["type"]["completed"]
        rank = {s: _rank(side[s]) if done else current.get(side[s]["team"]["abbreviation"]) for s in side}
        abbrs = {side[s]["team"]["abbreviation"] for s in side}
        ranks = list(rank.values())
        if not (abbrs & PINNED.keys() or any(r and r <= TOP_N for r in ranks)):
            continue
        kickoff = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(EASTERN)
        g = {"espn_id": e["id"], "game_date": kickoff.date().isoformat(),
             "game_day": f"{kickoff:%A}, {kickoff:%b} {kickoff.day}",
             "completed": comp["status"]["type"]["completed"],
             "venue_city": (comp.get("venue") or {}).get("address", {}).get("city") if comp.get("neutralSite") else None}
        for s in ("away", "home"):
            t = side[s]["team"]
            g |= {f"{s}_team": t["displayName"], f"{s}_abbr": t["abbreviation"], f"{s}_id": t["id"],
                  f"{s}_rank": rank[s], f"{s}_record": _record(side[s])}
        games.append(g)
    return games


def latest_completed_week(season: int) -> int:
    """The latest week whose selected games are all final (ESPN's current week, or the one before)."""
    current = scoreboard(season)["week"]["number"]
    games = week_games(season, current)
    return current if games and all(g["completed"] for g in games) else current - 1


@cache  # one poll per run
def ap_top25() -> dict:
    r = httpx.get(f"{SITE}/rankings", timeout=30)
    r.raise_for_status()
    ap = next(x for x in r.json()["rankings"] if x.get("type") == "ap")
    return {
        "poll": "AP Top 25", "headline": ap.get("headline"), "date": ap.get("date"),
        "teams": [{"rank": x["current"], "team": x["team"]["location"], "abbr": x["team"]["abbreviation"],
                   "record": x.get("recordSummary"), "previous": x.get("previous")} for x in ap["ranks"]],
    }


# --- facts (Parallel Task) ---------------------------------------------------------------------------

def collect_facts(client: Parallel, season: int, week: int, kind: str, processor: str = "base") -> dict:
    want_completed = kind == "recap"
    games = [g for g in week_games(season, week) if g["completed"] == want_completed]

    def one(g):
        inp = game_input(g["away_team"], g["home_team"], g["game_date"], season, week)
        path = fact_path(inp, processor, kind, RAW_GAMES)
        if path.exists():
            return {"cached": True}
        try:
            collect_game(client, inp, processor, kind, LEAGUE, RAW_GAMES)
            return {"cached": False}
        except Exception as e:
            return {"error": f"{g['away_abbr']}@{g['home_abbr']}: {type(e).__name__}: {e}"}

    with ThreadPoolExecutor(8) as pool:
        res = list(pool.map(one, games))
    new = sum(1 for r in res if r.get("cached") is False)
    return {"games": len(games), "new_runs": new, "errors": [r["error"] for r in res if "error" in r],
            "parallel_usd": round(new * 0.01, 2)}


# --- writing (OpenAI) --------------------------------------------------------------------------------

ATHLETE = "https://site.web.api.espn.com/apis/common/v3/sports/football/college-football/athletes/{id}"


def _athlete(athlete_id: str) -> dict:
    """One player's ESPN bio, shaped like a roster entry (for players the team roster feed leaves out)."""
    r = httpx.get(ATHLETE.format(id=athlete_id), timeout=30)
    r.raise_for_status()
    a = r.json()["athlete"]
    return {"id": a["id"], "displayName": a["displayName"], "jersey": a.get("jersey"), "position": a["position"],
            "experience": {"displayValue": a.get("displayExperience")},
            "birthPlace": {"displayText": a.get("displayBirthPlace")}}


def _box_score(espn_id: str) -> dict[str, list[tuple[str, str]]]:
    """{team abbreviation: [(athlete id, name)]} for everyone with a stat in the game."""
    r = httpx.get(f"{SITE}/summary", params={"event": espn_id}, timeout=30)
    r.raise_for_status()
    out = {}
    for t in r.json()["boxscore"].get("players", []):
        seen = out.setdefault(t["team"]["abbreviation"], [])
        for c in t["statistics"]:
            seen += [(x["athlete"]["id"], x["athlete"]["displayName"]) for x in c["athletes"]]
    return out


def _roster(team_id: str) -> list[dict]:
    r = httpx.get(f"{SITE}/teams/{team_id}/roster", timeout=30)
    r.raise_for_status()
    return r.json()["athletes"]


def _entry(g: dict, raw: dict, fresh: bool) -> dict:
    keep = ("away_team", "home_team", "away_abbr", "home_abbr", "away_rank", "home_rank", "game_date", "game_day")
    return {**{k: g[k] for k in keep}, "paragraph": raw["recap"]["paragraph"],
            "big_words": raw["recap"].get("big_words", []), "usage": raw["recap"].get("usage", []) if fresh else []}


def build_last_week(client: OpenAI, season: int, week: int, used: list[str], processor: str = "base") -> list[dict]:
    out = []
    for i, g in enumerate(g for g in week_games(season, week) if g["completed"]):
        path = fact_path(game_input(g["away_team"], g["home_team"], g["game_date"], season, week),
                         processor, "recap", RAW_GAMES)
        if not path.exists():
            print(f"missing fact sheet: {path.name}")
            continue
        raw = json.loads(path.read_text())
        c = raw["content"] or {}
        if not c.get("game_was_played"):
            log_drop(path.name, "cfb.recap", "fact sheet says the game was not played", {})
            continue
        fixes = fix_fact_sheet(c, game_players(g["espn_id"], LEAGUE))
        if fixes:
            raw.setdefault("auto_corrections", []).extend(
                {"from": a, "to": b, "source": "ESPN box score"} for a, b in fixes)
            raw.pop("recap", None)
        fresh = not (raw.get("recap") or {}).get("paragraph")
        if fresh:
            facts = usable_facts(c, raw.get("flags", {}).get("drop", []))
            facts |= {k: g[k] for k in ("away_rank", "home_rank", "venue_city") if g.get(k)}
            try:
                raw["recap"] = game_recap(client, facts, path.name, OPENERS[i % len(OPENERS)], used,
                                          EXAMPLE_KIDS[(i + 3) % len(EXAMPLE_KIDS)],
                                          EXAMPLE_TOPICS[(i * 5 + 2) % len(EXAMPLE_TOPICS)], league=LEAGUE)
            except Exception as ex:  # one failed call skips this game, not the whole week
                log_drop(path.name, "cfb.recap", f"{type(ex).__name__}: {ex}", {})
                continue
        path.write_text(json.dumps(raw, indent=2))
        e = _entry(g, raw, fresh) | {
            "away_score": c.get("away_score"), "home_score": c.get("home_score"),
            "away_record": c.get("away_record_after"), "home_record": c.get("home_record_after"),
            "_players": [p["name"] for p in usable_facts(c, raw.get("flags", {}).get("drop", [])).get("top_performers", [])],
        }
        used += [w["word"] for w in e["big_words"]]
        out.append(e)
    return out


def build_next_week(client: OpenAI, season: int, week: int, used: list[str], processor: str = "base") -> list[dict]:
    out = []
    for i, g in enumerate(g for g in week_games(season, week) if not g["completed"]):
        path = fact_path(game_input(g["away_team"], g["home_team"], g["game_date"], season, week),
                         processor, "preview", RAW_GAMES)
        if not path.exists():
            print(f"missing preview fact sheet: {path.name}")
            continue
        raw = json.loads(path.read_text())
        c = raw["content"] or {}
        if c.get("game_was_played"):
            continue
        groups = [grp for t in (g["away_id"], g["home_id"]) for grp in _roster(t)]
        unavailable = [a["displayName"] for grp in groups if grp.get("position") in ("injuredReserveOrOut", "suspended")
                       for a in grp["items"]]
        fixes = fix_fact_sheet(c, [a["displayName"] for grp in groups for a in grp["items"]])
        dropped = drop_unavailable(c, unavailable)
        if fixes or dropped:
            raw.setdefault("auto_corrections", []).append({"names": fixes, "dropped_unavailable": dropped})
            raw.pop("recap", None)
        fresh = not (raw.get("recap") or {}).get("paragraph")
        if fresh:
            facts = usable_facts(c, raw.get("flags", {}).get("drop", []))
            facts |= {"away_record": g["away_record"] or facts.get("away_record"),
                      "home_record": g["home_record"] or facts.get("home_record"), "game_day": g["game_day"]}
            facts |= {k: g[k] for k in ("away_rank", "home_rank", "venue_city") if g.get(k)}
            try:
                raw["recap"] = game_preview(client, facts, path.name, PREVIEW_OPENERS[i % len(PREVIEW_OPENERS)],
                                            used, EXAMPLE_KIDS[(i + 11) % len(EXAMPLE_KIDS)],
                                            EXAMPLE_TOPICS[(i * 3 + 7) % len(EXAMPLE_TOPICS)],
                                            PREVIEW_CLOSERS[(i + 1) % len(PREVIEW_CLOSERS)], league=LEAGUE)
            except Exception as ex:
                log_drop(path.name, "cfb.preview", f"{type(ex).__name__}: {ex}", {})
                continue
        path.write_text(json.dumps(raw, indent=2))
        e = _entry(g, raw, fresh) | {
            "away_score": None, "home_score": None,
            "away_record": g["away_record"], "home_record": g["home_record"],
            "_players": [p["name"] for p in usable_facts(c, raw.get("flags", {}).get("drop", [])).get("players_to_watch", [])],
        }
        used += [w["word"] for w in e["big_words"]]
        out.append(e)
    return out


# --- player cards (two per game) ---------------------------------------------------------------------

def _where(name: str, text: str) -> int | None:
    """Position of the player's full name in the text (suffix optional), or None."""
    short = re.sub(r" (Jr\.|Sr\.|II|III|IV)$", "", name).replace("’", "'")
    m = re.search(r"\b" + re.escape(short) + r"\b", text.replace("’", "'"))
    return m.start() if m else None


def pick_players(g: dict) -> list[dict]:
    """Up to two players per game, from anyone on either roster (or the box score) named in full in the paragraph.

    Order: a Florida or Kentucky player first, then the fact sheet's top performers (recap) or players to
    watch (preview), then whoever is named first in the paragraph.
    """
    named = []
    box = _box_score(g["espn_id"]) if g.get("away_score") is not None else {}
    for side in ("away", "home"):
        team = {"team_abbr": g[f"{side}_abbr"], "team_name": g[f"{side}_team"]}
        on_roster = set()
        for grp in _roster(g[f"{side}_id"]):
            for a in grp["items"]:
                on_roster.add(a["id"])
                at = _where(a["displayName"], g["paragraph"])
                if at is not None:
                    named.append((at, a | team))
        # The roster feed can miss players (e.g. a starting QB); the box score has everyone who played.
        for aid, name in dict(box.get(g[f"{side}_abbr"], [])).items():
            at = _where(name, g["paragraph"])
            if aid not in on_roster and at is not None:
                named.append((at, _athlete(aid) | team))
    featured = {n: i for i, n in enumerate(dict.fromkeys(g["_players"]))}
    named.sort(key=lambda x: (x[1]["team_abbr"] not in PINNED,
                              featured.get(x[1]["displayName"], len(featured)), x[0]))
    out = list({a["id"]: a for _, a in named}.values())  # one entry per player, order kept
    return out[:CARDS_PER_GAME]


def build_cards(season: int, games: list[dict]) -> dict:
    p_client, o_client = Parallel(), OpenAI()
    chosen = {}
    for g in games:
        for a in pick_players(g):
            chosen.setdefault(a["id"], a)

    def one(a):
        try:
            return build_card(p_client, o_client, a, season, False, LEAGUE, CARDS_DIR, RAW_PLAYERS)
        except Exception as e:
            log_drop(slugify(a["displayName"]), "cfb.players", f"{type(e).__name__}: {e}", {})
            return {"name": a["displayName"], "error": f"{type(e).__name__}: {e}"}

    existing = {p.stem for p in RAW_PLAYERS.glob("*.json")}
    with ThreadPoolExecutor(8) as pool:
        cards = list(pool.map(one, chosen.values()))
    ok = [c for c in cards if "error" not in c]
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (CARDS_DIR / "_index.json").write_text(json.dumps({c["name"]: slugify(c["name"]) for c in ok}, indent=2))
    new = [c for c in ok if f"{slugify(c['name'])}-base" not in existing]
    return {"cards": len(ok), "new_research": len(new), "errors": [c["name"] for c in cards if "error" in c],
            "parallel_usd": round(len(new) * 0.01, 2)}


# --- entry points ------------------------------------------------------------------------------------

def write_week(season: int, week: int) -> tuple[dict, list[dict]]:
    client = OpenAI()
    used: list[str] = []
    last = build_last_week(client, season, week, used)
    nxt = build_next_week(client, season, week + 1, used)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, items in (("last_week.json", last), ("next_week.json", nxt)):
        (OUT_DIR / name).write_text(json.dumps(
            [{k: v for k, v in g.items() if k not in ("usage", "_players")} for g in items if g["paragraph"]],
            indent=2))
    (OUT_DIR / "rankings.json").write_text(json.dumps(ap_top25(), indent=2))
    (OUT_DIR / "edition.json").write_text(json.dumps({"week": week}))
    every = last + nxt
    tin = sum(u["input"] for g in every for u in g["usage"])
    tout = sum(u["output"] for g in every for u in g["usage"])
    summary = {"recaps": f"{sum(1 for g in last if g['paragraph'])}/{len(last)}",
               "previews": f"{sum(1 for g in nxt if g['paragraph'])}/{len(nxt)}",
               "dropped": [f"{g['away_abbr']}@{g['home_abbr']}" for g in every if not g["paragraph"]],
               "openai_usd": round(tin * 1e-6 + tout * 5e-6, 4)}
    return summary, [g for g in every if g["paragraph"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, help="last completed week (default: auto-detect)")
    ap.add_argument("--list", action="store_true", help="only list the games that would be covered")
    args = ap.parse_args()
    load_dotenv(DATA_DIR.parent / ".env")
    week = args.week or latest_completed_week(args.season)
    if args.list:
        for wk in (week, week + 1):
            print(f"week {wk}:")
            for g in week_games(args.season, wk):
                rk = lambda s: f"#{g[s + '_rank']} " if g[s + "_rank"] else ""
                print(f"  {rk('away')}{g['away_team']} at {rk('home')}{g['home_team']}  {g['game_day']}"
                      f"  {'final' if g['completed'] else 'upcoming'}")
        return
    client = Parallel()
    print("recap facts", collect_facts(client, args.season, week, "recap"))
    print("preview facts", collect_facts(client, args.season, week + 1, "preview"))
    summary, games = write_week(args.season, week)
    print("write", summary)
    print("cards", build_cards(args.season, games))


if __name__ == "__main__":
    main()
