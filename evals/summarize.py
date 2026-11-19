"""Summarize parallel-search-evals runs into evals/results/summary.json for the Search Evals page.

    uv run python -m evals.summarize --runs ~/projects/parallel-search-evals/runs

Reads the harness's per-question records (grades) and the lsw-eval fork's sidecar metrics
(tokens, tool calls, wall time). Costs use list prices from 2026-11-19.
"""

import argparse
import json
import re
import statistics
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
ALLOW = tuple(json.loads((ROOT / "config" / "domains.json").read_text())["allow"])

ARMS = {  # key -> (label, search fee per call in USD)
    "parallel-advanced": ("Parallel Advanced", 0.005),
    "parallel-fast": ("Parallel Fast", 0.001),
    "openai-search": ("OpenAI web search", 0.010),
    "baseline": ("Baseline (no search)", 0.0),
}
EXTRACT_FEE = 0.001                       # Parallel Extract, per URL
TOKEN_IN, TOKEN_CACHED, TOKEN_OUT = 1.00e-6, 0.05e-6, 5.00e-6   # gpt-6.1-terra per token

SUITES = [
    {"key": "facts", "title": "30 easier questions", "questions": "evals/facts/questions.jsonl",
     "rounds": ["facts-r2", "facts-r3"], "tool_calls": 20,
     "note": "Week 10 scores, stat lines, kickers, season totals, play-by-play details and two games not yet played. "
             "Two clean rounds per tool; up to 20 searches per question."},
    {"key": "hard", "title": "20 harder questions", "questions": "evals/hard/questions.jsonl",
     "rounds": ["hard-r1"], "tool_calls": 5,
     "note": "Mostly whole-week questions (leaders, ties, games within 3 points), plus multi-step and trick "
             "questions. One round per tool; up to 5 searches per question; baseline not run."},
]


def on_allowlist(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOW)


def question_result(runs: Path, run: str, arm: str, i: int) -> dict | None:
    rec_path = runs / f"{run}-{arm}" / f"{i}.json"
    if not rec_path.exists():
        return None
    rec = json.loads(rec_path.read_text())
    met_path = runs / f"{run}-{arm}-metrics" / f"{i}.json"
    met = json.loads(met_path.read_text()) if met_path.exists() else {}
    calls = met.get("function_calls", [])
    searches = sum(c["name"] == "search_web" for c in calls) + len(met.get("web_search_calls", []))
    extract_urls = sum(len(json.loads(c["arguments"]).get("urls", [])) for c in calls if c["name"] == "web_fetch")
    usage = met.get("usage", [])
    tin = sum(u.get("input_tokens", 0) for u in usage)
    cached = sum((u.get("input_tokens_details") or {}).get("cached_tokens", 0) for u in usage)
    tout = sum(u.get("output_tokens", 0) for u in usage)
    answer = (rec.get("convo") or {}).get("assistant_text", "")
    cited = set(re.findall(r"https?://[^\s)\]>\"'\\]+", answer))
    cost = ((tin - cached) * TOKEN_IN + cached * TOKEN_CACHED + tout * TOKEN_OUT
            + searches * ARMS[arm][1] + extract_urls * EXTRACT_FEE)
    return {"correct": bool((rec.get("grader_result") or {}).get("correct")), "failed": rec["status"] != "completed",
            "elapsed": met.get("elapsed_s"), "searches": searches, "cost": cost,
            "cited": len(cited), "cited_off": sum(not on_allowlist(u) for u in cited)}


def pct(values: list[float], q: float) -> float | None:
    v = sorted(x for x in values if x is not None)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def summarize(runs: Path) -> dict:
    out = {"date": "2026-11-19", "model": "gpt-6.1-terra", "judge": "gpt-6-sol", "allowlist": list(ALLOW), "suites": []}
    for s in SUITES:
        qs = [json.loads(l) for l in (ROOT / s["questions"]).read_text().splitlines() if l.strip()]
        arms, per_q = [], [{"id": q["id"], "category": q["category"], "problem": q["problem"], "answer": q["answer"],
                             "marks": {}} for q in qs]
        for arm, (label, _) in ARMS.items():
            results = [[question_result(runs, run, arm, i) for i in range(len(qs))] for run in s["rounds"]]
            if all(r is None for rnd in results for r in rnd):
                arms.append({"key": arm, "label": label, "ran": False})
                for q in per_q:
                    q["marks"][arm] = None
                continue
            flat = [r for rnd in results for r in rnd if r]
            right = [sum(r["correct"] for r in rnd if r) for rnd in results]
            spent = sum(r["cost"] for r in flat)
            arms.append({
                "key": arm, "label": label, "ran": True, "rounds": len(results),
                "correct": right, "total": len(qs),
                "accuracy": statistics.mean(right) / len(qs),
                "cost_per_question": spent / len(flat),
                "cost_per_resolved": spent / sum(right) if sum(right) else None,
                "p50": pct([r["elapsed"] for r in flat], 0.5), "p95": pct([r["elapsed"] for r in flat], 0.95),
                "searches_per_question": sum(r["searches"] for r in flat) / len(flat),
                "cited": sum(r["cited"] for r in flat), "cited_off": sum(r["cited_off"] for r in flat),
                "failed": sum(r["failed"] for r in flat),
            })
            for i, q in enumerate(per_q):
                got = [rnd[i]["correct"] for rnd in results if rnd[i]]
                q["marks"][arm] = {"right": sum(got), "of": len(got)}
        out["suites"].append({**{k: s[k] for k in ("key", "title", "note", "tool_calls")}, "arms": arms,
                              "questions": per_q})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=Path.home() / "projects" / "parallel-search-evals" / "runs")
    args = ap.parse_args()
    dest = ROOT / "evals" / "results" / "summary.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = summarize(args.runs)
    dest.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    for s in data["suites"]:
        print(s["key"], [(a["label"], a.get("correct"), round(a["cost_per_resolved"], 4) if a.get("cost_per_resolved") else None)
                         for a in s["arms"]])


if __name__ == "__main__":
    main()
