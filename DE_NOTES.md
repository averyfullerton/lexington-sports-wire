# DE Notes — Parallel Web Systems evaluation

Friction log: latency, cost, accuracy, surprises.

## Setup
- 

## Task API (game facts, player cards)

### 2026-11-19: base vs core on one game (JAX 40 @ TEN 23, Week 10)
Setup: JSON output schema (score, records, top performers, key plays, tough day, storyline),
`source_policy.include_domains` = our 5-site allowlist. Checked against the ESPN box score.

| | base | core |
|---|---|---|
| Cost | ~$0.01 | ~$0.025 |
| Latency | 124 s | 130 s |
| Score, records, 5 stat lines | all correct | all correct |
| Key-play game clocks | 5/5 correct | 4/5 (Tuten TD 1:14, actual 1:10), rated **high** |
| Storyline (streaks, division lead) | correct | correct |
| Confidence | 3 fields medium (all correct) | all high |
| Citations | nfl.com, espn.com, jaguars.com; 0 off-allowlist | same |

Observations:
- `include_domains` held: every citation was on the allowlist.
- Confidence isn't a correctness signal at n=1: base's "medium" fields were right, and core's one error was "high". Dropping anything below high would have cut Parker Washington's 152-yard game. Rule is now drop **low** only (per CLAUDE.md); medium is logged.
- SDK returns integer schema fields as floats (`40.0`); normalized in code.
- Both runs ~2 min, so a 14-game week in parallel is a few minutes, not an hour.
- Next: run the full week on base (~$0.14) before settling the processor.

### 2026-11-19: full Week 10 on base (14 games)
Ran all 14 in parallel (8 workers). Per-game latency 68–326 s; whole week ~5.5 min; ~$0.14.
Auto-checked 198 facts against ESPN game data (scratchpad checker; ESPN play-by-play + box score).

| Fact type | Result |
|---|---|
| Final scores (28) | 28/28 |
| Records after game (28) | 28/28 |
| Player stat lines (top performers + tough day) | all numbers matched box score |
| Key plays with game clock | 10 flagged |
| Storyline claims (6 hand-checked vs schedules/box) | 6/6; 2 Thursday-night history claims unverifiable |

The 10 flagged key plays:
- 1 real error: JAX@TEN Strange TD placed at Q3 12:35 (that was a TEN field goal; TD was 6:28).
- 2 match the AP recap but not ESPN play-by-play (clock at snap vs after play).
- 7 off by 4–17 seconds; likely source clock conventions, still don't match the official record.
- Run-to-run variance: same game, same processor, previous run had all clocks exact.

