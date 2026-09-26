# Architecture

This document describes the architecture of AI Code Performance Engineer as of v1.1.0. The system
is built around one rule: a generated optimization is a hypothesis until correctness and measured
performance evidence justify promoting it.

## System overview

```text
                         +-------------------+
Repository / Git ref --->| Repository runner |
                         +---------+---------+
                                   |
                  +----------------+----------------+
                  |                                 |
                  v                                 v
          +---------------+                 +---------------+
          | Static analysis|                 | Profiling     |
          | Python / JS / TS|                | cProfile /    |
          +-------+-------+                 | resources     |
                  |                         +-------+-------+
                  +-------------+-------------------+
                                |
                                v
                     +----------------------+
                     | Optimization request |
                     | findings + hotspots  |
                     | bounded source context|
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     | Candidate provider   |
                     | command / OpenAI-    |
                     | compatible / Ollama  |
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     | Candidate validation |
                     | unified diff + scope |
                     +----------+-----------+
                                |
                                v
              +-----------------+-----------------+
              |                                   |
              v                                   v
      +---------------+                    +---------------+
      | Correctness   | -- fail ---------> | Reject        |
      | gate          |                    +---------------+
      +-------+-------+
              |
              v
      +---------------+
      | Paired        |
      | benchmarking  |
      +-------+-------+
              |
              v
      +---------------+
      | Attribution + |
      | decision +    |
      | ranking       |
      +-------+-------+
              |
              v
      +---------------+
      | Promote best  |
      | verified patch|
      +-------+-------+
              |
              +----> re-analyze promoted state for next stage
              |
              v
      +---------------+
      | Final original|
      | vs final      |
      | verification  |
      +-------+-------+
              |
              v
      +---------------+
      | Reproducible  |
      | unified patch |
      +---------------+
```

## Core invariant: measurement decides

Provider confidence is never an acceptance signal. A candidate must preserve behavior and satisfy
the configured performance evidence thresholds. The verification layer uses paired measurements,
confidence intervals, stability checks, absolute runtime evidence, and CPU/memory regression
budgets. A fast but incorrect candidate is rejected before benchmarking.

This separation keeps candidate generation replaceable: deterministic fixers, external commands,
OpenAI-compatible providers, and Ollama all feed the same verification pipeline.

## Optimization states

Cumulative optimization is modeled as a sequence of promoted states:

```text
Git baseline
    |
    v
   S0
    |
    | Stage 1: generate alternatives against S0
    |          verify independently
    |          promote A
    v
   S1
    |
    | Stage 2: re-analyze S1
    |          generate fresh alternatives against S1
    |          promote B
    v
   S2
    |
   ...
```

An optimization state is derived deterministically from the baseline commit and accepted candidate
sequence. Each promoted stage records its `baseline_state` and `resulting_state`. Candidate
evaluations record the state they were measured against together with their stage and provider
attempt numbers.

This is deliberately different from treating A, B, and A+B as sequential stages. A, B, and A+B
may be *alternatives within one stage*. Only a candidate that passes the evidence gates is promoted;
the next stage starts from that promoted state and generates new hypotheses from its current source
and evidence.

## Stage and provider-attempt lifecycle

Two limits control different dimensions of the search:

- `maximum_optimization_stages` bounds how many verified changes may be promoted cumulatively.
- `maximum_provider_attempts` bounds generation/refinement attempts within one state.

A stage operates as follows:

```text
current promoted state
        |
        v
analyze / optionally profile
        |
        v
provider attempt 1
        |
        +--> validate -> correctness -> benchmark -> attribute
        |
        +--> accepted candidate? -- yes --> rank and promote best --> next stage
        |
        no
        |
        v
measured feedback + refinement hints
        |
        v
provider attempt 2 ... up to configured limit
        |
        +--> no accepted fresh hypothesis --> stop cumulative search
```

Feedback is stage-local. A later provider attempt sees measured failures from the current state,
including correctness failures, confidence, speedup intervals, CPU/memory effects, and attribution.
A newly promoted state starts with fresh analysis and fresh stage-local feedback.

## Candidate identity and deduplication

Candidate IDs are kept unique across the run for provenance. Patch deduplication is keyed by both
the current optimization state and the patch hash. This prevents repeated benchmarking of the same
patch against the same state while allowing a patch shape to be reconsidered after cumulative
source changes make it a genuinely different experiment.

