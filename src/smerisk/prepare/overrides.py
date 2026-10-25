"""Apply and trace overrides in derived_parameters (§11.5)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import TypeAdapter, ValidationError

from smerisk.core.errors import FieldError, InvalidInput
from smerisk.prepare._base import Estimate

OVERRIDES_PATH = ("settings", "overrides")


def _swapped(path: str) -> str | None:
    """``"correlation.a~b"`` → ``"correlation.b~a"``: a pair can be named in either order."""
    head, _, last = path.rpartition(".")
    a, sep, b = last.partition("~")
    if not sep:
        return None
    return f"{head}.{b}~{a}" if head else f"{b}~{a}"


class DerivedParameters:
    """The parameters passed to the formulas, by path, with their source.

    A use case adds each estimate or default, then applies the caller's ``overrides``, then reads the values back to
    build the formula input. ``to_dict()`` is the ``derived_parameters`` of the response.
    """

    def __init__(self) -> None:
        self._items: dict[str, Estimate[Any]] = {}
        self._schemas: dict[str, TypeAdapter[Any]] = {}

    def add(self, path: str, estimate: Estimate[Any], schema: Any = None) -> None:
        """``schema``: the type an override must have (for example ``Distribution``); by default the value's type."""
        self._items[path] = estimate
        self._schemas[path] = TypeAdapter(schema if schema is not None else type(estimate.value))

    def default(self, path: str, value: Any, reason: str = "default of the use case", schema: Any = None) -> None:
        self.add(path, Estimate(value=value, source="default", method=reason), schema)

    def apply(self, overrides: Mapping[str, Any]) -> None:
        """Replace the values named in ``overrides``; unknown paths and invalid values raise 422 and change nothing."""
        errors: list[FieldError] = []
        accepted: dict[str, Any] = {}
        for path, value in overrides.items():
            target = path if path in self._items else _swapped(path)
            if target is None or target not in self._items:
                errors.append(FieldError((*OVERRIDES_PATH, path), "unknown parameter"))
                continue
            try:
                checked = self._schemas[target].validate_python(value)
            except ValidationError as exc:
                errors.append(FieldError((*OVERRIDES_PATH, path), exc.errors()[0]["msg"]))
                continue
            accepted[target] = checked
        if errors:
            raise InvalidInput(errors)
        for target, checked in accepted.items():
            self._items[target] = Estimate(value=checked, source="override", method="set by the caller")

    def __getitem__(self, path: str) -> Estimate[Any]:
        return self._items[path]

    def __contains__(self, path: object) -> bool:
        return path in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def value(self, path: str) -> Any:
        return self._items[path].value

    def to_dict(self) -> dict[str, Estimate[Any]]:
        return dict(self._items)
