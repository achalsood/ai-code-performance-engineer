# PerfArena Roadmap

## Phase 1 — Foundation (now)

- publish benchmark philosophy and specification
- formalize challenge metadata
- add machine-readable result/scorecard schema
- expand the seed corpus from 3 to 20 reviewed cases
- add category-level reporting

## Phase 2 — Agent evaluation

- adapter for optimization-agent submissions
- run the same challenge set against multiple providers/models
- record inference attempts and cost metadata when available
- separate generation failures, correctness failures, inconclusive measurements, regressions, and accepted improvements

## Phase 3 — Public evidence

- grow to 100 challenges
- generate a static leaderboard/report from checked-in run artifacts
- publish raw result JSON alongside every chart
- add a short terminal demo showing propose -> test -> benchmark -> verdict
- document surprising failures and regressions, not only wins

## Phase 4 — Community benchmark

- contribution template for new challenges
- benchmark-review checklist
- CI validation for challenge determinism
- versioned benchmark releases
- external agent submission format

## Launch criterion

PerfArena is ready for a public launch when another engineer can clone the repository, reproduce a scorecard, inspect raw evidence, and understand why each optimization was accepted or rejected without trusting the model that proposed it.