## Evidence and attribution

Every measured candidate can carry a `PerformanceAttribution` connecting:

```text
finding / hotspot
       |
       v
candidate strategy
       |
       v
baseline measurements
       |
       v
candidate measurements
       |
       v
wall / CPU / memory deltas
       |
       v
correctness + confidence + decision
```

Static findings and profiler hotspots are assigned evidence identifiers and supplied to providers.
Candidate-declared evidence links are resolved back to human-readable findings or hotspots for the
stored attribution. This makes a result explain not only *whether* a patch won, but what measured
problem it attempted to address and what changed.

## Benchmarking and verification

Command benchmarks use isolated processes. Explicit Python callable benchmarks use persistent
baseline and candidate interpreters to avoid interpreter startup dominating short algorithmic
measurements.

Paired measurements alternate AB/BA execution order to reduce temporal and thermal bias. Adaptive
sampling begins with the configured minimum rounds and can expand to `maximum_rounds` when the
evidence remains noisy.

Acceptance requires correctness plus the configured performance evidence. Inconclusive evidence is
not silently converted into acceptance. Resource budgets can independently reject candidates with
unacceptable CPU or memory regressions.

## Worktree isolation

The optimizer resolves the requested Git baseline and creates detached temporary worktrees for
candidate evaluation. Accepted candidates are replayed into fresh worktrees to construct the
current cumulative state. Temporary worktrees are removed and pruned after use, including failure
paths.

The optimizer does not automatically commit generated model output to the user's repository.

## Final cumulative verification

Per-stage acceptance is not the final proof. After the cumulative search ends, the accepted
sequence is reconstructed from the original Git baseline in a fresh worktree.

The engine then:

1. reapplies the complete accepted sequence;
2. reruns the correctness command on the final state;
3. directly benchmarks the original baseline against the final cumulative state;
4. runs the same verification decision logic on that comparison; and
5. creates a single original-to-final Git diff.

The resulting `final_verification` answers whether the cumulative result still holds when measured
end to end. The `composed_patch` is the reviewable artifact that reproduces the final source state
without requiring consumers to replay intermediate candidate patches.

## Persistence and provenance

Optimization runs are serialized as versioned JSON records. A run captures the baseline commit,
environment fingerprint, baseline measurements, optional baseline profile, all candidate
evaluations, provider-attempt count, promoted stages, winner, final verification, and composed
patch.

Audit logging is hash-chained. Provider source context is secret-redacted before transmission while
retaining original file hashes and redaction counts for provenance.

## Execution and security boundaries

Local execution sanitizes the environment, applies resource limits, and terminates process groups
on timeout. The optional Docker backend runs workloads with networking disabled, a read-only
filesystem, dropped Linux capabilities, process/memory quotas, and `no-new-privileges`.

These controls reduce risk; they do not make arbitrary repositories safe. Benchmark and correctness
commands execute repository code, so users should only run the tool against code they trust.

## Main implementation boundaries

| Responsibility | Primary module |
| --- | --- |
| Cumulative optimization orchestration, states, provenance | `optimizer.py` |
| Static performance findings | `analyzer.py` |
| Provider contracts and integrations | `providers.py` |
| Command benchmarking and paired measurement | `benchmark.py` |
| Persistent callable benchmarking | `callable_benchmark.py` |
| Correctness and evidence-based comparison | `verification.py` |
| Patch validation/application | `patches.py` |
| Profiling adapters | `profiling.py` |
| Execution policy and runners | `execution.py` |
| CLI surface and artifact export | `cli.py` |
| Audit chain | `audit.py` |

## Design principles

1. **Correctness precedes speed.** Broken candidates never earn acceptance through performance.
2. **Measurement outranks model confidence.** Provider metadata can guide search, not the verdict.
3. **Alternatives and stages are different concepts.** Candidates compete within a state; accepted
   optimizations accumulate across states.
4. **Noisy evidence stays inconclusive.** The engine expands measurement rather than overstating a
   result.
5. **Every promotion is attributable.** State, stage, attempt, evidence, measurements, and decision
   remain inspectable.
6. **The final cumulative result is verified directly.** Incremental wins are not assumed to compose.
7. **Provider implementations remain replaceable.** The deterministic measurement core does not
   depend on a particular AI service.
8. **Generated code remains reviewable.** The final output is a patch, not an automatic commit.
