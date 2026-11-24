"""Kid writing step (OpenAI API). Writes ONLY from verified facts.

Rules (see CLAUDE.md): sportswriter voice with variety, jargon OK, short sentences,
5-8 sentences per game, at most 2 exclamation points, a few 3rd-grade "big words"
with kid definitions, fun but never mean to a person, never invent facts.
Stories that are fundamentally mature return SKIP; never soften them.

Try one game:
    uv run python -m pipeline.write data/raw/games/<game>-base.json
"""

import json
import re
import sys

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from pipeline.safety import log_drop, tripwire

MODEL = "gpt-6.1-terra"
SKIP = "SKIP"
MAX_ATTEMPTS = 3
MIN_EXCLAMATIONS, MAX_EXCLAMATIONS = 1, 2

# Each game in a week gets a different opening move, so recaps don't all start alike.
# Rotated per game so the big-word example sentences don't all star the same kid.
EXAMPLE_KIDS = ["Leo", "Priya", "Marcus", "Sofia", "Jamal", "Hana", "Diego", "Ava", "Kofi",
                "Lily", "Mateo", "Zoe", "Ethan", "Nia", "Omar", "Grace"]
EXAMPLE_TOPICS = ["a pet dog", "a birthday party", "a science project", "a bike ride",
                  "baking cookies", "a soccer game at recess", "a trip to the zoo",
                  "building with blocks", "a rainy day", "a swimming lesson", "a garden",
                  "a spelling test", "a camping trip", "a lemonade stand", "a library book",
                  "a talent show"]

OPENERS = [
    "Start with the single biggest play, told as action, as if it is happening now.",
    "Start with the star player's name and what he did.",
    "Start with a striking number from the facts (yards, points, a streak, a record).",
    "Start by setting the scene with drama: where the game was and what was at stake.",
    "Start with a short question a fan might ask, then answer it.",
    "Start with the final score, told in a fresh way (not 'X beat Y').",
    "Start with the losing team's side of the story, then turn to the winners.",
    "Start with how the game ended: the last big moment.",
]

PREVIEW_OPENERS = [
    "Start with the star player to watch and his numbers this season.",
    "Start with what is at stake in the standings.",
    "Start with a short question a fan might ask about this game.",
    "Start with a striking number from this season.",
    "Start by setting the scene: where the game is and when.",
    "Start with one team's streak or last game.",
    "Start with the road team's side of the story.",
]

PREVIEW_INSTRUCTIONS = """\
You are a sportswriter with a lively, varied style, writing NFL game PREVIEWS for a
6-year-old superfan. He knows football and its jargon. He reads at a first-grade level
and is ready to stretch a little.

This game has NOT been played yet. Write 4 to 6 sentences that get him excited to watch.

Style
- Opening: follow the OPENING MOVE given below. Do not start with "Big showdown",
  "Big ___ showdown" or "Wild ___". Do not start with an exclamation.
- Cover who is playing, their records, 1 or 2 players to watch with their numbers this
  season, and why the game matters. Mention the day it is played if it is not Sunday.
- Never predict a winner or a score. No "will win", "should win", "favored", "underdog",
  odds or picks.
- Ending: follow the CLOSING MOVE given below. Never end with "Get ready for..." or
  "... game!" hype lines.
- Use 1 or 2 exclamation points, saved for the most exciting parts.
- Mostly first-grade words, sentences mostly under 15 words.
- Use 1 to 3 "big words" a third grader would know but a first grader might not
  (for example: spotlight, perched, eager, fierce, thrilling, rivals, stunning, determined).
  A big word must be an everyday describing or action word, NOT a football or sports term
  (not season, league, division, standings, matchup, receiving, interception, average, title).
  List each one in big_words exactly as it appears, with:
    definition: a short kid-friendly meaning (under 15 words)
    example: a new sentence using the word, about everyday kid life, NOT about football.
      Make it vivid and specific. Use the EXAMPLE KID and EXAMPLE TOPIC given below.
- The game is at the home team's place: use the home team's state or city ("in New York").
- Fun, not mean. Never mock or insult a person.

Facts
- Use ONLY the facts given. Never add a stat, record, name, place or detail that is not in
  the facts. If a fact you want is missing, leave it out.
- Never mention injuries, fights, arrests, suspensions, money, betting, or anything scary or sad.
- Plain text, one paragraph. No title, no list, no emoji. Write numbers as digits.
"""

SPORTS_WORDS = {"season", "seasons", "league", "division", "standings", "matchup", "receiving",
                "interception", "interceptions", "average", "title", "playoffs", "playoff", "touchdown",
                "touchdowns", "quarterback", "yards", "rushing", "passing", "conference", "record"}

PREVIEW_CLOSERS = [
    "End with a question the reader can answer by watching the game.",
    "End on the player to watch and the one thing he does best.",
    "End with what a win would mean in the standings.",
    "End with a short, punchy fact (a number) from the facts.",
    "End by naming the thing to watch for: a matchup, a streak, a debut.",
    "End with the scene: when and where the game kicks off.",
    "End on the team that needs this game most, and why (from the facts).",
]

