"""Player cards: ESPN for bio and stats, Parallel Task API for fun facts, OpenAI to write them for kids.

    uv run python -m pipeline.players --list      # who would get a card
    uv run python -m pipeline.players --build     # research + write cards into data/players/
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from parallel import APIStatusError, Parallel
from pydantic import BaseModel

from pipeline.config import DATA_DIR, allowed_domains
from pipeline.games import RESULT_WAIT_S, MAX_WAIT_S, flag_basis
from pipeline.safety import log_drop, tripwire
from pipeline.write import MODEL

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{abbr}/roster"
STATS = "https://site.web.api.espn.com/apis/common/v3/sports/football/{league}/athletes/{id}/stats"
ESPN_LEAGUE = {"nfl": "nfl", "cfb": "college-football"}
PLAYERS_DIR = DATA_DIR / "players"
RAW_DIR = DATA_DIR / "raw" / "players"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _roster(abbr: str) -> list[dict]:
    r = httpx.get(ROSTER.format(abbr=abbr.lower()), timeout=30)
    r.raise_for_status()
    return [a | {"team_abbr": abbr} for g in r.json()["athletes"] for a in g["items"]]


def mentioned_players() -> list[dict]:
    """Players named (full name) in this week's recaps and previews, matched to ESPN roster entries."""
    games = json.loads((DATA_DIR / "last_week.json").read_text()) + json.loads((DATA_DIR / "next_week.json").read_text())
    teams = sorted({g["away_abbr"] for g in games} | {g["home_abbr"] for g in games})
    with ThreadPoolExecutor(8) as pool:
        rosters = dict(zip(teams, pool.map(_roster, teams)))
    found = {}
    for g in games:
        text = g["paragraph"].replace("’", "'")
        for abbr in (g["away_abbr"], g["home_abbr"]):
            for a in rosters[abbr]:
                name = a["displayName"].replace("’", "'")
                short = re.sub(r" (Jr\.|Sr\.|II|III|IV)$", "", name)
                if re.search(r"\b" + re.escape(short) + r"\b", text):
                    found[a["id"]] = a
    return sorted(found.values(), key=lambda a: a["displayName"])


FUN_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "college": {"type": ["string", "null"], "description": "College he played for (last one if several)"},
        "draft": {"type": ["string", "null"], "description": "NFL draft year, round and pick, e.g. '2021, round 1, pick 1', or 'Undrafted'"},
        "fun_facts": {
            "type": "array",
            "description": (
                "4 to 6 true, surprising, kid-friendly facts, one per item: hometown and childhood, other sports he "
                "played, college feats, nicknames, hobbies, pets, records and milestones. NOT allowed: injuries or "
                "health, arrests or legal trouble, suspensions, contracts or money, gambling, politics, religion, "
                "relationships or anything sad. Use the player's real name; never guess."
            ),
            "items": {"type": "string"},
        },
    },
    "required": ["college", "draft", "fun_facts"],
    "additionalProperties": False,
}


CFB_FUN_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "hometown": {"type": ["string", "null"], "description": "Hometown and state"},
        "high_school": {"type": ["string", "null"], "description": "High school he played for"},
        "fun_facts": {
            "type": "array",
            "description": (
                "4 to 6 true, surprising, kid-friendly facts about this college football player, one per item: "
                "hometown and childhood, other sports he played, high school feats, college records and "
                "milestones, nicknames, hobbies, pets, what he studies. NOT allowed: injuries or health, arrests or "
                "legal trouble, suspensions, NIL deals, contracts or money, recruiting rankings, the transfer "
                "portal, gambling, politics, religion, relationships or anything sad. Use the player's real name; "
                "never guess."
            ),
            "items": {"type": "string"},
        },
    },
    "required": ["hometown", "high_school", "fun_facts"],
    "additionalProperties": False,
}


