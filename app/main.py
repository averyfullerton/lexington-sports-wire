"""Read-only web app. Serves stored pipeline output from data/; never calls APIs.

uv run uvicorn app.main:app --reload
"""

import json
import re
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
    return Markup(f"<p>{html}</p>" + "".join(cards))


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


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {
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
    return templates.TemplateResponse(request, "player.html", {"p": card, "pinned": PINNED})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    q_norm = q.strip().lower()
    players, games = [], []
    if q_norm:
        for path in sorted((DATA / "players").glob("*.json")) if (DATA / "players").exists() else []:
            card = json.loads(path.read_text())
            if q_norm in card.get("name", "").lower() or q_norm in card.get("team", "").lower():
                players.append(card | {"slug": path.stem})
        for g in load("last_week.json", []) + load("next_week.json", []):
            if q_norm in (g.get("home_team", "") + " " + g.get("away_team", "")).lower():
                games.append(g)
    return templates.TemplateResponse(request, "search.html", {
        "q": q, "players": players, "games": games, "pinned": PINNED,
    })