Decisions:
- Processor: **base** (matches core on everything the reader shows, ~40% of the cost).
- Key plays: keep game-clock times (owner's call), accepting they may be off by a few seconds.
- Team-unit "tough day" entries (e.g. "Titans defense, 459 yards allowed") can't be auto-checked against player rows; spot-checked ones were correct.
- Historical trivia in storylines ("first Thursday-night win since 2022") is the riskiest claim type; consider keeping storylines to this-season facts.

### 2026-11-19: name hallucination from a gamebook initial
HOU@CLE fact sheet turned the NFL gamebook's "K.Concepcion" into "**Kyle** Concepcion" (real name: KC Concepcion).
The Task cited the gamebook PDF text, which only had the initial, so the first name was a guess. The OpenAI recap repeated it.
Fix: corrected that fact sheet by hand (recorded in its `manual_corrections`), and added "never expand an initial into a
guessed first name" to the schema descriptions. Lesson: abbreviated sources (gamebooks, box scores) invite this; the
auto-checker matched on surname, so it didn't catch it. A hand read did.

### 2026-11-19: Week 11 previews on base (13 games)
Preview schema: records, 2–4 players to watch with season stats, 3–5 this-season matchup notes, storyline.
13/13 completed in 100–307 s (whole week ~5 min, ~$0.13). 0 low-confidence fields, 0 off-allowlist citations;
almost every field rated medium.

- Player season stats: 40 of 41 stat lines matched ESPN's 2026 numbers exactly. The 41st (James Cook) only
  failed the auto-check on name format ("James Cook III" on ESPN).
- Team claims hand-checked against ESPN (records, division places, Mexico City venue, MIN 37 sacks first,
  TB 16 INT first, DAL 28.6 ppg, PHI 7 straight losses): all correct except one.
- 1 stale fact: "SF averages 399.8 yards per game, ranked first," cited to nfl.com's game preview page.
  After Week 10, ESPN has DET 407.6 and SF 407.2. The page hadn't caught up with last week; Parallel copied it
  faithfully and rated it medium. Corrected by hand (`manual_corrections`). Lesson: league *rankings* go stale
  fastest; they're a weak claim type for previews.
- No names needed fixing; nobody on the players-to-watch lists was injured or out (checked against ESPN rosters).

### 2026-11-19: player cards (76 players, base)
Split by strength: ESPN feeds for bio and per-season stats (official numbers); Parallel Task for fun facts,
with college and draft also requested so they can be checked. OpenAI rewrites the 3 best facts for a first grader.

- 76/76 completed, p50 70 s, max 222 s, ~$0.76. 0 low-confidence fields, 0 off-allowlist citations.
- College vs ESPN: 75/76 match. The 76th is Charlie Smyth (Irish Gaelic football convert): ESPN lists no
  college, Parallel named St. Mary's University College, Belfast.
- Fun facts: hand-read all 228 kid versions; traced the 4 most surprising to their sources (Jordan Love's
  "Pookie" nickname on PFR, C.J. Stroud's toddler dunks in an ESPN feature, Rodgers' memory story, Likely's
  2026 line). All real and cited, all rated medium. Nothing mature came through.
- Rewrite pitfall: the first kid rewrite avoided the word "interception" and turned a 97-yard college pick-six
  into "ran 97 yards after he caught a pass." Fixed by allowing football words and requiring where it happened
  (high school, college, NFL).
- Weakest facts are filler ("Gavin is 6 feet 5 inches tall"), not wrong ones.

## OpenAI writing step
- Model: gpt-6.1-terra, reasoning effort low. ~850 input / ~110 output tokens per recap, ~$0.0015 each; 14 games ~$0.02.
- 14/14 passed the length and tripwire checks on the first attempt.
- Spot-checked score swings and scorers in 5 recaps against ESPN scoring plays: no OpenAI-introduced errors.

## Search + Extract (big stories)
- 

## Decisions API (safety)

### 2026-11-19: first 5 Big Stories run
Search (fast, allowlist + `after_date`) -> Extract (full_content) -> Decisions (8 noul questions, one request
per story) -> OpenAI rank + rewrite -> Decisions again on the kid text -> tripwire. ~45 s, about $0.07/run.

- **Decisions researches the web on its own and has no source policy.** The first test (a 3-sentence clean
  Jaguars recap) scored gambling 0.26 and failed: its citations were CBS, Fox, AP etc. about the same game,
  6 of 10 off our allowlist. Telling it to judge ONLY `state.article_text` (plus `criteria`) fixed the
  scoring (max 0.03), but it still cites off-allowlist pages (8 of 10). Accepted because its output is only a
  probability; citations are logged, never shown. Documented as the one allowlist exception in CLAUDE.md.
- Sanity pair: clean story max 0.03; "torn ACL, carted off" story injury 0.98.
- **Article-level "any mention" is too strict for NFL news:** 6 of 30, then 8 of 46 articles passed, because
  almost every game story or roundup mentions an injury somewhere. Moved to two checks: article by *topic*
  (16 of 50 passed), then the finished kid text by *any mention*. All 5 published stories scored <= 0.04.
- Search pool: generic "storyline" queries returned mostly roundups. Adding one query per game from last
  week's results ("Chargers beat Ravens storylines") brought in single-game stories and spread picks across
  teams. URL-path filter drops where-to-watch, schedule and ticket pages.
- Facts in all 5 stories checked against ESPN box scores / play-by-play: correct.
- Run-to-run variance is large: three runs gave 16, 13 and 15 safe articles and different story sets.
  The editor step also under-picked (4 of 13) until told to always pick 8 with backups.
- Big words: banned an "easy word" list and required >= 6 letters; now composed, persistent, savored, triumph.
- Extract reliability: re-extracting the Aaron Donald article with `full_content: true` returned a result
  with empty `full_content` and no entry in `errors` (same URL worked in the pipeline minutes earlier).
  Excerpts mode on the same URL worked. Code treats empty text as no candidate (fails closed).
- Final 5 checked against ESPN (Daniels 2 INT, P. Washington 6 catches/148 yds in the first half,
  Rams bye then Packers, Chargers 23-20) and against the source text ("Rams' defense played so few snaps"):
  all correct. One story came from an nfl.com /series/ index page (facts right); index pages now filtered.

- 

### 2026-11-19: an allowlisted source got a fact wrong
ESPN's "Seven things Solak thinks" column described McCaffrey's late, game-sealing catch against a Cover 0
blitz and then said "A 74-yard touchdown later, the game was over." The official play-by-play has that play as
a **66-yard** TD at 3:23 of Q4; the 74-yarder was in Q2. The writer repeated the column faithfully, so the
kid story was wrong even though every step "used only the source."
- Lesson: the allowlist controls *where* facts come from, not whether they're right. Columns and roundups
  are weaker than box scores.
- Fix: stories about last week's games now get ESPN's official scoring plays as an "official record" that
  wins over the article, plus an OpenAI fact-check pass (story vs. article + official record). Tested on the
  bad story: it flagged both the yardage and the timing. Next run: 5/5 stories used the record, 1 needed a
  fact-check retry, all spot-checked details correct.

## Weekly refresh
`uv run python -m pipeline.refresh` auto-detects the last completed week (ESPN scoreboard) and runs every
step in order, reusing cached fact sheets and player research. A re-run with everything cached costs only
the stories step (~$0.07, ~1 min). Estimated fresh week: ~$0.14 game facts + ~$0.13 previews + player
research for new names only (~$0.76 the first week, less after) + ~$0.10 OpenAI + ~$0.07 stories.

## Eval results

### 2026-11-19: facts suite (30 questions, Week 10), 4 arms x 3 rounds
Harness: parallel-search-evals @ 6d29a42, `suite=local`, our judge assets (`evals/facts/judge/`), run before
the Week 11 Thursday game. Same model on every arm (gpt-6.1-terra, harness default effort), same prompts,
same 20-tool-call budget, same judge (gpt-6-sol). Both search arms limited to the 5-site allowlist.

Fork changes (local branch `lsw-eval`, not upstream):
- `include_domains=`: Parallel Search gets `source_policy.include_domains`; Extract rejects other URLs.
- `search_engine=openai-search`: OpenAI's built-in `web_search` tool with `filters.allowed_domains`,
  `max_tool_calls` = the same 20.
- Per-question sidecar metrics (tokens, tool calls, wall time) in `<run>-metrics/`; saved records unchanged.
- Upstream test suite still passes apart from 2 failures and 1 hanging Gemini test that also occur on the
  untouched clone.

Results (rounds 2 and 3; round 1 is excluded, see below):

| Arm | Correct (r2 / r3) | Cost per resolved question | p50 / p95 latency | Searches per question | Cited URLs off allowlist |
|---|---|---|---|---|---|
| baseline (closed book) | 0 / 0 | n/a ($0.075 per run, nothing resolved) | 8 s / 17 s | 0 | n/a |
| parallel-fast | 30 / 30 | $0.016 | 9.2 s / 13 s | 1.15 | 0 of 68 |
| parallel-advanced | 30 / 30 | $0.020 | 11.3 s / 16 s | 1.23 | 0 of 66 |
| openai-search | 30 / 30 | $0.022 to $0.026 | 5.2 s / 8 to 24 s | 1.27 | 0 of 64 |

Cost = model tokens ($1 in, $0.05 cached in, $5 out per 1M) + tool fees (Parallel Search $1 or $5 per 1k,
Extract $1 per 1k URLs; OpenAI web search $10 per 1k). The judge is the same on every arm and is not included.

Findings:
- **A three-way tie on accuracy.** Every search arm got every question right in both clean rounds. The suite
  doesn't separate the tools: even the "obscure" play-by-play questions were one search away on espn.com or
  nfl.com. A harder set is needed to rank them (multi-game aggregation, questions whose answer is only in a
  gamebook PDF, older seasons).
- **Cost: Parallel is cheaper on tool fees** (fast $0.07–0.09 per 30 questions vs. OpenAI $0.35–0.41) but
  sends more tokens to the model, because excerpts and fetched pages go into the context. Net per question:
  fast $0.016, advanced $0.020, OpenAI $0.022–0.026. All three are pennies.
- **Latency: OpenAI is faster at the median** (about 5 s vs 9–11 s): it searches inside a single model call,
  while the harness's Parallel loop needs a model round trip per tool call.
- **Allowlist held on both arms:** 0 cited URLs off the 5 sites. Parallel also cited `static.www.nfl.com`
  gamebook PDFs (subdomain of nfl.com, allowed).
- **Baseline mostly says "I don't know"** (27–29 of 30 per round) but invented 5 answers across 90 tries,
  e.g. Cam Little's 2025 long as 70 yards (actual 68). This is the closed-book hallucination rate (~6%).
- **Round 1 hit OpenAI rate limits.** Running all 4 arms at once (24 concurrent workers) produced 11 HTTP 429
  failures (9 on openai-search, 2 on parallel-fast). The harness doesn't retry and reuses saved failures on
  resume, so the fix was fresh output directories with arms run one at a time (4 workers). Failure
  classes in rounds 2–3: none (no missing search calls, provider errors, retrieval misses or synthesis failures).
- **Judge hand-check:** 5 of 5 grades correct on the hard cases (two-part question, "no return TD happened",
  not-played games for a search arm and baseline, a baseline "I don't know").
- **Task API check against the same answer key:** the saved Week 10 fact sheets cover 11 answer-key facts
  (3 scores, 5 QB lines, Daniels' 2 INTs, Fitzgerald's 54-yard winner, Chris Johnson's 59-yard pick-six):
  11 of 11 match. The schema doesn't hold the rest (season totals, who recovered a fumble).

| Suite | Mode | Accuracy | p50 latency | Cost per resolved question |
|---|---|---|---|---|
| Facts | baseline | 0–1 / 30 | 8 s | n/a |
| Facts | parallel-fast | 30 / 30 | 9.2 s | $0.016 |
| Facts | parallel-advanced | 30 / 30 | 11.3 s | $0.020 |
| Facts | openai-search | 30 / 30 | 5.2 s | $0.022–0.026 |
| Safety | | not run yet | | |
