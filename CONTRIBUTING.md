# Contributing

Thank you for improving AI Code Performance Engineer.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Node.js 22 or newer is required to run the complete mixed-language corpus.

Before opening a pull request, run:

```bash
ruff check .
mypy src
pytest
perf-engineer evaluate --corpus benchmarks/corpus.json --rounds 5
```

## Pull requests

- Keep changes focused and add tests for observable behavior.
- Include benchmark evidence for performance claims.
- Never weaken correctness or sandbox checks to improve a benchmark.
- Use Conventional Commit prefixes such as `feat:`, `fix:`, `perf:`, `docs:`, and `test:`.
- Update `CHANGELOG.md` for user-visible changes.

## Adding a corpus case

Add baseline and candidate directories under `benchmarks/`, give both the same benchmark and
correctness entry points, and register the case in `benchmarks/corpus.json`. Cases must be
deterministic, offline, fast enough for CI, and materially large enough to rise above process
startup noise.

## Security

Do not include credentials, private source, or proprietary benchmark data. Treat repository code
as untrusted and use `--sandbox docker` when reviewing unfamiliar contributions.