STALE_CLOSER_RE = re.compile(r"\bget ready\b|\bgame!\s*$", re.IGNORECASE)

PREDICTION_RE = re.compile(
    r"\b(will|should|going to|expected to|likely to)\s+(win|beat|lose|cruise|roll)\b"
    r"|\b(favou?red|underdog|predict\w*|odds)\b", re.IGNORECASE)


RECAP_INSTRUCTIONS = """\
You are a sportswriter with a lively, varied style, writing NFL game recaps for a
6-year-old superfan. He knows football and its jargon (third down, red zone, pick-six).
He reads at a first-grade level and is ready to stretch a little.

Write 5 to 8 sentences about the game in the facts below.

Style
- Opening: follow the OPENING MOVE given below. Do not start with "Big showdown",
  "Big ___ showdown" or "Wild ___ in". Do not start with an exclamation.
- Tell it like a story, not a stat list. Bring energy through vivid verbs (zipped, rumbled,
  snagged, stunned) and rhythm: mix short punchy sentences with slightly longer ones.
- A close game or shootout should feel thrilling; a blowout can feel like a statement.
- Vary your verbs and phrases. Avoid stock lines like "but it wasn't enough" in every recap.
- Use 1 or 2 exclamation points, saved for the most exciting moments. Other sentences end with a period.
- Mostly first-grade words, sentences mostly under 15 words.
- Use 1 to 3 "big words" a third grader would know but a first grader might not
  (for example: rally, dominant, clutch, momentum, determined, spectacular, streak, comeback).
  List each one in big_words exactly as it appears in the recap, with:
    definition: a short kid-friendly meaning (under 15 words)
    example: a new sentence using the word, about everyday kid life, NOT about this game.
      Make it vivid and specific. Use the EXAMPLE KID and EXAMPLE TOPIC given below.
- Where it fits, mention where the game was. The game was played at the home team's place:
  use the home team's state or city ("in Tennessee"), never "the Titans' stadium".
  Call it a rivalry or division game only if the facts mention the division.
- Teasing a team's result is fine ("the Titans fell flat"); never mock or insult a person.
- Say why the game matters (standings, streaks) somewhere, often near the end.

Facts
- Use ONLY the facts given. Never add a score, stat, play, name, place or detail that is
  not in the facts. If a fact you want is missing, leave it out.
- Never mention injuries, fights, arrests, money, betting, or anything scary or sad.
- Plain text, one paragraph. No title, no list, no emoji. Write numbers as digits.
"""


CFB_NOTES = """
College games
- These are college teams. Call players by name; never say "pro" or "NFL".
- If the facts give away_rank or home_rank, that is the team's AP Top 25 rank: say "No. 3 Georgia".
  Unranked teams have no rank; never call them ranked.
- If the facts give venue_city, the game is played there (it may be a neutral site): use that place
  instead of the home team's city.
"""


def instructions_for(kind: str, league: str) -> str:
    base = PREVIEW_INSTRUCTIONS if kind == "preview" else RECAP_INSTRUCTIONS
    return base if league == "nfl" else base.replace("NFL", "college football") + CFB_NOTES


class BigWord(BaseModel):
    word: str
    definition: str
    example: str


