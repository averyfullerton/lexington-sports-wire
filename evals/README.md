# Evals

Search-tool comparison run with Parallel's open-source harness,
[parallel-search-evals](https://github.com/parallel-web/parallel-search-evals), on our own NFL questions
(`suite=local`). Results are on the site's [Search Evals page](https://averyfullerton.github.io/lexington-sports-wire/evals.html)
and written up in [`../DE_NOTES.md`](../DE_NOTES.md#eval-results).

| Path | What it is |
|---|---|
| `facts/questions.jsonl` | 30 easy questions about Week 10 of 2026 (scores, stat lines, kickers, season totals, play-by-play, games not yet played). Answers from ESPN game data. |
| `hard/questions.jsonl` | 30 hard questions: whole-week leaders and ties, multi-step and trick questions, and 10 "very hard" ones (who played where and when, streaks, upsets, history). Answers computed from ESPN box scores and schedules. |
| `facts/judge/` | The grading prompt and schema used for both sets (strict right-or-wrong). |
| `summarize.py` | Turns harness run folders into `results/summary.json` (accuracy, cost per right answer, latency, searches, allowlist check). |
| `results/summary.json` | What the Search Evals page reads. |

## Arms

Same answering model (gpt-6.1-terra), prompts, judge (gpt-6-sol) and allowlist on every arm; only the search tool changes.

- `baseline`: no search (easy set only).
- `parallel-turbo`, `parallel-fast`, `parallel-advanced`: Parallel Search + Extract as the agent's tools.
- `openai-search`: OpenAI's built-in web search.

The OpenAI arm, the 5-site allowlist (`include_domains`), a `max_tool_calls` setting and per-question cost and
latency records are additions in a local fork of the harness (branch `lsw-eval`).

## Reproduce

```sh
# In a clone of parallel-search-evals with the lsw-eval changes; keys come from the environment.
uv run --frozen python -m parallel_search_evals.run_eval suite=local provider=openai model=gpt-6.1-terra \
  search_engine=parallel-fast source=/path/to/evals/hard/questions.jsonl source_sha256=<sha256> \
  judge_assets=/path/to/evals/facts/judge include_domains=nfl.com,espn.com,pro-football-reference.com,jaguars.com,commanders.com \
  max_tool_calls=20 limit=None max_workers=4 output_dir=runs/hard20-r1-parallel-fast

# Back in this repo:
uv run python -m evals.summarize --runs /path/to/parallel-search-evals/runs
```

Answer keys are dated 2026-11-19; season totals and "not played yet" answers go stale after that.
