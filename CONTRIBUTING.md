# Contributing to smerisk

## Setup

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
```

## Guidelines

- **One documentation page per formula:** every added or changed formula must have a corresponding
  `docs/formulas/<function>.md` page, and the table in [`docs/reference.md`](docs/reference.md) must be updated in the
  same pull request.
- **No calculation rules in `service/`:** the service validates input, calls the library, and formats responses.
- **Reproducibility:** every randomized function accepts a seed; identical input, seed, and version produce
  identical results.
- **Tests:** cover a hand-calculated case, useful properties, and the exact solution where one exists.
- **No real data:** do not include real data in the repository, examples, tests, or issues. Do not include names,
  national identifiers, or bank-account numbers.
- Warning and error codes are stable: adding one is a minor release; removing one is a major release.

## Versions and releases

The version is written in one place, `src/smerisk/_version.py`; the library and the service both read it. It follows
[Semantic Versioning](https://semver.org/):

| Change | Version |
| --- | --- |
| Bug fix, same contracts, same results except the bug | patch: 0.1.0 → 0.1.1 |
| New function, new optional field, new warning code; former calls give the same results | minor: 0.1.1 → 0.2.0 |
| Contract or method change that alters results, removed warning or error code, new random-number generator | major: 0.x → 1.0.0 |

Before 1.0.0, a minor release may still change a contract; the changelog says so. The HTTP routes keep `/v1` until a
major release adds `/v2`; `/v1` is then served for at least 12 months.

To publish version `0.1.0`:

1. Set `__version__ = "0.1.0"` in `src/smerisk/_version.py`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.1.0] - YYYY-MM-DD` and open a new empty `## [Unreleased]`.
3. Commit (`Release 0.1.0`), then `git tag v0.1.0` and `git push origin main v0.1.0`.
4. The `Publish` workflow checks that the tag matches the version, runs the tests, and publishes the library on
   TestPyPI, then PyPI. The service is not published: it is run from a clone (`git checkout v0.1.0` for that version).
5. Set `__version__ = "0.1.0."` and commit, so that development builds are never mistaken for the release.

## Formula page template

Each `docs/formulas/<function>.md` page should cover:

1. **Purpose:** the problem solved, in two or three sentences, with a concrete SME example.
2. **Python usage:** a complete, executable example and its actual output, when the function is implemented.
3. **HTTP usage:** the JSON request and endpoint response. Clearly mark specified-but-unimplemented endpoints.
4. **Input:** a field table with type, requirement, unit, and default.
5. **Calculation:** equations, the method's common name, and a published reference.
6. **Output:** a table describing each `result` field.
7. **Warnings and errors:** possible codes and their triggers.
8. **Limitations:** assumptions and cases where the method is not appropriate.

Never present a planned example response as the output of an unimplemented function. Examples for implemented
functions must be covered by tests and must match the library's actual output.
