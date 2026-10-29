---
description: A/B test two prompt or skill variants on the same fixtures and score them on a declared rubric
allowed-tools: Agent, Read, Write, Bash, Edit
argument-hint: <variant-A> <variant-B> <fixtures> <rubric> [runs=N]
---
Invoke the `prompt-lab` skill with this input: **$ARGUMENTS**

Arguments: variant A and variant B (prompt file paths, inline prompts, or
skill names), then the fixtures file and the rubric file, optionally
`runs=3`.

If no arguments are given: ask for all four inputs (two variants, fixtures,
rubric). Do not start the run until every input is a real file or string;
a half-declared harness only produces an uninterpretable verdict.
