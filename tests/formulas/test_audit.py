from typing import Any

import numpy as np
import pytest
from scipy import stats

from smerisk import ComputationImpossible, InvalidInput, benford
from smerisk.core.warnings import WarningCode

RNG = np.random.default_rng(0)
BENFORD_LIKE = list(10 ** RNG.uniform(1, 6, 5_000))  # log-uniform amounts follow Benford's law
UNIFORM = list(RNG.uniform(10, 1_000, 5_000))


def test_benford_sample_conforms() -> None:
    out = benford(amounts=BENFORD_LIKE)
    assert out.result.conformity == "CLOSE"
    assert out.result.suspicious_digits == []
    assert out.warnings == []


def test_uniform_amounts_do_not_conform() -> None:
    r = benford(amounts=UNIFORM).result
    assert r.conformity == "NONCONFORMITY"
    assert 9 in r.suspicious_digits and 1 not in r.suspicious_digits


def test_expected_proportions_and_counts_by_hand() -> None:
    amounts = [100, 150, 199.99, 200, 900, 5, -310]
    r = benford(amounts=amounts).result
    assert (r.n_used, r.n_excluded) == (6, 1)  # 5 is below min_amount; -310 counts as 310
    observed = {d.digit: d.observed for d in r.digits}
    assert observed[1] == pytest.approx(3 / 6) and observed[2] == pytest.approx(1 / 6)
    assert [d.expected for d in r.digits] == pytest.approx([np.log10(1 + 1 / d) for d in range(1, 10)])
    assert r.mad == pytest.approx(np.mean([abs(d.observed - d.expected) for d in r.digits]))


def test_chi2_matches_scipy() -> None:
    r = benford(amounts=UNIFORM).result
    counts = np.array([d.observed for d in r.digits]) * r.n_used
    reference = stats.chisquare(counts, np.array([d.expected for d in r.digits]) * r.n_used)
    assert r.chi2 == pytest.approx(reference.statistic)
    assert r.p_value == pytest.approx(reference.pvalue)


def test_nigrini_z_with_continuity_correction() -> None:
    r = benford(amounts=UNIFORM).result
    d1 = r.digits[0]
    n = r.n_used
    expected_z = (abs(d1.observed - d1.expected) - 1 / (2 * n)) / np.sqrt(d1.expected * (1 - d1.expected) / n)
    assert d1.z == pytest.approx(expected_z)


def test_first_two_digits() -> None:
    r = benford(amounts=[10.5, 11, 99.9, 1_050, 12_345], test="first_two_digits", min_amount=10).result
    assert [d.digit for d in r.digits] == list(range(10, 100))
    observed = {d.digit: d.observed for d in r.digits}
    assert observed[10] == pytest.approx(2 / 5) and observed[99] == pytest.approx(1 / 5)
    assert benford(amounts=BENFORD_LIKE, test="first_two_digits").result.conformity in ("CLOSE", "ACCEPTABLE")


def test_digits_are_read_from_cents_not_float_noise() -> None:
    r = benford(amounts=[299.99999999999997, 0.3 * 1000], min_amount=1).result
    observed = {d.digit: d.observed for d in r.digits}
    assert observed[3] == 1.0  # 299.99999999999997 rounds to 300.00


def test_flagged_labels_follow_suspicious_digits() -> None:
    amounts = UNIFORM[:1_000]
    labels = [f"entry-{i}" for i in range(1_000)]
    r = benford(amounts=amounts, labels=labels).result
    assert r.flagged_labels is not None and r.suspicious_digits
    first = {f"entry-{i}": int(str(int(round(a * 100)))[0]) for i, a in enumerate(amounts)}
    assert {first[label] for label in r.flagged_labels} <= set(r.suspicious_digits)


def test_small_samples() -> None:
    small = benford(amounts=BENFORD_LIKE[:150])
    assert [w.code for w in small.warnings] == [WarningCode.SMALL_SAMPLE] and small.result.conformity is not None
    tiny = benford(amounts=BENFORD_LIKE[:50])
    assert tiny.result.conformity is None


def test_no_eligible_amount() -> None:
    with pytest.raises(ComputationImpossible):
        benford(amounts=[1, 2, 3])


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"amounts": [100], "labels": ["a", "b"]}, ()),
        ({"amounts": []}, ("amounts",)),
        ({"amounts": [100], "test": "last_digit"}, ("test",)),
        ({"amounts": [100], "min_amount": -1}, ("min_amount",)),
    ],
)
def test_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        benford(fields)
    assert path in [e.path for e in exc.value.errors]
