"""5 Big Stories: Search + Extract (allowlist) -> Decisions safety filter -> rank -> kid rewrite.

    uv run python -m pipeline.stories --since 2026-11-12

Every drop is logged to data/drops.jsonl with its reason. The full run (candidates, safety
scores, rankings, drafts) is saved to data/raw/stories/ for review and the safety eval.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
from parallel import Parallel
from pydantic import BaseModel

from pipeline.config import DATA_DIR, allowed_domains, is_allowed_url
from pipeline.safety import classify_story, is_safe, log_drop, tripwire
from pipeline.write import (EXAMPLE_KIDS, EXAMPLE_TOPICS, MODEL, BigWord, Recap,
                            check_recap)

RAW_DIR = DATA_DIR / "raw" / "stories"
MAX_EXTRACT = 60
N_STORIES = 5
MAX_ATTEMPTS = 3

SEARCHES = [
    ("The biggest NFL storylines of the past week that a young fan would enjoy: winning streaks, comebacks, "
     "record-breaking games, rookies shining, rivalries, players facing their old team, players back on the field.",
     ["NFL week 10 takeaways", "NFL winning streak continues", "NFL rookie breakout game",
      "NFL record broken this week", "faces former team NFL week 11"]),
    ("This week's news about the Jacksonville Jaguars and Washington Commanders: wins, streaks, standout players, "
     "the upcoming game.",
     ["Jaguars week 11 news", "Commanders week 11 news", "Jaguars AFC South lead", "Jayden Daniels Commanders"]),
]

# Article pages only; skip URL paths that are almost never kid material.
ARTICLE_PATH = re.compile(r"/(news|story|article)s?/")
SKIP_PATH = re.compile(r"fantasy|odds|betting|bets|picks|injur|arrest|suspend|lawsuit|podcast|video|photos?/|"
                       r"where-to-watch|how-to-watch|stream|tickets|schedule|transactions|press-conference|/series/",
                       re.IGNORECASE)


def game_searches() -> list[tuple[str, list[str]]]:
    """One query per game from last week's results, so single-game stories join the roundups."""
    path = DATA_DIR / "last_week.json"
    if not path.exists():
        return []
    games = json.loads(path.read_text())
    queries = []
    for g in games:
        away_won = (g["away_score"] or 0) > (g["home_score"] or 0)
        w, l = (g["away_team"], g["home_team"]) if away_won else (g["home_team"], g["away_team"])
        queries.append(f"{w.split()[-1]} beat {l.split()[-1]} storylines")
    objective = ("Game stories and storylines from these NFL games: standout players, streaks, comebacks, "
                 "records, rookies, players facing their old team.")
    return [(objective, queries[i:i + 5]) for i in range(0, len(queries), 5)]


def discover(client: Parallel, since: str) -> list[dict]:
    seen, out = set(), []
    for objective, queries in SEARCHES + game_searches():
        res = client.search(
            objective=objective, search_queries=queries, mode="fast",
            advanced_settings={"max_results": 20,
                               "source_policy": {"include_domains": allowed_domains(), "after_date": since}},
        )
        if res.warnings:
            print("search warnings:", res.warnings)
        for r in res.results:
            url = r.url.split("#")[0]
            if url in seen:
                continue
            seen.add(url)
            path = urlparse(url).path
            if not is_allowed_url(url):
                log_drop(url, "stories.search", "off allowlist", {})
            elif not ARTICLE_PATH.search(path):
                continue  # scores pages, team hubs, etc.: not a story
            elif SKIP_PATH.search(path):
                log_drop(url, "stories.search", "url filter", {"title": r.title})
            else:
                out.append({"url": url, "title": r.title, "publish_date": r.publish_date})
    return out


def extract(client: Parallel, cands: list[dict]) -> list[dict]:
    cands = cands[:MAX_EXTRACT]
    by_url = {c["url"]: c for c in cands}
    for i in range(0, len(cands), 20):
        batch = [c["url"] for c in cands[i:i + 20]]
        res = client.extract(urls=batch, objective="The news story: what happened and who was involved.",
                             advanced_settings={"full_content": True})
        for r in res.results:
            c = by_url.get(r.url)
            if c:
                c["title"] = r.title or c["title"]
                c["publish_date"] = r.publish_date or c["publish_date"]
                c["text"] = (r.full_content or "\n".join(r.excerpts))[:20000]
        for e in res.errors:
            log_drop(e.url, "stories.extract", f"extract failed: {e.error_type}", {"status": e.http_status_code})
    return [c for c in cands if c.get("text")]


