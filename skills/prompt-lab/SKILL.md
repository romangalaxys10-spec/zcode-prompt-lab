---
name: prompt-lab
description: A/B test two prompt or skill variants against the same fixtures and score them on a declared weighted rubric. Use when the user asks to compare two prompts, two skill versions, two system messages, or two agents head to head on a task, or to pick the better of two instructions before committing to one.
---

# prompt-lab

Compare variant A and variant B of a prompt or skill on the same fixtures.
The goal is a defensible verdict, not a vibes impression. Fresh subagents
per run so variant A never sees variant B's output.

## What the harness can and cannot auto-score

The scorer (`scripts/score_outputs.py`, stdlib only) is deterministic for
objective criteria and honest about the rest:

- `contains` criteria: every listed needle must appear, case-insensitive.
- `regex` criteria: every listed pattern must match (DOTALL). Invalid
  patterns fail the check and say why.
- A fixture's `expected_properties`: plain substrings are auto-checked as
  present; properties phrased "does not contain X", "no X", "without X" are
  auto-checked as absent. Anything more subjective is written out as manual.
- `manual` criteria (and any uncheckable property): never auto-scored. The
  scorer marks them `judged: false` with a `judge` field. The agent judges
  them, writes the judgment back, and the aggregator includes it.

The excluded portion of the total is reported as `pending_manual`, so a
verdict is never silently made on partial data.

## Inputs the user must provide

1. **Variant A** and **variant B**: each one of
   - a path to a prompt file,
   - an inline prompt string,
   - a skill name to invoke.
2. **Fixtures**: a JSON file, a JSON array, or a directory of `.json`
   fixture files. Each fixture:
   `{"id": "...", "input": "...", "expected_properties": ["..."]}`.
   `input` is what the variant receives. `expected_properties` are short
   checkable strings ("contains `--quiet`", "does not contain `TODO`",
   "no invented numbers"). Keep them checkable: the harness prefers
   substring-absence/presence, and marks the rest for the agent.
3. **Rubric**: JSON, a non-empty `criteria` list. Each criterion:
   `{"id": "...", "type": "contains|regex|manual", "weight": 1.0,
   "needles": [...] | "patterns": [...] | "description": "..."}`.
   Weights may be any non-negative number.
4. **runs_per_fixture** (default 1). Raise to 2 or 3 when a single run is
   not enough to average out luck.

## Workflow

### 1. Validate up front

```bash
python3 scripts/score_outputs.py --check-rubric <rubric.json>
python3 scripts/score_outputs.py --check-fixtures <fixtures.json>
```

Both fail fast on malformed JSON with the exact reason. If either fails,
stop and ask the user to fix the file. Do not invent the missing parts.

### 2. Run both variants on every fixture

For each fixture, for each variant, for each run:

- Spawn a **fresh subagent** via the Agent tool. No shared context between
  runs. The subagent receives exactly: the variant's prompt (or skill
  instruction) plus the fixture's `input`, nothing else. No prior variant's
  output, no `expected_properties`, no rubric. This is the no-contamination
  rule, and it is why each run is its own agent.
- Save the subagent's full output to
  `<workdir>/<fixture-id>/<variant>-run<n>.txt` (n starts at 1).
- Append one record per run to `<workdir>/<fixture-id>/runs.json`:
  fixture id, variant, run number, prompt source, UTC timestamp. This is
  the contamination audit trail. A runs.json with no entries for a run is a
  red flag, so keep it in lockstep with the output files.

Run count is fixtures x variants x runs_per_fixture subagents. Tell the
user the count and the approximate cost before starting.

### 3. Score the objective criteria

```bash
python3 scripts/score_outputs.py \
  --rubric <rubric.json> --fixture <fixtures.json> \
  --out-dir <workdir> --variants A B [--runs-per 1]
```

Writes one `scores.json` per variant under `<workdir>/<variant>/`.
Objective `contains`/`regex` criteria and auto-checkable
`expected_properties` carry a pass/fail right now. Manual criteria come
back with `"judged": false` and a `judge` field. Missing output files are
scored as empty, with a warning listing them, and a `pending_manual`
counter is printed.

### 4. Judge the manual criteria yourself

For each criterion or property the script marked `judged: false`:

- Read the variant's output against the criterion's `description`
  (or the property's own wording). Be literal: the description is the
  rubric.
- If you cannot tell from the output text, keep `judged: false` and say
  why in the report. Do not score on how the output *sounds*.
- Write each judgment back into `<workdir>/<variant>/scores.json`: set
  `judged: true`, set `pass`, add a one-line `reason`. The script
  preserves these on re-scores.

Then aggregate:

```bash
python3 scripts/score_outputs.py \
  --rubric <rubric.json> --out-dir <workdir> \
  --aggregate --variants A B
```

Prints the two totals, a per-fixture table, and a verdict line. The total
includes only judged or objective criteria; anything still unjudged shows
in `pending_manual`.

### 5. Write the comparison

- `<workdir>/comparison.md`: one table, row per fixture, columns
  A score, B score, and per-criterion pass/fail.
- One-line verdict naming the single diff that mattered most. The diff is
  the prompt or skill wording difference that explains the score gap, not
  a paraphrase of the score gap.
- If `runs_per_fixture` was 1 and the totals are within 0.10, say so
  instead of picking a winner. One run per fixture is a sample of size 1.

## Honest limits

- Only `contains`, `regex`, and substring-form `expected_properties` are
  auto-scored. Anything requiring reasoning is the agent's call, and that
  call is not deterministic. `pending_manual` tells you how much of the
  verdict rests on it.
- This compares instructions. It does not benchmark the model behind the
  subagents.
- An empty output is scored as failing its checks, not as a harness
  error. Check the warning list before trusting a near-zero total.
