import numpy as np

from smerisk.core.random import make_rng, make_streams, resolve_seed


def test_same_seed_same_draws() -> None:
    assert np.array_equal(make_rng(3).random(5), make_rng(3).random(5))


def test_drawn_seed_is_a_32_bit_integer() -> None:
    seed = resolve_seed(None)
    assert 0 <= seed < 2**32
    assert resolve_seed(7) == 7


def test_streams_do_not_depend_on_other_group_sizes() -> None:
    (a,), (b,) = make_streams(1, 1, 1)
    [a2, extra], [b2, *_] = make_streams(1, 2, 5)
    assert np.array_equal(a.random(10), a2.random(10))
    assert np.array_equal(b.random(10), b2.random(10))
    assert not np.array_equal(make_streams(1, 2)[0][0].random(10), extra.random(10))
