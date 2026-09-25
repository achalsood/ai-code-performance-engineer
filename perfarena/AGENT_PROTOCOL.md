# PerfArena Agent Protocol

PerfArena separates **generation** from **judging**. An optimization agent receives the baseline source and task contract, but not the reference candidate implementation or measured reference result.

## Agent input

Each challenge exposes:

- challenge ID, category, and language
- baseline source files
- objective: improve runtime or memory without changing observable behavior
- public correctness command
- benchmark command

The reference candidate directory is evaluator-only data. It exists to validate the benchmark itself and must never be included in an agent prompt.

## Agent output

Agents return one or more candidates using the existing provider contract:

```json
{
  "candidates": [
    {
      "candidate_id": "agent-1",
      "title": "Short description",
      "rationale": "Why this should improve performance",
      "strategy": "algorithmic|data-structure|allocation|repeated-work|io|other",
      "expected_impact": "Expected effect",
      "risk": "Correctness/performance risk",
      "patch": "unified diff"
    }
  ]
}
```

## Fairness rules

1. The same public challenge input is supplied to every compared agent.
2. Reference optimized implementations and prior measured results are hidden.
3. Agents may not edit tests, benchmark workloads, PerfArena infrastructure, or challenge metadata.
4. A candidate that fails correctness receives no accepted optimization.
5. Model/provider failures are recorded rather than silently retried away. A configured retry policy must be identical across compared agents.
6. Generation metadata should include provider/model identity, attempt count, and cost/token information when the provider exposes it.
7. Performance is measured by PerfArena, never self-reported by the agent.

## Result taxonomy

Every attempt is classified as one of:

- `accepted` — correct and clears the configured performance/statistical gates
- `rejected` — correct but fails the performance gate or violates a resource budget
- `correctness-failure` — patch applies but public/hidden correctness validation fails
- `invalid` — malformed or unsafe patch
- `provider-failure` — the agent fails to produce a valid response
- `inconclusive` — measurement cannot establish the required improvement

## Comparison metrics

Public comparisons should report at least:

- challenge attempts
- valid-patch rate
- correctness rate
- accepted optimization rate
- median accepted speedup
- regressions/inconclusive results
- per-category results
- generation cost and latency when available

Raw artifacts should accompany aggregate charts so individual outcomes remain inspectable.
