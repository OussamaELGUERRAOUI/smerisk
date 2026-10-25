import smerisk


def test_version_is_exposed() -> None:
    assert isinstance(smerisk.__version__, str)
