# AI Code Performance Engineer

[![CI](https://github.com/achalsood/ai-code-performance-engineer/actions/workflows/ci.yml/badge.svg)](https://github.com/achalsood/ai-code-performance-engineer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/achalsood/ai-code-performance-engineer)](https://github.com/achalsood/ai-code-performance-engineer/releases)
[![Python](https://img.shields.io/badge/python-3.11--3.13-blue)](https://www.python.org/)

An evidence-driven developer tool that finds performance risks, measures candidate changes,
checks correctness, and accepts an optimization only when the data supports it.

The central rule is simple: **AI may propose a patch; measurement decides whether it ships.**

## What works today

- Python, JavaScript, and TypeScript AST analysis for common performance risks
- Isolated command benchmarks with warmups, timeouts, deterministic hash seeds, and raw samples
- Median/variance-based comparison instead of trusting a single timing
- Correctness gate that rejects fast but broken candidates
- Machine-readable JSON reports and CI across Python 3.11–3.13
- Isolated Git worktrees and durable, versioned experiment records
- Wall-clock, CPU-time, and peak-memory measurements
- Provider-independent AI candidate generation with strict JSON contracts
- Safe unified-diff validation and measured multi-candidate ranking
- Resource-limited execution, optional network-disabled Docker isolation, and hash-chained audits
- Reproducible benchmark corpus, confidence intervals, history, and regression reports
- Python, JavaScript, and TypeScript AST analysis plus normalized profiler adapters

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

perf-engineer analyze src
perf-engineer benchmark "python examples/workload.py" --rounds 9
```

Export findings into GitHub code scanning or fail CI on severe risks:

```bash
perf-engineer analyze src --format sarif --output performance.sarif
perf-engineer analyze src --fail-on high
```

Run a complete experiment directly from two Git revisions:

```bash
perf-engineer experiment \
  --repository . \
  --baseline-ref main~1 \
  --candidate-ref main \
  --benchmark "python benchmark.py" \
  --test "python -m pytest"
```

The tool checks out both revisions into temporary detached worktrees, runs the correctness and
measurement gates, writes a versioned record under `.perf-engineer/experiments`, and cleans up
the worktrees even after a failure.

Generate and evaluate AI proposals through any command that accepts an `OptimizationRequest`
as JSON on stdin and returns `{\"candidates\": [...]}` on stdout:

```bash
perf-engineer optimize \
  --repository . \
  --provider-command "python integrations/my_agent.py" \
  --benchmark "python benchmark.py" \
  --test "python -m pytest" \
  --maximum-candidates 3
```

For repositories you do not fully trust, use the Docker backend:

```bash
perf-engineer optimize \
  --repository . \
  --provider-command "python integrations/my_agent.py" \
  --benchmark "python benchmark.py" \
  --test "python -m pytest" \
  --sandbox docker \
  --memory-mb 1024 \
  --timeout 30
```

The container runs with networking disabled, a read-only filesystem, all Linux capabilities
dropped, process and memory quotas, and `no-new-privileges`. Local execution also sanitizes the
environment, applies OS resource limits, and kills the entire process group on timeout.
Local memory enforcement monitors physical resident memory, allowing V8 and other runtimes to
reserve virtual address space without being mistaken for actual memory consumption.

Run the checked-in evaluation corpus and generate a report:

```bash
perf-engineer evaluate \
  --corpus benchmarks/corpus.json \
  --rounds 9 \
  --history .perf-engineer/history.jsonl \
  --report .perf-engineer/report.md
```

The mixed Python/JavaScript report includes correctness and acceptance rates, median speedup, a deterministic 95%
bootstrap confidence interval, per-case outcomes, and regressions against the previous run.

Collect a portable resource profile or Python function-level hotspots:

```bash
perf-engineer profile "python workload.py" --adapter resource
perf-engineer profile "python workload.py" --adapter cprofile --output profile.json
```

Candidate patches cannot create, delete, rename, or modify non-Python files. Each valid patch is
applied in a disposable worktree, tested, benchmarked, and ranked. Model confidence never affects
the acceptance decision.

Built-in providers are also available:

```bash
# OpenAI or an OpenAI-compatible endpoint
OPENAI_API_KEY=... perf-engineer optimize \
  --repository . --provider openai --model YOUR_MODEL \
  --benchmark "python benchmark.py" --test "python -m pytest"

# Local Ollama
perf-engineer optimize \
  --repository . --provider ollama --model qwen2.5-coder \
  --benchmark "python benchmark.py" --test "python -m pytest"
```

Evaluation uses alternating AB/BA execution order to reduce temporal and thermal bias. Audit
appends read only the final hash-chain record, keeping logging constant-time as histories grow.
Optimization decisions require the lower bound of a bootstrapped 95% speedup interval to clear
the configured threshold. Every run is saved as JSON and an accepted winner is exported as a
reviewable unified-diff patch; the tool never commits model output automatically.

Compare the same workload in two separate worktrees:

```bash
perf-engineer verify \
  --baseline /tmp/project-before \
  --candidate /tmp/project-after \
  --benchmark "python benchmark.py" \
  --test "python -m pytest" \
  --minimum-improvement 5
```

The command exits `0` only for an accepted candidate, `2` for a rejected or inconclusive
candidate, and `1` for an execution error. This makes the verdict usable in CI.

## Architecture

```text
Repository -> Static analysis -> Candidate patch -> Correctness gate
                                      |                  |
                                      +-> Benchmark -----+
                                               |
                                        Accept / Reject
```

The current release establishes the deterministic core and repository runner. Planned layers
are profiler adapters, an optional LLM candidate provider, sandboxed containers, ranked
multi-candidate search, and a historical evaluation dataset.

## Engineering principles

1. Never treat model output as proof.
2. Preserve behavior before optimizing it.
3. Store raw measurements, not just summaries.
4. Reject noisy experiments instead of overstating results.
5. Keep provider-specific AI behind interfaces so the engine remains testable offline.

## Security note

Benchmark and test commands execute local code. Only run the tool against repositories you
trust. Container isolation and resource quotas are part of the next milestone.

## Development

```bash
python -m pip install -e '.[dev]'
ruff check .
mypy src
pytest
```

## Roadmap

- **M1 — Measurement core:** static analysis, benchmark runner, verification gate (current)
- **M2 — Repository runner:** Git worktrees, CPU/memory profiling, persistent records (current)
- **M3 — AI optimization:** structured candidate generation and multi-candidate ranking (current)
- **M4 — Hardening:** container isolation, quotas, audit logs, property checks (current)
- **M5 — Evaluation:** reproducible corpus, effectiveness metrics, regression reports (current)

## Release status

Version 1.0.0 implements the complete evidence-driven optimization pipeline. See
[CHANGELOG.md](CHANGELOG.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