def safety(cands: list[dict]) -> list[dict]:
    def one(c):
        try:
            c["safety"] = classify_story(c, scope="topic")
        except Exception as e:
            c["safety"] = {"label": "error", "confident": False, "error": f"{type(e).__name__}: {e}"}
        return c

    with ThreadPoolExecutor(8) as pool:
        cands = list(pool.map(one, cands))
    safe = []
    for c in cands:
        s = c["safety"]
        if is_safe(s):
            safe.append(c)
        else:
            log_drop(c["url"], "stories.decisions", s["label"],
                     {"title": c["title"], "probabilities": s.get("probabilities"), "error": s.get("error")})
    return safe


class Pick(BaseModel):
    index: int
    angle: str


class Ranking(BaseModel):
    picks: list[Pick]


RANK_INSTRUCTIONS = """\
You are the editor of an NFL news page for a 6-year-old superfan. From the candidate stories, pick 8,
best first (or every qualifying story if fewer than 8 qualify). Backups matter: some picks may be cut later. A great pick is a fun storyline a kid can follow: a winning streak, a big comeback, a rookie
shining, a record, a rivalry, a player facing his old team, a star back on the field.
- One pick per storyline: if several articles tell the same story, pick the best one.
- Spread the picks across different teams: at most 2 picks may feature the same team. If there is a good
  Jaguars or Commanders story, include it.
- A pick must have a real storyline with facts (who did what). Never pick a schedule, TV listing,
  ticket page, or a story that is only about what might happen next.
- Skip anything about contracts, trades talk, coaching drama, injuries, or who might get fired.
- angle: one sentence on what makes it fun for a kid.
"""


def rank(client: OpenAI, safe: list[dict]) -> list[tuple[dict, str]]:
    listing = "\n\n".join(
        f"[{i}] {c['title']} ({c.get('publish_date') or 'no date'})\n{c['text'][:800]}" for i, c in enumerate(safe))
    resp = client.responses.parse(model=MODEL, instructions=RANK_INSTRUCTIONS, input=listing,
                                  reasoning={"effort": "low"}, text_format=Ranking, store=False)
    out = []
    for p in resp.output_parsed.picks:
        if 0 <= p.index < len(safe) and safe[p.index] not in [c for c, _ in out]:
            out.append((safe[p.index], p.angle))
    return out


EASY_WORDS = {"final", "perfect", "returned", "return", "earned", "nearly", "equal", "happy", "fast",
              "strong", "big", "great", "awesome", "amazing", "exciting", "special", "winning"}


class StoryDraft(BaseModel):
    skip: bool
    skip_reason: str
    title: str
    paragraph: str
    big_words: list[BigWord]


STORY_INSTRUCTIONS = """\
You are a sportswriter with a lively, varied style, writing one of the week's 5 Big Stories for a
6-year-old NFL superfan. He knows football jargon. He reads at a first-grade level and is ready to stretch.

First decide: is this story fundamentally about something mature (injury, arrest, legal trouble,
suspension, death, gambling, abuse, money fights, firings, anything scary or sad)? If so, set skip=true,
give skip_reason, and leave the other fields empty. Never soften a mature story to make it fit.

Otherwise, skip=false and write:
- title: a short, fun headline (under 9 words). No "Big ___ showdown".
- paragraph: 5 to 7 sentences that tell the story from the ANGLE given. Make it concrete: name who did
  what, with at least 2 specific facts from the article (a number, a play, a streak, a quote). Set the
  scene, build to the best moment, and end with why it matters. Mostly first-grade words, sentences
  mostly under 15 words. Use 1 or 2 exclamation points, saved for the best moment.
- 1 to 3 "big words" a third grader would know but a first grader might not, used in the paragraph
  (for example: triumph, relentless, legendary, astonishing, persistent, glorious, sturdy, magnificent).
  A big word must be a real stretch: NOT easy words like final, perfect, returned, earned, nearly, equal,
  happy, fast, strong, big. Everyday describing or action words, NOT football terms. Not any word in the
  ALREADY USED list.
  For each: definition (under 15 words) and an example sentence about everyday kid life, not football,
  using the EXAMPLE KID and EXAMPLE TOPIC.
Facts: use ONLY what the article says. Never add a stat, score, name or detail. Leave out anything about
injuries, money, betting, or trouble even if the article mentions it. Fun, not mean. Plain text, no emoji.
"""


