"""Game facts via Parallel Task API + JSON schema.

For each completed game, Parallel researches the allowlisted sites and fills
GAME_FACTS_SCHEMA. Every field comes back with a `basis` (citations, reasoning,
confidence). Low-confidence fields and off-allowlist citations are flagged so the
writing step can leave them out.

Try one game with several processors:
    uv run python -m pipeline.games --away "Jacksonville Jaguars" \
        --home "Tennessee Titans" --date 2026-11-15 --week 10 --processors base core
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from parallel import APIStatusError, Parallel

from pipeline.config import DATA_DIR, allowed_domains, is_allowed_url

RAW_DIR = DATA_DIR / "raw" / "games"
RESULT_WAIT_S = 600  # per blocking result call; 408 means "still running, poll again"
MAX_WAIT_S = 3600

_player = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Player's name as the roster lists it. If a source only gives an initial (e.g. 'K.Concepcion'), find the real name; never guess a first name from an initial"},
        "team": {"type": "string", "description": "Player's team name"},
        "stat_line": {
            "type": "string",
            "description": "This game's key stats exactly as in the box score, e.g. '25 of 35, 352 yards, 3 TD, 1 INT'",
        },
    },
    "required": ["name", "team", "stat_line"],
    "additionalProperties": False,
}

GAME_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "game_was_played": {
            "type": "boolean",
            "description": "True only if this exact game has been played to a final score. If false, leave every other field null or empty.",
        },
        "away_team": {"type": "string", "description": "Away team full name"},
        "home_team": {"type": "string", "description": "Home team full name"},
        "away_score": {"type": ["integer", "null"], "description": "Away team's final score"},
        "home_score": {"type": ["integer", "null"], "description": "Home team's final score"},
        "away_record_after": {
            "type": ["string", "null"],
            "description": "Away team's win-loss(-tie) record after this game, e.g. '7-2' or '3-5-1'",
        },
        "home_record_after": {
            "type": ["string", "null"],
            "description": "Home team's win-loss(-tie) record after this game",
        },
        "top_performers": {
            "type": "array",
            "description": "The 3 to 5 best individual performances in this game, from either team",
            "items": _player,
        },
        "key_plays": {
            "type": "array",
            "description": "3 to 5 most important plays in order, each with the quarter, e.g. 'Q4 0:57: Jayden Daniels 32-yard TD pass to Stefon Diggs'. Use players' real names; never expand an initial into a guessed first name",
            "items": {"type": "string"},
        },
        "tough_day": {
            "type": "array",
            "description": "Players or units who struggled in this game, with the stat that shows it (e.g. '2 interceptions'). Facts only, no opinions.",
            "items": _player,
        },
        "storyline": {
            "type": ["string", "null"],
            "description": "Why this game matters, in one or two factual sentences: standings, streaks, milestones, debuts or trades",
        },
    },
    "required": [
        "game_was_played", "away_team", "home_team", "away_score", "home_score",
        "away_record_after", "home_record_after", "top_performers", "key_plays",
        "tough_day", "storyline",
    ],
    "additionalProperties": False,
}


PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "game_was_played": {
            "type": "boolean",
            "description": "True if this game has already been played. It should be false for a preview.",
        },
        "away_team": {"type": "string", "description": "Away team full name"},
        "home_team": {"type": "string", "description": "Home team full name"},
        "away_record": {"type": ["string", "null"], "description": "Away team's current win-loss(-tie) record before this game, e.g. '7-2'"},
        "home_record": {"type": ["string", "null"], "description": "Home team's current win-loss(-tie) record before this game"},
        "players_to_watch": {
            "type": "array",
            "description": "2 to 4 players to watch (at least one per team), with this season's stats so far",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Player's name as the roster lists it; never guess a first name from an initial"},
                    "team": {"type": "string"},
                    "stat_line": {"type": "string", "description": "This season's key stats so far, e.g. '2,284 passing yards, 21 TD'"},
                },
                "required": ["name", "team", "stat_line"],
                "additionalProperties": False,
            },
        },
        "matchup_notes": {
            "type": "array",
            "description": "3 to 5 factual notes about THIS season: streaks, division standings, last week's results, debuts. No history before this season, no betting odds, no predictions.",
            "items": {"type": "string"},
        },
        "storyline": {"type": ["string", "null"], "description": "Why this game matters, in one or two factual sentences. No predictions."},
    },
    "required": ["game_was_played", "away_team", "home_team", "away_record", "home_record",
                 "players_to_watch", "matchup_notes", "storyline"],
    "additionalProperties": False,
}


LEAGUE_NAMES = {"nfl": "NFL", "cfb": "college football (FBS)"}


def task_spec(kind: str = "recap", league: str = "nfl") -> dict:
    return {
        "input_schema": {
            "type": "json",
            "json_schema": {
                "type": "object",
                "properties": {
                    "away_team": {"type": "string"},
                    "home_team": {"type": "string"},
                    "game_date": {"type": "string", "description": "Local game date, YYYY-MM-DD"},
                    "season": {"type": "integer"},
                    "week": {"type": "integer", "description": f"{LEAGUE_NAMES[league]} regular-season week number"},
                },
            },
        },
        "output_schema": {"type": "json", "json_schema": PREVIEW_SCHEMA if kind == "preview" else GAME_FACTS_SCHEMA},
    }


def game_input(away: str, home: str, date: str, season: int, week: int) -> dict:
    return {"away_team": away, "home_team": home, "game_date": date, "season": season, "week": week}


def slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")


def run_task(client: Parallel, inp: dict, processor: str, kind: str = "recap", league: str = "nfl") -> dict:
    """Create one Task run and wait for it. Returns a plain dict for storage."""
    started = time.monotonic()
    run = client.task_run.create(
        input=inp,
        task_spec=task_spec(kind, league),
        processor=processor,
        source_policy={"include_domains": allowed_domains()},
        metadata={"app": "lexington-sports-wire", "step": f"{league}_game_{kind}"},
    )
    while True:
        try:
            result = client.task_run.result(run.run_id, api_timeout=RESULT_WAIT_S)
            break
        except APIStatusError as e:
            if e.status_code == 408 and time.monotonic() - started < MAX_WAIT_S:
                continue
            raise
    out = result.output
    return {
        "run_id": run.run_id,
        "processor": processor,
        "status": result.run.status,
        "elapsed_s": round(time.monotonic() - started, 1),
        "input": inp,
        "content": normalize(out.content) if out else None,
        "basis": [b.model_dump() for b in out.basis] if out else [],
    }


def flag_basis(basis: list[dict]) -> dict:
    """Per CLAUDE.md: drop low-confidence fields; medium is kept but noted for review.

    A field with no confidence rating, or citing any non-allowlisted site, is also dropped.
    """
    conf = lambda b: (b.get("confidence") or "").lower()
    off_list = sorted({
        c["url"] for b in basis for c in (b.get("citations") or []) if not is_allowed_url(c["url"])
    })
    off_fields = {
        b["field"] for b in basis
        if any(not is_allowed_url(c["url"]) for c in (b.get("citations") or []))
    }
    drop = sorted({b["field"] for b in basis if conf(b) in ("low", "")} | off_fields)
    medium = sorted({b["field"] for b in basis if conf(b) == "medium"})
    return {"drop": drop, "medium_confidence": medium, "off_allowlist_citations": off_list}


def normalize(content: dict | None) -> dict | None:
    """The SDK returns JSON numbers as floats; scores are whole numbers."""
    if not content:
        return content
    for k in ("away_score", "home_score"):
        if isinstance(content.get(k), float) and content[k].is_integer():
            content[k] = int(content[k])
    return content


def fact_path(inp: dict, processor: str, kind: str = "recap", raw_dir=RAW_DIR):
    tag = "" if kind == "recap" else f"-{kind}"
    return raw_dir / f"{slug(inp['away_team'], 'at', inp['home_team'], inp['game_date'])}{tag}-{processor}.json"


def collect_game(client: Parallel, inp: dict, processor: str, kind: str = "recap", league: str = "nfl",
                 raw_dir=RAW_DIR) -> dict:
    res = run_task(client, inp, processor, kind, league)
    res["flags"] = flag_basis(res["basis"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = fact_path(inp, processor, kind, raw_dir)
    path.write_text(json.dumps(res, indent=2))
    res["path"] = str(path)
    return res


def collect_week(client: Parallel, season: int, week: int, processor: str, kind: str = "recap",
                 skip_existing: bool = False) -> list[dict]:
    """Research a week in parallel: completed games (recap) or upcoming games (preview)."""
    from pipeline.schedule import week_games

    want_completed = kind == "recap"
    games = [g for g in week_games(season, week) if g["completed"] == want_completed]

    def one(g: dict) -> dict:
        inp = game_input(g["away_team"], g["home_team"], g["game_date"], season, week)
        path = fact_path(inp, processor, kind)
        if skip_existing and path.exists():
            return {**json.loads(path.read_text()), "game": g, "cached": True}
        try:
            res = collect_game(client, inp, processor, kind)
        except Exception as e:  # keep the rest of the week going; report the failure
            res = {"status": "error", "error": f"{type(e).__name__}: {e}", "input": inp}
        return {**res, "game": g}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, games))


def collect_last_week() -> list[dict]:
    raise NotImplementedError


def collect_next_week() -> list[dict]:
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--away")
    ap.add_argument("--home")
    ap.add_argument("--date")
    ap.add_argument("--all-games", action="store_true", help="every game in --week (completed for recap, upcoming for preview)")
    ap.add_argument("--kind", choices=["recap", "preview"], default="recap")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--processors", nargs="+", default=["core"])
    args = ap.parse_args()

    load_dotenv()
    client = Parallel()
    if args.all_games:
        results = collect_week(client, args.season, args.week, args.processors[0], args.kind)
        for r in results:
            g = r["game"]
            line = f"{g['away_abbr']}@{g['home_abbr']}: {r['status']}"
            line += f" {r.get('elapsed_s', '')}s drop={r['flags']['drop']}" if "flags" in r else f" {r.get('error')}"
            print(line)
        return
    if not (args.away and args.home and args.date):
        ap.error("--away, --home and --date are required unless --all-games")
    inp = game_input(args.away, args.home, args.date, args.season, args.week)
    with ThreadPoolExecutor(len(args.processors)) as pool:
        results = list(pool.map(lambda p: collect_game(client, inp, p), args.processors))
    for r in results:
        print(f"{r['processor']}: {r['status']} in {r['elapsed_s']}s -> {r['path']}")
        print(f"  flags: {r['flags']}")


if __name__ == "__main__":
    main()
