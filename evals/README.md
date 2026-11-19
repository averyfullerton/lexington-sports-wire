# Evals

Harness: separate clone at `~/projects/parallel-search-evals` (`uv sync --frozen`; needs
PARALLEL_API_KEY and OPENAI_API_KEY; the grader is always OpenAI).

- `facts/questions.jsonl` — 30 Week 10 (2026) questions with ESPN-sourced answers; see CLAUDE.md;
  baseline, parallel-fast, parallel-advanced, and OpenAI web search (needs a harness fork).
- `safety/` — ~20 real headlines, clean and sensitive; measure catches and misses.

Record results in `../DE_NOTES.md`.
