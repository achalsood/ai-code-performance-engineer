# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-09-26

### Added

- Automatic Python baseline profiling that prioritizes repository-owned runtime hotspots in AI
  candidate context, with an opt-out for unsupported or externally profiled workloads.
- Bounded measurement-guided provider refinement with correctness, confidence, speedup, CPU, and
  memory feedback.
- Structured candidate strategy, expected-impact, and risk metadata.
- Cumulative multi-stage optimization that promotes the best verified candidate, re-analyzes the
  resulting state, and generates fresh alternatives for the next stage.
- Direct original-vs-final correctness and paired performance verification after cumulative
  optimization.
- Reproducible original-to-final unified patch export for the accepted optimization sequence.
- Deterministic optimization-state identities and stage/attempt provenance for candidate
  evaluations.

### Changed

- Provider refinement attempts and promoted optimization stages now have independent limits.
- Patch deduplication is state-aware, allowing the same patch text to be reconsidered when the
  cumulative source state has changed.
- Optimization stages record their actual baseline and resulting state identities instead of
  representing cumulative states as the original Git commit.
- Performance attribution and refinement feedback are scoped to the state and stage that produced
  each candidate.
- PERF004 analysis recognizes invariant sequence membership inside loops while excluding
  loop-bound containers and known sets.
- Optimization records use schema version 6 and retain cumulative stages, provider-attempt counts,
  the composed final patch, and final verification evidence.

## [1.0.0] - 2026-09-03

### Added

- Python AST performance analysis and JSON diagnostics.
- Reproducible wall-clock, CPU, and peak-memory benchmarks.
- Correctness, stability, and minimum-improvement acceptance gates.
- Isolated Git worktrees and versioned experiment records.
- Structured multi-candidate AI optimization and safe unified-diff validation.
- Command, OpenAI-compatible, and Ollama provider integrations.
- Local OS limits, optional Docker isolation, and tamper-evident audit logs.
- Reproducible optimization corpus, confidence intervals, history, and regression reports.
- Python 3.11–3.13 CI, benchmark artifacts, and automated GitHub releases.
- Bootstrap confidence gates that require the lower 95% bound to clear the threshold.
- Persisted optimization records and safe export of winning patches for human review.
- Bounded fallback source context when static analysis produces no findings.
- Tree-sitter-based JavaScript and TypeScript performance analysis.
- Normalized resource and Python cProfile adapters with ranked hotspots.
- Mixed Python/JavaScript evaluation corpus and Node.js CI coverage.
- Per-process physical-memory monitoring compatible with V8 virtual heap reservation.
- Dependency-tree pruning for scalable repository discovery and SARIF code-scanning output.
- Process-group memory accounting plus bounded, retrying provider HTTP requests.
- Secret-redacted provider context, original-source hashes, and environment fingerprints.
- Coverage enforcement, Dependabot, release SBOMs, and build-provenance attestations.
- Hotspot-guided prompts, adaptive paired benchmarking, resource regression budgets, and
  conservative multi-objective candidate ranking.

[1.1.0]: https://github.com/achalsood/ai-code-performance-engineer/releases/tag/v1.1.0
[1.0.0]: https://github.com/achalsood/ai-code-performance-engineer/releases/tag/v1.0.0
[Unreleased]: https://github.com/achalsood/ai-code-performance-engineer/compare/v1.1.0...HEAD
