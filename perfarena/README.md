# PerfArena

PerfArena is a reproducible benchmark for answering one question:

> Did an optimization actually make working code faster?

It is designed to evaluate optimization systems—not to reward persuasive model output. Every submission must pass correctness checks before its performance result counts.

## Scorecard

PerfArena records, per challenge:

- correctness verdict
- acceptance verdict
- wall-clock speedup
- CPU-time change
- peak-memory change
- raw benchmark samples
- reproducible environment metadata

Aggregate reports include correctness rate, acceptance rate, median speedup, and a deterministic 95% bootstrap confidence interval.

## Design rules

1. Correctness is a hard gate.
2. Measurement, not model confidence, decides acceptance.
3. Baseline and candidate runs alternate AB/BA to reduce temporal and thermal bias.
4. Raw measurements are retained.
5. Challenges must have deterministic tests and a representative workload.
6. Benchmark results are evidence, not universal claims; environment metadata travels with every run.

## Current state

The repository contains a 20-case categorized benchmark plus a blind agent runner for comparing optimization systems.

## Planned challenge categories

- algorithms and asymptotic complexity
- data structures and membership/indexing
- repeated/invariant work
- parsing and serialization
- allocation and memory pressure
- numerical workloads
- I/O and batching
- concurrency (where reproducible)
- JavaScript/TypeScript runtime patterns

The first milestone is a 20-case public suite. The launch target is 100 reviewed challenges.

## Run the benchmark suite

```bash
python -m pip install -e '.[dev]'
perf-engineer evaluate \
  --corpus benchmarks/corpus.json \
  --rounds 9 \
  --history .perf-engineer/history.jsonl \
  --report .perf-engineer/report.md
```

PerfArena does not claim an optimizer is good because it produced a patch. It asks whether the patch stayed correct and survived measurement.


## Run a blind AI experiment in GitHub Actions

The `PerfArena Experiment` workflow is manually triggered so model calls never run on ordinary pushes or pull requests. Add an `OPENAI_API_KEY` repository secret, choose the model ID when dispatching the workflow, and GitHub Actions will run the full blind corpus.

The workflow uploads `perfarena-agent.json` as a 90-day artifact. The artifact contains the per-case verdicts and aggregate scorecard while the API key remains available only through the GitHub Actions secret environment.
