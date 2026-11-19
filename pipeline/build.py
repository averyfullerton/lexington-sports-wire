"""Write the site's data files from saved fact sheets (no new Parallel calls).

    uv run python -m pipeline.build --week 10

- data/last_week.json: one entry per game with score and recap (OpenAI)
- data/standings.json: playoff picture (ESPN standings feed)
Recaps are cached in each fact-sheet file; pass --rewrite to write them again.
"""

import argparse
import json

from dotenv import load_dotenv
from openai import OpenAI

from pipeline.config import DATA_DIR
from pipeline.games import RAW_DIR, slug
from pipeline.schedule import week_games
from pipeline.standings import playoff_picture
from pipeline.verify import fix_fact_sheet, game_players
from pipeline.write import EXAMPLE_KIDS, EXAMPLE_TOPICS, OPENERS, game_recap, usable_facts


def build_last_week(client: OpenAI, season: int, week: int, processor: str, rewrite: bool) -> list[dict]:
    games = [g for g in week_games(season, week) if g["completed"]]

    def one(i: int, g: dict) -> dict | None:
        path = RAW_DIR / f"{slug(g['away_team'], 'at', g['home_team'], g['game_date'])}-{processor}.json"
        if not path.exists():
            print(f"missing fact sheet: {path.name}")
            return None
        raw = json.loads(path.read_text())
        # Fix first names the fact step guessed from gamebook initials (e.g. "A.Armstrong").
        fixes = fix_fact_sheet(raw["content"], game_players(g["espn_id"]))
        if fixes:
            print(f"{g['away_abbr']}@{g['home_abbr']}: corrected names {fixes}")
            raw.setdefault("auto_corrections", []).extend(
                {"from": a, "to": b, "source": "ESPN box score"} for a, b in fixes)
            raw.pop("recap", None)
            path.write_text(json.dumps(raw, indent=2))
        fresh = rewrite or not (raw.get("recap") or {}).get("paragraph")
        if fresh:
            facts = usable_facts(raw["content"], raw.get("flags", {}).get("drop", []))
            raw["recap"] = game_recap(
                client, facts, path.name, OPENERS[i % len(OPENERS)], used_words,
                EXAMPLE_KIDS[i % len(EXAMPLE_KIDS)], EXAMPLE_TOPICS[(i * 5) % len(EXAMPLE_TOPICS)])
            path.write_text(json.dumps(raw, indent=2))
        c = raw["content"]
        return {
            "away_team": g["away_team"], "home_team": g["home_team"],
            "away_abbr": g["away_abbr"], "home_abbr": g["home_abbr"],
            "away_score": c.get("away_score"), "home_score": c.get("home_score"),
            "away_record": c.get("away_record_after"), "home_record": c.get("home_record_after"),
            "game_date": g["game_date"],
            "paragraph": raw["recap"]["paragraph"],
            "big_words": raw["recap"].get("big_words", []),
            "usage": raw["recap"].get("usage", []) if fresh else [],
        }

    # Sequential so each recap knows which big words earlier ones already used.
    used_words: list[str] = []
    out = []
    for i, g in enumerate(games):
        r = one(i, g)
        if r:
            used_words += [w["word"] for w in r["big_words"]]
            out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--processor", default="base")
    ap.add_argument("--rewrite", action="store_true")
    args = ap.parse_args()

    load_dotenv(DATA_DIR.parent / ".env")
    games = build_last_week(OpenAI(), args.season, args.week, args.processor, args.rewrite)
    shown = [g for g in games if g["paragraph"]]
    (DATA_DIR / "last_week.json").write_text(json.dumps(
        [{k: v for k, v in g.items() if k != "usage"} for g in shown], indent=2))
    (DATA_DIR / "standings.json").write_text(json.dumps(playoff_picture(args.season), indent=2))

    tin = sum(u["input"] for g in games for u in g["usage"])
    tout = sum(u["output"] for g in games for u in g["usage"])
    print(f"{len(shown)}/{len(games)} recaps shown; dropped: {[g['home_abbr'] for g in games if not g['paragraph']]}")
    print(f"OpenAI tokens this run: in={tin} out={tout} (~${tin * 1e-6 + tout * 5e-6:.3f} at gpt-6.1-terra rates)")


if __name__ == "__main__":
    main()
