# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Automatic Python baseline profiling that prioritizes repository-owned runtime hotspots in AI
  candidate context, with an opt-out for unsupported or externally profiled workloads.
- Bounded AI refinement attempts that feed correctness failures, benchmark confidence, speedup,
  CPU cost, and memory cost back to the provider when no initial candidate is acceptable.
- Structured candidate strategy, expected-impact, and risk metadata with cross-attempt patch
  deduplication and identifier collision handling.

### Changed

- Optimization records use schema version 4 and retain the normalized baseline profile and number
  of provider attempts used to guide candidate generation.

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

[1.0.0]: https://github.com/achalsood/ai-code-performance-engineer/releases/tag/v1.0.0
[Unreleased]: https://github.com/achalsood/ai-code-performance-engineer/compare/v1.0.0...HEAD
