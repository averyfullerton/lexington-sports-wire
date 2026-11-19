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

## Eval results
| Suite | Mode | Accuracy | p50 latency | Cost |
|---|---|---|---|---|
| Facts | baseline | | | |
| Facts | parallel-fast | | | |
| Facts | parallel-advanced | | | |
| Safety | — | catches / misses | | |