class Recap(BaseModel):
    paragraph: str
    big_words: list[BigWord]


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def words_per_sentence(text: str) -> list[int]:
    return [len(s.split()) for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def usable_facts(content: dict, drop: list[str]) -> dict:
    """Remove fields (and list elements like 'key_plays.2') the fact step flagged to drop."""
    facts = json.loads(json.dumps(content))
    for path in sorted(drop, key=lambda p: -p.count(".")):
        key, _, idx = path.partition(".")
        if idx and isinstance(facts.get(key), list) and int(idx) < len(facts[key]):
            facts[key][int(idx)] = None
        elif not idx:
            facts.pop(key, None)
    for k, v in facts.items():
        if isinstance(v, list):
            facts[k] = [x for x in v if x is not None]
    return facts


def word_in(word: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE) is not None


def check_recap(recap: Recap, avoid_words: list[str] = (), kind: str = "recap") -> list[str]:
    text = recap.paragraph
    problems = []
    n = count_sentences(text)
    lo, hi, want = {"preview": (3, 7, "4 to 6"), "story": (5, 8, "5 to 7")}.get(kind, (4, 9, "5 to 8"))
    if not lo <= n <= hi:
        problems.append(f"has {n} sentences; write {want}")
    if kind == "preview":
        if STALE_CLOSER_RE.search(text):
            problems.append("uses a stock 'Get ready...' or '...game!' line; follow the CLOSING MOVE instead")
        guess = PREDICTION_RE.search(text)
        if guess:
            problems.append(f"predicts the result ('{guess.group(0)}'); never predict a winner")
    long = [w for w in words_per_sentence(text) if w > 18]
    if long:
        problems.append(f"sentences too long ({long} words); keep them mostly under 15 words")
    bangs = text.count("!")
    if not MIN_EXCLAMATIONS <= bangs <= MAX_EXCLAMATIONS:
        problems.append(f"uses {bangs} exclamation points; use 1 or 2")
    if re.match(r"\s*(big|wild)\b", text, re.IGNORECASE):
        problems.append("starts with 'Big' or 'Wild'; follow the opening move")
    if not 1 <= len(recap.big_words) <= 3:
        problems.append(f"lists {len(recap.big_words)} big words; use 1 to 3")
    missing = [w.word for w in recap.big_words if not word_in(w.word, text)]
    if missing:
        problems.append(f"big words not found in the recap exactly as written: {missing}")
    jargon = [w.word for w in recap.big_words if w.word.lower() in SPORTS_WORDS]
    if jargon:
        problems.append(f"big words are sports terms, not stretch words: {jargon}; pick everyday words")
    reused = [w.word for w in recap.big_words if w.word.lower() in {a.lower() for a in avoid_words}]
    if reused:
        problems.append(f"big words already used this week: {reused}; pick different ones")
    all_text = " ".join([text] + [f"{w.definition} {w.example}" for w in recap.big_words])
    hits = tripwire(all_text)
    if hits:
        problems.append(f"uses words not allowed for kids: {hits}")
    return problems


def game_recap(client: OpenAI, facts: dict, game_id: str, opener: str = OPENERS[0],
               avoid_words: list[str] = (), example_kid: str = EXAMPLE_KIDS[0],
               example_topic: str = EXAMPLE_TOPICS[0], league: str = "nfl") -> dict:
    """Short recap of a completed game, plus its big words."""
    return _write(client, facts, game_id, opener, avoid_words, example_kid, example_topic, "recap", league=league)


def game_preview(client: OpenAI, facts: dict, game_id: str, opener: str = PREVIEW_OPENERS[0],
                 avoid_words: list[str] = (), example_kid: str = EXAMPLE_KIDS[0],
                 example_topic: str = EXAMPLE_TOPICS[0], closer: str = PREVIEW_CLOSERS[0],
                 league: str = "nfl") -> dict:
    """Short preview of an upcoming game, plus its big words. Never predicts a result."""
    return _write(client, facts, game_id, opener, avoid_words, example_kid, example_topic, "preview", closer, league)


def _write(client: OpenAI, facts: dict, game_id: str, opener: str, avoid_words, example_kid: str,
           example_topic: str, kind: str, closer: str | None = None, league: str = "nfl") -> dict:
    """Returns {"paragraph", "big_words", "opener", "attempts", "problems", "usage"}."""
    instructions = instructions_for(kind, league)
    avoid = ", ".join(avoid_words) or "(none yet)"
    closing = f"CLOSING MOVE: {closer}\n" if closer else ""
    prompt = (f"OPENING MOVE: {opener}\n{closing}\nBig words already used this week (choose different ones): {avoid}\n"
              f"EXAMPLE KID: {example_kid}\nEXAMPLE TOPIC: {example_topic}\n\n"
              "Facts (JSON):\n" + json.dumps(facts, indent=1))
    feedback = ""
    usage = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = client.responses.parse(
            model=MODEL,
            instructions=instructions,
            input=prompt + feedback,
            reasoning={"effort": "low"},
            text_format=Recap,
            store=False,
        )
        recap: Recap = resp.output_parsed
        usage.append({"input": resp.usage.input_tokens, "output": resp.usage.output_tokens})
        problems = check_recap(recap, avoid_words, kind)
        if not problems:
            return {
                "paragraph": recap.paragraph.strip(),
                "big_words": [w.model_dump() for w in recap.big_words],
                "opener": opener,
                "attempts": attempt,
                "problems": [],
                "usage": usage,
            }
        feedback = (
            "\n\nYour last draft was:\n" + recap.model_dump_json()
            + "\n\nFix these problems and rewrite it: " + "; ".join(problems)
        )
    # Fail closed: a recap that still breaks the rules is not shown.
    log_drop(game_id, f"write.{'' if league == 'nfl' else league + '_'}game_{kind}", "; ".join(problems), {"draft": recap.model_dump()})
    return {"paragraph": None, "big_words": [], "opener": opener, "attempts": MAX_ATTEMPTS,
            "problems": problems, "usage": usage}


def main() -> None:
    load_dotenv()
    client = OpenAI()
    for i, path in enumerate(sys.argv[1:]):
        raw = json.load(open(path))
        facts = usable_facts(raw["content"], raw.get("flags", {}).get("drop", []))
        out = game_recap(client, facts, path, OPENERS[i % len(OPENERS)])
        print(f"== {facts['away_team']} {facts.get('away_score')} @ {facts['home_team']} {facts.get('home_score')}")
        print(out["paragraph"] or f"DROPPED: {out['problems']}")
        for w in out["big_words"]:
            print(f"   [{w['word']}] {w['definition']} | {w['example']}")
        print(f"   attempts={out['attempts']} usage={out['usage']}")


if __name__ == "__main__":
    main()