def research(client: Parallel, a: dict, season: int, processor: str = "base", league: str = "nfl",
             raw_dir=RAW_DIR) -> dict:
    """Parallel Task run for one player's fun facts. Cached in data/raw/players/ (or raw_dir)."""
    path = raw_dir / f"{slugify(a['displayName'])}-{processor}.json"
    if path.exists():
        return json.loads(path.read_text())
    inp = {"player": a["displayName"], "team": a["team_name"], "position": a["position"]["displayName"],
           "season": season}
    run = client.task_run.create(
        input=inp,
        task_spec={"output_schema": {"type": "json", "json_schema":
                                     CFB_FUN_FACTS_SCHEMA if league == "cfb" else FUN_FACTS_SCHEMA}},
        processor=processor,
        source_policy={"include_domains": allowed_domains()},
        metadata={"app": "lexington-sports-wire", "step": "player_card" if league == "nfl" else f"{league}_player_card"},
    )
    started = time.monotonic()
    while True:
        try:
            res = client.task_run.result(run.run_id, api_timeout=RESULT_WAIT_S)
            break
        except APIStatusError as e:
            if e.status_code == 408 and time.monotonic() - started < MAX_WAIT_S:
                continue
            raise
    out = res.output
    raw = {"run_id": run.run_id, "processor": processor, "status": res.run.status,
           "elapsed_s": round(time.monotonic() - started, 1), "input": inp,
           "content": out.content if out else None,
           "basis": [b.model_dump() for b in out.basis] if out else []}
    raw["flags"] = flag_basis(raw["basis"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2))
    return raw


def usable_fun_facts(raw: dict) -> list[str]:
    """Fun facts minus any element Parallel rated low confidence or cited off the allowlist."""
    drop = set(raw.get("flags", {}).get("drop", []))
    if "fun_facts" in drop:
        return []
    facts = (raw.get("content") or {}).get("fun_facts") or []
    return [f for i, f in enumerate(facts) if f"fun_facts.{i}" not in drop]


class KidFacts(BaseModel):
    facts: list[str]


KID_FACTS_INSTRUCTIONS = """\
You write fun facts about NFL players for a 6-year-old superfan who reads at a first-grade level.

From the facts given, pick the 3 most fun and surprising ones and rewrite each as ONE short
sentence (under 16 words), with first-grade words. Start with the player's first name or "He".
- He knows football: use football words (interception, pick-six, field goal, sack) as they are.
  Never swap them for vaguer words that change the meaning.
- Say where it happened when the fact says (in high school, in college, in the NFL).
- Use ONLY what the facts say. Never add a detail, number or name.
- Skip any fact about injuries, health, arrests, legal trouble, suspensions, contracts or money,
  gambling, politics, religion, relationships, or anything sad. Do not soften it; leave it out.
- If fewer than 3 facts are fun and safe, return fewer. Plain text, no emoji.
"""


def kid_facts(client: OpenAI, name: str, facts: list[str], league: str = "nfl") -> list[str]:
    if not facts:
        return []
    instructions = KID_FACTS_INSTRUCTIONS if league == "nfl" else (
        KID_FACTS_INSTRUCTIONS.replace("NFL players", "college football players")
        .replace("(in high school, in college, in the NFL)", "(in high school, in college)")
        .replace("contracts or money,", "contracts, NIL deals or money, recruiting rankings, the transfer portal,"))
    resp = client.responses.parse(
        model=MODEL, instructions=instructions, reasoning={"effort": "low"},
        input=f"Player: {name}\nFacts:\n" + "\n".join(f"- {f}" for f in facts),
        text_format=KidFacts, store=False)
    keep = []
    for f in resp.output_parsed.facts[:3]:
        hits = tripwire(f)
        if hits or len(f.split()) > 20:
            log_drop(slugify(name), "players.kid_facts", f"tripwire {hits}" if hits else "too long", {"fact": f})
            continue
        keep.append(f)
    return keep


# Which stats table to show, by position, and which columns of it.
TABLES = {
    "QB": ("passing", ["GP", "CMP", "ATT", "YDS", "TD", "INT"]),
    "RB": ("rushing", ["GP", "CAR", "YDS", "AVG", "TD"]),
    "FB": ("rushing", ["GP", "CAR", "YDS", "TD"]),
    "WR": ("receiving", ["GP", "REC", "YDS", "TD", "LNG"]),
    "TE": ("receiving", ["GP", "REC", "YDS", "TD", "LNG"]),
    "PK": ("kicking", ["GP", "FG", "FG%", "LNG", "PTS"]),
    "K": ("kicking", ["GP", "FG", "FG%", "LNG", "PTS"]),
    "P": ("punting", ["GP", "PUNTS", "YDS", "AVG", "LNG"]),
}
DEFENSE = ("defensive", ["GP", "TOT", "SACK", "INT", "FF"])
LABELS = {"GP": "Games", "CMP": "Completions", "ATT": "Attempts", "YDS": "Yards", "TD": "TDs", "INT": "INTs",
          "CAR": "Carries", "AVG": "Avg", "REC": "Catches", "LNG": "Longest", "FG": "Field goals",
          "FG%": "FG %", "PTS": "Points", "PUNTS": "Punts", "TOT": "Tackles", "SACK": "Sacks", "FF": "Forced fumbles"}