def write_story(client: OpenAI, c: dict, angle: str, used: list[str], i: int) -> dict | None:
    prompt = (f"ANGLE: {angle}\nALREADY USED big words: {', '.join(used) or '(none)'}\n"
              f"EXAMPLE KID: {EXAMPLE_KIDS[(i + 3) % len(EXAMPLE_KIDS)]}\n"
              f"EXAMPLE TOPIC: {EXAMPLE_TOPICS[(i * 7 + 2) % len(EXAMPLE_TOPICS)]}\n\n"
              f"ARTICLE: {c['title']}\n{c['text'][:12000]}")
    feedback, problems, d = "", [], None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = client.responses.parse(model=MODEL, instructions=STORY_INSTRUCTIONS, input=prompt + feedback,
                                      reasoning={"effort": "low"}, text_format=StoryDraft, store=False)
        d = resp.output_parsed
        if d.skip:
            log_drop(c["url"], "stories.rewrite", "SKIP", {"title": c["title"], "reason": d.skip_reason})
            return None
        problems = check_recap(Recap(paragraph=d.paragraph, big_words=d.big_words), used, kind="story")
        easy = [w.word for w in d.big_words if w.word.lower() in EASY_WORDS or len(w.word) < 6]
        if easy:
            problems.append(f"big words too easy: {easy}; pick real third-grade stretch words")
        hits = tripwire(d.title)
        if hits:
            problems.append(f"title contains blocked words {hits}")
        if not problems:
            final = classify_story({"url": c["url"], "title": d.title, "text": f"{d.title}\n{d.paragraph}"},
                                   scope="text")
            if not is_safe(final):
                log_drop(c["url"], "stories.decisions_final", final["label"],
                         {"title": d.title, "paragraph": d.paragraph, "probabilities": final.get("probabilities")})
                return None
            return {"title": d.title, "paragraph": d.paragraph,
                    "big_words": [w.model_dump() for w in d.big_words],
                    "source_url": c["url"], "source_title": c["title"], "angle": angle, "attempts": attempt,
                    "final_safety": final["probabilities"]}
        feedback = "\n\nYour last draft had these problems; fix them:\n- " + "\n- ".join(problems)
    log_drop(c["url"], "stories.rewrite", "; ".join(problems), {"draft": d.model_dump() if d else None})
    return None


def build(since: str) -> list[dict]:
    load_dotenv(DATA_DIR.parent / ".env")
    p_client, o_client = Parallel(), OpenAI()
    found = discover(p_client, since)
    cands = extract(p_client, found)
    safe = safety(cands)
    ranked = rank(o_client, safe) if safe else []
    used = [w["word"] for f in ("last_week.json", "next_week.json") if (DATA_DIR / f).exists()
            for g in json.loads((DATA_DIR / f).read_text()) for w in g.get("big_words", [])]
    stories = []
    for i, (c, angle) in enumerate(ranked):
        if len(stories) == N_STORIES:
            break
        s = write_story(o_client, c, angle, used, i)
        if s:
            used += [w["word"] for w in s["big_words"]]
            stories.append(s)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{date.today().isoformat()}.json").write_text(json.dumps({
        "since": since, "found": len(found), "extracted": len(cands), "safe": len(safe),
        "candidates": [{k: v for k, v in c.items() if k != "text"} for c in cands],
        "ranked": [{"url": c["url"], "title": c["title"], "angle": a} for c, a in ranked],
        "stories": stories}, indent=2))
    (DATA_DIR / "stories.json").write_text(json.dumps(stories, indent=2))
    print(f"search: {len(found)} article URLs | extracted: {len(cands)} | safe: {len(safe)} | "
          f"ranked: {len(ranked)} | written: {len(stories)}")
    return stories


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD: only news published on or after this date")
    args = ap.parse_args()
    build(args.since)


if __name__ == "__main__":
    main()
