"""Render the site to static files for GitHub Pages.

    uv run python -m app.freeze --out _site

Pages are rendered through the real app (so they match the local server), then
root-relative links are rewritten to relative ones, because the site is served
from a sub-path (user.github.io/lexington-sports-wire/).
"""

import argparse
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import DATA, app

STATIC = Path(__file__).parent / "static"


def relativize(html: str, depth: int) -> str:
    up = "../" * depth
    html = re.sub(r'href="/college/player/([a-z0-9-]+)"', lambda m: f'href="{up}college/player/{m.group(1)}.html"', html)
    html = re.sub(r'href="/player/([a-z0-9-]+)"', lambda m: f'href="{up}player/{m.group(1)}.html"', html)
    html = re.sub(r'(href|src)="/static/', lambda m: f'{m.group(1)}="{up}static/', html)
    html = html.replace('href="/evals"', f'href="{up}evals.html"')
    html = html.replace('href="/college"', f'href="{up}college.html"')
    html = html.replace('href="/"', f'href="{up or "./"}"')
    return html


def freeze(out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    (out / "player").mkdir(parents=True)
    (out / "college" / "player").mkdir(parents=True)
    shutil.copytree(STATIC, out / "static")
    client = TestClient(app)

    r = client.get("/")
    r.raise_for_status()
    (out / "index.html").write_text(relativize(r.text, 0))

    r = client.get("/evals")
    r.raise_for_status()
    (out / "evals.html").write_text(relativize(r.text, 0))

    r = client.get("/college")
    r.raise_for_status()
    (out / "college.html").write_text(relativize(r.text, 0))

    college = sorted(p.stem for p in (DATA / "cfb" / "players").glob("[!_]*.json"))
    for slug in college:
        r = client.get(f"/college/player/{slug}")
        r.raise_for_status()
        (out / "college" / "player" / f"{slug}.html").write_text(relativize(r.text, 2))

    players = sorted(p.stem for p in (DATA / "players").glob("[!_]*.json"))
    for slug in players:
        r = client.get(f"/player/{slug}")
        r.raise_for_status()
        (out / "player" / f"{slug}.html").write_text(relativize(r.text, 1))

    (out / ".nojekyll").write_text("")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    leftovers = [str(p) for p in out.rglob("*.html") if re.search(r'(href|src)="/(?!/)', p.read_text())]
    return {"pages": 3 + len(players) + len(college), "root_links_left": leftovers}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()
    print(freeze(Path(args.out)))


if __name__ == "__main__":
    main()