def season_stats(athlete_id: str, position: str, league: str = "nfl") -> dict | None:
    r = httpx.get(STATS.format(league=ESPN_LEAGUE[league], id=athlete_id), timeout=30)
    r.raise_for_status()
    d = r.json()
    cat_name, cols = TABLES.get(position, DEFENSE)
    cat = next((c for c in d.get("categories", []) if c["name"] == cat_name), None)
    if not cat:
        return None
    labels = [l.strip() for l in cat["labels"]]
    idx = [labels.index(c) for c in cols if c in labels]  # first match (defense has two YDS/INT-adjacent cols)
    teams = {slug: t.get("abbreviation", "") for slug, t in d.get("teams", {}).items()}
    rows = []
    for s in cat["statistics"]:
        rows.append([str(s["season"]["year"]), teams.get(s.get("teamSlug"), "")] + [s["stats"][i] for i in idx])
    return {"kind": cat_name, "columns": ["Season", "Team"] + [LABELS.get(labels[i], labels[i]) for i in idx],
            "rows": rows}


def build_card(p_client: Parallel, o_client: OpenAI, a: dict, season: int, rewrite: bool, league: str = "nfl",
               cards_dir=PLAYERS_DIR, raw_dir=RAW_DIR) -> dict:
    raw = research(p_client, a, season, league=league, raw_dir=raw_dir)
    card_path = cards_dir / f"{slugify(a['displayName'])}.json"
    old = json.loads(card_path.read_text()) if card_path.exists() else {}
    facts = (old.get("fun_facts") if old and not rewrite
             else kid_facts(o_client, a["displayName"], usable_fun_facts(raw), league))
    pos = a["position"]["abbreviation"]
    card = {
        "name": a["displayName"], "espn_id": a["id"], "team": a["team_name"], "team_abbr": a["team_abbr"],
        "number": a.get("jersey"), "position": a["position"]["displayName"], "position_abbr": pos,
        "college": (a.get("college") or {}).get("name") if league == "nfl" else None,
        "experience": (a.get("experience") or {}).get("years") if league == "nfl" else None,
        "class_year": (a.get("experience") or {}).get("displayValue") if league == "cfb" else None,
        "hometown": (a.get("birthPlace") or {}).get("displayText") if league == "cfb" else None,
        "league": league,
        "fun_facts": facts,
        "stats": season_stats(a["id"], pos, league),
    }
    cards_dir.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2))
    return card | {"_raw": raw}


def build_all(season: int, only: list[str] | None, rewrite: bool) -> list[dict]:
    load_dotenv(DATA_DIR.parent / ".env")
    ps = mentioned_players()
    if only:
        ps = [p for p in ps if p["displayName"] in only]
    teams = httpx.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams").json()
    names = {t["team"]["abbreviation"]: t["team"]["displayName"] for t in teams["sports"][0]["leagues"][0]["teams"]}
    for p in ps:
        p["team_name"] = names[p["team_abbr"]]
    p_client, o_client = Parallel(), OpenAI()

    def one(a):
        try:
            return build_card(p_client, o_client, a, season, rewrite)
        except Exception as e:
            log_drop(slugify(a["displayName"]), "players.build", f"{type(e).__name__}: {e}", {})
            return {"name": a["displayName"], "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(8) as pool:
        cards = list(pool.map(one, ps))
    index = {c["name"]: slugify(c["name"]) for c in cards if "error" not in c}
    (PLAYERS_DIR / "_index.json").write_text(json.dumps(index, indent=2))
    return cards


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--only", nargs="*", help="player names to build (default: everyone mentioned)")
    ap.add_argument("--rewrite", action="store_true", help="rewrite kid fun facts even if a card exists")
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()
    if args.build:
        cards = build_all(args.season, args.only, args.rewrite)
        errs = [c for c in cards if "error" in c]
        for c in cards:
            if "error" in c:
                continue
            r = c["_raw"]
            print(f"{c['name']} ({c['team_abbr']} #{c['number']} {c['position_abbr']}) {r.get('elapsed_s')}s "
                  f"drop={r['flags']['drop']} facts={len(c['fun_facts'])} stats_rows={len((c['stats'] or {}).get('rows', []))}")
        print(f"{len(cards) - len(errs)}/{len(cards)} cards; errors: {[(e['name'], e['error']) for e in errs]}")
        return
    if args.list:
        ps = mentioned_players()
        print(len(ps), "players")
        print(", ".join(f"{p['displayName']} ({p['team_abbr']} {p['position']['abbreviation']})" for p in ps))


if __name__ == "__main__":
    main()
