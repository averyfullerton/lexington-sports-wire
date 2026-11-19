"""Read-only web app. Serves stored pipeline output from data/; never calls APIs.

uv run uvicorn app.main:app --reload
"""

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PINNED = json.loads((ROOT / "config" / "pinned_teams.json").read_text())

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def glossify(paragraph: str, big_words: list[dict] | None, key: str) -> Markup:
    """Turn each big word's first use into a tappable button that opens a definition card.

    Uses the HTML popover attribute, so it works on tap (iPad) with no JavaScript.
    """
    html = str(escape(paragraph))
    cards = []
    for i, w in enumerate(big_words or []):
        word = str(escape(w.get("word", "")))
        if not word:
            continue
        pid = f"w-{slugify(key)}-{i}"
        pattern = re.compile(r"\b(" + re.escape(word) + r")\b", re.IGNORECASE)
        html, n = pattern.subn(
            lambda m: f'<button type="button" class="bigword" popovertarget="{pid}">{m.group(1)}</button>',
            html, count=1)
        if not n:
            continue
        cards.append(
            f'<div id="{pid}" popover class="wordcard">'
            f'<button type="button" class="close" popovertarget="{pid}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'<p class="word">{word}</p>'
            f'<p class="def">{escape(w.get("definition", ""))}</p>'
            f'<p class="ex">“{escape(w.get("example", ""))}”</p>'
            f"</div>")
    html = link_players(html)
    return Markup(f"<p>{html}</p>" + "".join(cards))


SUFFIX_RE = re.compile(r" (Jr\.|Sr\.|II|III|IV)$")


def link_players(html: str) -> str:
    """Link the first full-name mention of each player who has a card (internal links only)."""
    index = load("players/_index.json", {})
    for name in sorted(index, key=len, reverse=True):
        short = SUFFIX_RE.sub("", name)
        for variant in {short, short.replace("'", "’"), short.replace("’", "'")}:
            pat = re.compile(r"(?<![\w>/-])" + re.escape(str(escape(variant))) + r"\b(?![^<]*</a>)")
            html, n = pat.subn(lambda m: f'<a class="player" href="/player/{index[name]}">{m.group(0)}</a>',
                               html, count=1)
            if n:
                break
    return html


templates.env.globals["glossify"] = glossify


def load(name: str, default):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def pinned_first(games: list[dict]) -> list[dict]:
    def is_pinned(g):
        return g.get("home_abbr") in PINNED or g.get("away_abbr") in PINNED

    return sorted(games, key=lambda g: not is_pinned(g))


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def edition() -> dict:
    """Masthead dateline: the week of the last refresh and today's date."""
    week = None
    log = DATA / "refresh_log.jsonl"
    if log.exists():
        lines = [l for l in log.read_text().splitlines() if l.strip()]
        week = json.loads(lines[-1]).get("week") if lines else None
    now = datetime.now()
    return {"edition": f"Week {week} Edition" if week else "", "today": f"{now:%A}, {now:%B} {now.day}, {now:%Y}"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        **edition(),
        "last_week": pinned_first(load("last_week.json", [])),
        "next_week": pinned_first(load("next_week.json", [])),
        "stories": load("stories.json", [])[:5],
        "standings": load("standings.json", {}),
        "pinned": PINNED,
    })


@app.get("/player/{slug}", response_class=HTMLResponse)
def player(request: Request, slug: str):
    if slugify(slug) != slug:
        raise HTTPException(404)
    card = load(f"players/{slug}.json", None)
    if card is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "player.html", {**edition(), "p": card, "pinned": PINNED,
                                                               "pin": PINNED.get(card.get("team_abbr"))})
