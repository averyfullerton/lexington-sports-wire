"""Weekly refresh: run the whole pipeline in order. Meant for Tuesday morning, after Monday Night Football.

    uv run python -m pipeline.refresh            # auto-detect the week
    uv run python -m pipeline.refresh --week 10  # or pick one

Steps (each reuses earlier results, so a re-run only pays for what is new):
  1. game facts for last week's games (Parallel Task)
  2. preview facts for next week's games (Parallel Task)
  3. recaps + previews + standings (OpenAI)  -> data/last_week.json, next_week.json, standings.json
  4. player cards (ESPN + Parallel Task + OpenAI) -> data/players/
  5. 5 Big Stories (Search, Extract, Decisions, OpenAI) -> data/stories.json
  6. college football: Florida, Kentucky and AP Top 10 games, facts + writing + two player cards per game
     (see pipeline/cfb.py) -> data/cfb/
A summary goes to data/refresh_log.jsonl. If a step fails, later steps still run on what exists.
"""

import argparse
import json
import time
import traceback
from datetime import datetime

from dotenv import load_dotenv
from parallel import Parallel

from pipeline.config import DATA_DIR

PARALLEL_BASE_USD = 0.01  # per Task run on the base processor


def clean_keys() -> None:
    """Strip stray whitespace/newlines/quotes around API keys (e.g. from a secret set via a pipe).

    A newline inside a key makes the HTTP client reject the request before it is sent, which the SDKs
    report as a "connection error". Only reports whether a key needed cleaning, never the key.
    """
    import os

    for name in ("PARALLEL_API_KEY", "OPENAI_API_KEY"):
        raw = os.environ.get(name)
        if raw is None:
            print(f"{name}: missing")
            continue
        clean = raw.strip().strip("'\"").strip()
        if clean != raw:
            os.environ[name] = clean
            print(f"{name}: removed stray whitespace or quotes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, help="last completed week (default: auto-detect)")
    ap.add_argument("--processor", default="base")
    args = ap.parse_args()
    load_dotenv(DATA_DIR.parent / ".env")
    clean_keys()

    from pipeline import build, games, players, schedule, stories

    week = args.week or schedule.latest_completed_week(args.season)
    since = min(g["game_date"] for g in schedule.week_games(args.season, week))
    log = {"started": datetime.now().isoformat(timespec="seconds"), "season": args.season, "week": week,
           "steps": {}}
    print(f"Refreshing: recaps for week {week}, previews for week {week + 1}, news since {since}")
    client = Parallel()

    def step(name, fn):
        t0 = time.monotonic()
        try:
            out = fn()
            log["steps"][name] = {"ok": True, "seconds": round(time.monotonic() - t0), **(out or {})}
        except Exception as e:
            log["steps"][name] = {"ok": False, "seconds": round(time.monotonic() - t0),
                                  "error": f"{type(e).__name__}: {e}"}
            traceback.print_exc()
        print(f"[{name}] {log['steps'][name]}")

    def facts(kind, wk):
        def run():
            res = games.collect_week(client, args.season, wk, args.processor, kind, skip_existing=True)
            new = [r for r in res if not r.get("cached")]
            return {"games": len(res), "new_runs": len(new),
                    "errors": [r["input"]["home_team"] for r in res if r.get("status") == "error"],
                    "parallel_usd": round(len(new) * PARALLEL_BASE_USD, 2)}
        return run

    def player_cards():
        existing = {p.stem for p in (DATA_DIR / "raw" / "players").glob("*.json")}
        cards = players.build_all(args.season, None, rewrite=False)
        new = [c for c in cards if "error" not in c and f"{players.slugify(c['name'])}-base" not in existing]
        return {"cards": len(cards), "new_research": len(new),
                "errors": [c["name"] for c in cards if "error" in c],
                "parallel_usd": round(len(new) * PARALLEL_BASE_USD, 2)}

    def big_stories():
        s = stories.build(since)
        return {"stories": len(s), "titles": [x["title"] for x in s]}

    step("game_facts", facts("recap", week))
    step("preview_facts", facts("preview", week + 1))
    step("write", lambda: build.build(args.season, week, args.processor))
    step("player_cards", player_cards)
    step("stories", big_stories)

    from pipeline import cfb
    cfb_week = None
    cfb_games: list[dict] = []

    def cfb_facts():
        nonlocal cfb_week
        cfb_week = cfb.latest_completed_week(args.season)
        recap = cfb.collect_facts(client, args.season, cfb_week, "recap", args.processor)
        preview = cfb.collect_facts(client, args.season, cfb_week + 1, "preview", args.processor)
        return {"week": cfb_week, "recap": recap, "preview": preview}

    def cfb_write():
        nonlocal cfb_games
        summary, cfb_games = cfb.write_week(args.season, cfb_week)
        return summary

    step("cfb_facts", cfb_facts)
    if cfb_week:
        step("cfb_write", cfb_write)
        step("cfb_player_cards", lambda: cfb.build_cards(args.season, cfb_games))

    log["finished"] = datetime.now().isoformat(timespec="seconds")
    log["ok"] = all(s["ok"] for s in log["steps"].values())
    with open(DATA_DIR / "refresh_log.jsonl", "a") as f:
        f.write(json.dumps(log) + "\n")
    print("DONE" if log["ok"] else "FINISHED WITH ERRORS", "- see data/refresh_log.jsonl")


if __name__ == "__main__":
    main()
