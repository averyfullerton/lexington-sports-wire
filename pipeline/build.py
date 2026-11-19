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
from pipeline.games import RAW_DIR, fact_path, game_input, slug
from pipeline.schedule import week_games
from pipeline.standings import playoff_picture
from pipeline.verify import drop_unavailable, fix_fact_sheet, game_players, team_roster
from pipeline.write import (EXAMPLE_KIDS, EXAMPLE_TOPICS, OPENERS, PREVIEW_CLOSERS, PREVIEW_OPENERS, game_preview,
                            game_recap, usable_facts)


def build_last_week(client: OpenAI, season: int, week: int, processor: str, rewrite: bool,
                    used_words: list[str]) -> list[dict]:
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
    out = []
    for i, g in enumerate(games):
        r = one(i, g)
        if r:
            used_words += [w["word"] for w in r["big_words"]]
            out.append(r)
    return out


def build_next_week(client: OpenAI, season: int, week: int, processor: str, rewrite: bool,
                    used_words: list[str]) -> list[dict]:
    games = [g for g in week_games(season, week) if not g["completed"]]
    records = {t["abbr"]: t["record"] for conf in playoff_picture(season).values()
               for tier in conf.values() for t in tier}
    out = []
    for i, g in enumerate(games):
        inp = game_input(g["away_team"], g["home_team"], g["game_date"], season, week)
        path = fact_path(inp, processor, "preview")
        if not path.exists():
            print(f"missing preview fact sheet: {path.name}")
            continue
        raw = json.loads(path.read_text())
        c = raw["content"]
        if c.get("game_was_played"):
            print(f"{g['away_abbr']}@{g['home_abbr']}: fact sheet says already played; skipping preview")
            continue
        rosters = [team_roster(g["away_abbr"]), team_roster(g["home_abbr"])]
        everyone = [n for r in rosters for n in r["active"] + r["unavailable"]]
        fixes = fix_fact_sheet(c, everyone)
        out_players = drop_unavailable(c, [n for r in rosters for n in r["unavailable"]])
        if fixes or out_players:
            print(f"{g['away_abbr']}@{g['home_abbr']}: corrected names {fixes}; dropped unavailable {out_players}")
            raw.setdefault("auto_corrections", []).append({"names": fixes, "dropped_unavailable": out_players})
            raw.pop("recap", None)
            path.write_text(json.dumps(raw, indent=2))
        fresh = rewrite or not (raw.get("recap") or {}).get("paragraph")
        if fresh:
            facts = usable_facts(c, raw.get("flags", {}).get("drop", []))
            facts["away_record"] = records.get(g["away_abbr"], facts.get("away_record"))
            facts["home_record"] = records.get(g["home_abbr"], facts.get("home_record"))
            facts["game_day"] = g["game_day"]
            raw["recap"] = game_preview(
                client, facts, path.name, PREVIEW_OPENERS[i % len(PREVIEW_OPENERS)], used_words,
                EXAMPLE_KIDS[(i + 7) % len(EXAMPLE_KIDS)], EXAMPLE_TOPICS[(i * 3 + 1) % len(EXAMPLE_TOPICS)],
                PREVIEW_CLOSERS[(i + 3) % len(PREVIEW_CLOSERS)])
            path.write_text(json.dumps(raw, indent=2))
        r = {
            "away_team": g["away_team"], "home_team": g["home_team"],
            "away_abbr": g["away_abbr"], "home_abbr": g["home_abbr"],
            "away_score": None, "home_score": None,
            "away_record": records.get(g["away_abbr"]), "home_record": records.get(g["home_abbr"]),
            "game_date": g["game_date"], "game_day": g["game_day"],
            "paragraph": raw["recap"]["paragraph"],
            "big_words": raw["recap"].get("big_words", []),
            "usage": raw["recap"].get("usage", []) if fresh else [],
        }
        used_words += [w["word"] for w in r["big_words"]]
        out.append(r)
    return out


def build(season: int, week: int, processor: str = "base", rewrite: bool = False) -> dict:
    """Write recaps (week) and previews (week + 1), then save data/*.json. Returns a summary."""
    load_dotenv(DATA_DIR.parent / ".env")
    client = OpenAI()
    used_words: list[str] = []
    games = build_last_week(client, season, week, processor, rewrite, used_words)
    previews = build_next_week(client, season, week + 1, processor, rewrite, used_words)

    def save(name: str, items: list[dict]) -> None:
        (DATA_DIR / name).write_text(json.dumps(
            [{k: v for k, v in g.items() if k != "usage"} for g in items if g["paragraph"]], indent=2))

    save("last_week.json", games)
    save("next_week.json", previews)
    (DATA_DIR / "standings.json").write_text(json.dumps(playoff_picture(season), indent=2))
    every = games + previews
    tin = sum(u["input"] for g in every for u in g["usage"])
    tout = sum(u["output"] for g in every for u in g["usage"])
    return {
        "recaps": f"{sum(1 for g in games if g['paragraph'])}/{len(games)}",
        "previews": f"{sum(1 for g in previews if g['paragraph'])}/{len(previews)}",
        "dropped": [f"{g['away_abbr']}@{g['home_abbr']}" for g in every if not g["paragraph"]],
        "openai_usd": round(tin * 1e-6 + tout * 5e-6, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--processor", default="base")
    ap.add_argument("--rewrite", action="store_true")
    args = ap.parse_args()
    print(build(args.season, args.week, args.processor, args.rewrite))


if __name__ == "__main__":
    main()
