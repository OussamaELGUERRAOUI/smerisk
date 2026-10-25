# `brier`

## Purpose

Evaluates probabilistic forecasts against observed binary outcomes and checks whether forecasts are calibrated.
For example, a business can assess whether opportunities forecast at 70% actually convert about 70% of the time.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Ten past deal-signing probabilities and what happened:

```python
from smerisk import brier

past = [(0.9, 1), (0.8, 1), (0.7, 0), (0.3, 0), (0.6, 1), (0.2, 0), (0.85, 1), (0.4, 1), (0.1, 0), (0.65, 1)]
result = brier(forecasts=[{"p": p, "outcome": o} for p, o in past], n_bins=5)
print(round(result.result.brier_score, 4))  # 0.1345
print(round(result.result.skill_score, 3))  # 0.44: better than always announcing the observed rate
print(
    [(b.lower, b.count) for b in result.result.calibration]
)  # [(0.0, 1), (0.2, 2), (0.4, 1), (0.6, 3), (0.8, 3)]: bins are [lower, upper)
```

## HTTP

`POST /v1/validation/brier` takes the body below and returns the response envelope.

```json
{
  "forecasts": [
    {"p": 0.8, "outcome": 1},
    {"p": 0.3, "outcome": 0}
  ],
  "n_bins": 10
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `forecasts[].p` | rate | Yes | Forecast probability from 0 to 1 |
| `forecasts[].outcome` | `0` or `1` | Yes | Observed outcome |
| `baseline` | `climatology` or rate | No | Default `climatology`; use observed event rate or a supplied reference rate |
| `n_bins` | integer, 1 to 100 | No | Number of calibration bins; default `10` |

## Calculation

The Brier score is the mean squared probability error:

```text
BS  = (1/N) * sum((p[i] - outcome[i])^2)
BSS = 1 - BS / BS_reference
```

Lower Brier score is better. Positive Brier skill score means the forecast beats the baseline. The `climatology`
reference announces the observed event rate for every case. Calibration groups forecasts into equal-width bins on
[0, 1] (a probability of exactly 1 goes in the last bin) and compares average forecast probability with observed
event frequency; empty bins are omitted.

Reference: Brier, “Verification of Forecasts Expressed in Terms of Probability,” *Monthly Weather Review*, 1950.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `brier_score` | Mean squared probability error, from 0 (perfect) to 1 |
| `skill_score` | Brier skill score relative to the baseline; positive indicates improvement |
| `baseline_rate`, `reference_score` | Rate announced by the reference and its Brier score |
| `calibration[]` | Per non-empty bin: `lower`, `upper`, `mean_forecast`, `observed_frequency`, and `count` |

## Warnings and errors

- `422` (`InvalidInput`): probabilities outside [0, 1], outcomes other than 0 or 1, an empty list, an invalid
  baseline, or a bin count outside 1 to 100.
- `409` (`ComputationImpossible`, `UNDEFINED_SKILL_SCORE`): the reference Brier score is 0 (every outcome equals the
  baseline rate), so the skill score is undefined and no value is fabricated.
- `413` (`InputTooLarge`): more than 50,000 forecasts.

## Limitations

The Brier score combines calibration, resolution, and uncertainty; it is not a pure calibration metric. Meaningful
comparison requires forecasts and outcomes for the same event definition and evaluation horizon. Small samples can
make bin-level calibration noisy.
