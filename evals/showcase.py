"""Render the standalone Search Evals page (for sharing; not part of the kids' site).

    uv run python -m evals.showcase --out path/to/search-evals.html

Reads evals/results/summary.json (built by evals.summarize).
"""

import argparse
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent


def money(v):
    if v is None:
        return "—"
    return f"{v * 100:.1f}¢" if v < 0.1 else f"${v:.2f}"


def secs(v):
    return "—" if v is None else f"{v:.0f} s"


def render() -> str:
    data = json.loads((HERE / "results" / "summary.json").read_text())
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters.update(money=money, secs=secs)
    return env.get_template("showcase.html").render(ev=data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.write_text(render())
    print("wrote", args.out)


if __name__ == "__main__":
    main()
