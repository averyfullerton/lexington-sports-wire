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

## OpenAI writing step
- Model: gpt-6.1-terra, reasoning effort low. ~850 input / ~110 output tokens per recap, ~$0.0015 each; 14 games ~$0.02.
- 14/14 passed the length and tripwire checks on the first attempt.
- Spot-checked score swings and scorers in 5 recaps against ESPN scoring plays: no OpenAI-introduced errors.

## Search + Extract (big stories)
- 

## Decisions API (safety)
- 

## Eval results
| Suite | Mode | Accuracy | p50 latency | Cost |
|---|---|---|---|---|
| Facts | baseline | | | |
| Facts | parallel-fast | | | |
| Facts | parallel-advanced | | | |
| Safety | — | catches / misses | | |
