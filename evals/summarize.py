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
    "parallel-turbo": ("Parallel Turbo", 0.001),
    "openai-search": ("OpenAI web search", 0.010),
    "baseline": ("Baseline (no search)", 0.0),
}
EXTRACT_FEE = 0.001                       # Parallel Extract, per URL
TOKEN_IN, TOKEN_CACHED, TOKEN_OUT = 1.00e-6, 0.05e-6, 5.00e-6   # gpt-6.1-terra per token

SUITES = [
    {"key": "facts", "title": "30 easy questions", "questions": "evals/facts/questions.jsonl",
     "rounds": ["facts-r2", "facts-r3"], "tool_calls": 20,
     "note": "Week 10 scores, stat lines, kickers, season totals, play-by-play details and two games not yet played. "
             "Two clean rounds per tool; up to 20 searches per question."},
    {"key": "hard5", "title": "20 hard questions", "questions": "evals/hard/questions.jsonl", "limit": 20,
     "rounds": ["hard-r1"], "tool_calls": 5,
     "note": "Mostly whole-week questions (leaders, ties, games within 3 points), plus multi-step and trick "
             "questions. One round per tool; up to 5 searches per question."},
    {"key": "hard20", "title": "30 hard questions", "questions": "evals/hard/questions.jsonl",
     # One round, run in two batches: questions 1-20, then the 10 added later (21-30).
     "rounds": [["hard20-r1", "hard30x-r1"]], "tool_calls": 20,
     "note": "The same 20 hard questions plus 10 very hard ones (who played where and when, streaks, upsets, "
             "history). One round per tool; up to 20 searches per question."},
]


def on_allowlist(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOW)


def locate(runs: Path, run, arm: str, i: int) -> tuple[str, int]:
    """A round is one run prefix, or a list of prefixes whose questions follow each other in order."""
    if isinstance(run, str):
        return run, i
    for prefix in run:
        n = len(list((runs / f"{prefix}-{arm}").glob("[0-9]*.json"))) if (runs / f"{prefix}-{arm}").exists() else 0
        if i < n:
            return prefix, i
        i -= n
    return run[-1], 10**6


def question_result(runs: Path, run, arm: str, i: int) -> dict | None:
    run, i = locate(runs, run, arm, i)
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
        qs = [json.loads(l) for l in (ROOT / s["questions"]).read_text().splitlines() if l.strip()][:s.get("limit")]
        arms, per_q = [], [{"id": q["id"], "category": q["category"], "problem": q["problem"], "answer": q["answer"],
                             "marks": {}} for q in qs]
        for arm, (label, _) in ARMS.items():
            results = [[question_result(runs, run, arm, i) for i in range(len(qs))] for run in s["rounds"]]
            if all(r is None for rnd in results for r in rnd):
                continue  # this tool wasn't run on this set
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
    out["search_payload"] = search_payload(runs, "hard-r1", 20)
    return out


def search_payload(runs: Path, run: str, n: int) -> dict:
    """What one search hands the model, from the 5-search run's records.

    Parallel: results and excerpt characters per search_web call (visible in the records).
    OpenAI: only its actions are visible (queries, pages opened); its search results are not returned to us.
    """
    out = {}
    for arm in ("parallel-fast", "parallel-advanced"):
        chars, nres = [], []
        for i in range(n):
            path = runs / f"{run}-{arm}" / f"{i}.json"
            if not path.exists():
                continue
            msgs = [m["native"] for m in json.loads(path.read_text())["convo"]["messages"]]
            names = {m["call_id"]: m["name"] for m in msgs if m.get("type") == "function_call"}
            for m in msgs:
                if m.get("type") == "function_call_output" and names.get(m["call_id"]) == "search_web":
                    try:
                        res = json.loads(m["output"]).get("results", [])
                    except (ValueError, AttributeError):
                        continue
                    nres.append(len(res))
                    chars.append(sum(len(e) for x in res for e in x.get("excerpts", [])))
        if chars:
            out[arm] = {"label": ARMS[arm][0], "searches": len(chars), "results": statistics.median(nres),
                        "chars": int(statistics.median(chars))}
    acts, over = {}, 0
    for i in range(n):
        path = runs / f"{run}-openai-search-metrics" / f"{i}.json"
        if path.exists():
            calls = json.loads(path.read_text()).get("web_search_calls", [])
            over += len(calls) > 5
            for a in calls:
                acts[a.get("type")] = acts.get(a.get("type"), 0) + 1
    out["openai-search"] = {"label": ARMS["openai-search"][0], "actions": acts, "over_cap": over}
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
