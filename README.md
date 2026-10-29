# Prompt Lab

A/B harness for prompts and skills: two variants, the same fixtures, a
declared weighted rubric, and a verdict that names the diff that mattered.

## Why

Prompt comparisons usually end as a vibes call: try each variant on a task
you like, pick the one that sounds better, commit to it. That is not a test.
It is one sample, contaminated by your own reading of both outputs, with no
shared criteria. Prompt Lab forces the three things a comparison needs:
identical inputs, declared criteria, and fresh subagents so variant A never
sees variant B.

## How it works

1. You provide two variants (prompt files, inline prompts, or skill names),
   a fixture set (`{"id", "input", "expected_properties"}`), and a rubric
   (weighted criteria of type `contains`, `regex`, or `manual`).
2. The skill validates both files up front, then runs every fixture through
   both variants in fresh subagents (one agent per run, no shared context),
   saving outputs and a `runs.json` contamination audit trail.
3. `scripts/score_outputs.py` (stdlib only) scores what it can:
   `contains` needles, `regex` patterns, and substring-form
   expected properties. Manual criteria come back marked `judged: false`
   for the agent to fill in; the total reports `pending_manual` so a verdict
   is never made on hidden partial data.
4. You get `<workdir>/comparison.md`: one row per fixture, A vs B scores,
   and a one-line verdict naming the wording diff behind the gap. If
   `runs_per_fixture` is 1 and the gap is under 0.10, it says draw instead of
   picking.

## Install

Option A: self-installable marketplace.

1. Clone or download this folder.
2. In ZCode: Plugin Marketplace -> Add -> Add Plugin Marketplace, paste the
   folder path.
3. Install `prompt-lab`.

Option B: copy the folder into your workspace `plugins/` directory.

## Try it

```
/prompt-lab prompts/summarize-v1.md prompts/summarize-v2.md fixtures.json rubric.json runs=2
```

Expected: a fixture x variant x run count, fresh subagent runs, a
`comparison.md` with per-fixture scores, and a verdict line (or an explicit
draw note if the gap is within noise at the chosen run count).

## Requirements

- Python 3.8+ (stdlib only) for the scorer.
- The Agent tool, for the no-contamination subagent runs.
- Honest limit: only objective criteria auto-score. Manual judgments are the
  agent's call and are not deterministic; the report shows how much of the
  verdict rests on them.

---

<p align="center">
  <a href="https://z.ai/subscribe?ic=R0K78RJKNW"><img alt="z.ai coding plans - 10% OFF coupon" src="https://img.shields.io/badge/z.ai%20coding%20plans-10%25%20OFF%20coupon-blueviolet" /></a>
  <a href="https://zcode.z.ai/invite/ALWM9JUA"><img alt="Try ZCode free" src="https://img.shields.io/badge/Try%20ZCode-free%20invite-0969da" /></a>
</p>

## License

MIT.
