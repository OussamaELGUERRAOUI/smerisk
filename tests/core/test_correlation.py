import numpy as np
import pytest
from pydantic import ValidationError
from scipy import stats

from smerisk.core.correlation import Correlation, prepare_correlation
from smerisk.core.errors import ComputationImpossible


def test_spearman_is_reproduced_by_the_copula() -> None:
    target = np.array([[1.0, 0.6, -0.3], [0.6, 1.0, 0.2], [-0.3, 0.2, 1.0]])
    prepared = prepare_correlation(target)
    assert prepared.adjustment == 0
    z = prepared.cholesky @ np.random.default_rng(0).standard_normal((3, 200_000))
    rho = stats.spearmanr(z.T).statistic
    np.testing.assert_allclose(rho, target, atol=0.01)


def test_slightly_non_positive_definite_is_adjusted() -> None:
    m = np.array([[1.0, 0.8, 0.8], [0.8, 1.0, 0.25], [0.8, 0.25, 1.0]])
    assert np.linalg.eigvalsh(m)[0] < 0
    prepared = prepare_correlation(m)
    assert 0 < prepared.adjustment < 0.05
    assert np.abs(prepared.spearman - m).max() == pytest.approx(prepared.adjustment)
    assert np.all(np.linalg.eigvalsh(prepared.spearman) > 0)


def test_far_from_positive_definite_is_impossible() -> None:
    m = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    with pytest.raises(ComputationImpossible) as exc:
        prepare_correlation(m)
    assert exc.value.code == "CORRELATION_NOT_POSITIVE_DEFINITE"
    assert exc.value.details["min_eigenvalue"] == pytest.approx(np.linalg.eigvalsh(m)[0])


@pytest.mark.parametrize(
    "matrix",
    [
        [[1, 0.5], [0.4, 1]],
        [[0.9, 0.5], [0.5, 1]],
        [[1, 1.2], [1.2, 1]],
        [[1, 0.5, 0], [0.5, 1, 0]],
    ],
)
def test_invalid_matrix(matrix: list[list[float]]) -> None:
    with pytest.raises(ValidationError):
        Correlation(variables=["a", "b"], matrix=matrix)


def test_duplicate_variables() -> None:
    with pytest.raises(ValidationError):
        Correlation(variables=["a", "a"], matrix=[[1, 0], [0, 1]])
