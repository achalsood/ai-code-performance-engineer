# PerfArena Benchmark Specification

## Goal

PerfArena measures an optimization system's ability to produce behavior-preserving performance improvements under a fixed evaluation protocol.

## Challenge contract

Each challenge has:

- a stable `case_id`
- a human-readable description
- a baseline implementation
- a candidate implementation or generated submission
- a deterministic correctness command
- a representative benchmark command
- a minimum improvement threshold
- a category
- optional runtime/language metadata

A candidate that fails correctness receives no accepted performance result.

## Measurement protocol

The evaluator warms both implementations, then alternates execution order by round:

```text
round 1: baseline -> candidate
round 2: candidate -> baseline
round 3: baseline -> candidate
...
```

This reduces bias from machine warm-up, background load, and thermal drift. PerfArena stores raw wall-clock samples and also records CPU time and peak resident memory.

## Acceptance

A candidate is accepted only when:

1. correctness passes;
2. measured improvement clears the challenge threshold under the engine's statistical verification rules; and
3. configured CPU and memory regression budgets are respected where applicable.

An inconclusive measurement is not converted into a success.

## Aggregate metrics

The public scorecard reports:

- attempted challenges
- correctness rate
- accepted optimization rate
- median speedup
- 95% bootstrap interval over per-case speedups
- regressions / slower candidates
- per-category outcomes

No single aggregate number is intended to hide failed cases.

## Reproducibility

Every published run should include:

- repository commit
- benchmark schema version
- operating system and architecture
- Python/runtime version
- CPU identity when available
- raw samples
- configuration and thresholds

Cross-machine results should not be treated as directly interchangeable without normalization or rerunning on a common runner.

## Anti-gaming principles

Challenges should test observable behavior, not implementation shape. Hidden or expanded correctness tests may be used for public competitions. Benchmark workloads must be large enough to dominate process-launch noise and small enough to run repeatedly.

Submissions must not special-case case IDs, inspect expected outputs, disable tests, alter benchmark harnesses, or perform network access during evaluation.
