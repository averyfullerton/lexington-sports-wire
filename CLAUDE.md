# Lexington Sports Wire

An NFL reader for a 6-year-old, written at a first-grade reading level. It's a walled garden: only approved sources, no outbound links, nothing mature.

This is also a hands-on evaluation of Parallel Web Systems' APIs (`parallel-web` SDK). Use the installed `parallel-api` skill for all Parallel work and check current docs (docs.parallel.ai or the Parallel MCP server) before writing calls. Don't guess parameter names.

## What the kid sees
1. **Last Week's Games:** every game from the most recent completed NFL week (Thu–Mon). Final score plus a short, high-energy recap: exciting moments, star players, who had a tough day, why the game matters.
2. **Next Week's Games:** every upcoming matchup. Records plus a short preview: who's playing, players to watch, why it matters.
3. **5 Big Stories:** the week's top kid-friendly storylines (a star back from injury, a rivalry game, a player facing his old team, a winning or losing streak).
4. **Player cards:** tap any player name → team, number, position, 3 fun facts, per-season stats table.
5. **Search bar:** type a player or team name and jump to their card or games.

- Jaguars and Commanders games are pinned at the top of each section and shown in team colors.
- Reading only. No audio, no video, no outbound links, no ads, no comments.

## Writing rules (sportswriter voice, early-reader sentences)
- Lively sportswriter voice with variety: each game in a week gets a different opening move (big play, star player, a number, the scene, a question...). Never open with "Big ___ showdown".
- 1 or 2 exclamation points per recap, saved for the best moments.
- Football jargon is fine (third down, red zone, pick-six); the reader knows the game.
- Mostly first-grade words and short sentences (mostly under 15 words), 5–8 sentences per game.
- 1–3 third-grade "big words" per recap, no repeats within a week. Each gets a kid definition and an example sentence (everyday kid life, not the game); the site shows them in a tap-to-open card (HTML `popover`, no JS).
- Fun, not mean. Teasing a team's result is OK ("the Titans fell flat"); mocking a person is not.
- Player names must match the game's ESPN box score; guessed first names from gamebook initials are auto-corrected before writing.
- Write only from verified facts the pipeline gathered. Never invent scores, stats or plays. If a fact is missing or low-confidence, leave it out.

## Pipeline: facts first, then writing
1. **Game facts (Task API + JSON schema):** for each game, collect score, top performers, key plays, team records, and storyline. Keep the per-field `basis` (citations + confidence). Flag or drop low-confidence fields.
2. **Kid paragraph (OpenAI API):** write the recap only from the verified facts in step 1.
3. **5 Big Stories (Search + Extract → Decisions → rank):** gather the week's news from allowed sources, filter for safety, pick the 5 most interesting kid-friendly storylines, write each at first-grade level.
4. **Player cards:** ESPN feeds for bio and per-season stats (official numbers); Task API + JSON schema for fun facts (with `basis`), rewritten for a first grader. Every player named in a recap or preview gets a card and a tappable name.
5. **Refresh:** weekly on Tuesday morning (after Monday Night Football). The web app only reads stored output in `data/`.

## Source policy (`config/domains.json` is the single source of truth)
- Allowlist: nfl.com, espn.com, pro-football-reference.com, jaguars.com, commanders.com
- Blocklist (belt-and-suspenders): theringer.com, deadspin.com, theathletic.com, all gambling/betting sites
- Every Parallel call that touches the web uses the allowlist. Nothing outside it reaches the UI.

## Kid-safety filter (fail closed: when in doubt, drop it)
1. **Decisions API** classifies each story twice: arrest, lawsuit, suspension, new or serious injury, death, gambling, abuse, other mature content. First the source article by topic (is the story *about* any of these?), then the finished kid text strictly (does it *mention* any of these at all?). Anything other than a confident "safe" (every category under 0.2, no refusal) is dropped. A player returning from injury is OK; a new or serious injury is not. Decisions has no source policy and researches the web on its own; its citations are logged and never reach the UI (the one allowlist exception).
2. **Rewrite step** returns `SKIP` for stories that are fundamentally mature. Never soften them.
3. **Keyword tripwire** scans the final text; any hit blocks it.
- Log every drop with its reason (for review and for the safety eval).

## Stack and conventions
- Python 3.12 with `uv` (`uv add`, `uv run`). No bare pip.
- FastAPI + Jinja2 server-rendered HTML + plain CSS. Big fonts, team colors, no external JS.
- Secrets live in `.env` (gitignored). Never print, log or commit keys.
- Small, focused commits with clear messages.

## Layout (target)
```
config/domains.json   allowlist / blocklist
pipeline/             games.py, stories.py, players.py, safety.py, write.py
app/                  FastAPI app, templates/, static/
data/                 generated output
evals/                custom question sets and results
DE_NOTES.md           friction log: latency, cost, accuracy, surprises
```

## Evaluation
Follows Parallel's "How to evaluate web search for AI" guide and the open-source harness (github.com/parallel-web/parallel-search-evals).

- **Harness:** separate clone at ~/projects/parallel-search-evals (`uv sync --frozen`). Run with `provider=openai`; needs only PARALLEL_API_KEY and OPENAI_API_KEY (the grader is always OpenAI). Use the README's `python -m parallel_search_evals.run_eval key=value` syntax, not the blog's. Our questions run as `suite=local` with our own judge assets.
- **Fair comparison:** hold everything constant except the search tool: same OpenAI model, prompt, tool budget and judge. Arms:
  - `baseline` (closed-book): measures the lift from search and how often the model invents answers. Not a competitor on fresh questions.
  - `parallel-fast` and `parallel-advanced`: Parallel Search + Extract.
  - OpenAI's built-in web search: the real head-to-head. Not in the harness; requires adding an engine in a fork.
  - Same 5-site allowlist on both search arms (a product reason to restrict). The harness doesn't apply `include_domains` today, so that is a fork change too.
- **Facts suite:** `evals/facts/questions.jsonl`, 30 questions with ESPN-sourced answers (Week 10, 2026; answer key dated 2026-11-19). Mix: scores, QB, kicker, season (player card), evergreen, obscure play-by-play, narrative, and not-yet-played (correct answer is "not played yet"). Run every arm on the same day; season totals change weekly.
- **Grading:** LLM judge with reasons. Hand-check ~10% (3 questions). Classify failures: no search call, provider error, retrieval miss, synthesis failure.
- **Report:** accuracy, cost per resolved question (search + extract + model tokens), p50/p95 latency, tool calls, citations on the allowlist. With 30 questions, call close results ties. Run each arm 3 times if budget allows.
- **Safety suite:** ~20 real headlines, clean and sensitive; measure catches and misses.
- The harness tests Search + Extract only. The Task API game-facts step gets its own check against the same answer key.
- Record results in DE_NOTES.md.
