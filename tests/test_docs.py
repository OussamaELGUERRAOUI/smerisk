"""Python examples in the README and in implemented documentation pages must run and print what their comments say."""

import contextlib
import io
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = [ROOT / "README.md"] + [
    p
    for p in sorted([*(ROOT / "docs").glob("*.md"), *(ROOT / "docs").glob("*/*.md")])
    if "**Status:** implemented" in p.read_text("utf-8")
]
BLOCK = re.compile(r"```python\n(.*?)```", re.S)
EXPECTED = re.compile(r"^print\(.*\)\s+#\s*(.+)$", re.S)


def _print_statements(code: str) -> list[str]:
    """Top-level ``print(...)`` statements, each joined on one line when the formatter split it."""
    statements, current = [], ""
    for line in code.splitlines():
        if current or line.startswith("print("):
            current += line.strip() if current else line
            if current.split("  #")[0].count("(") <= current.split("  #")[0].count(")"):
                statements.append(current)
                current = ""
    return statements


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_examples_print_documented_values(page: Path) -> None:
    blocks = BLOCK.findall(page.read_text("utf-8"))
    assert blocks, f"{page.name} has no Python example"
    for code in blocks:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(code, str(page), "exec"), {})
        printed = iter(out.getvalue().splitlines())
        for line in _print_statements(code):
            value = next(printed)
            match = EXPECTED.match(line)
            if match:
                # The comment may add an explanation after the value, e.g. "# ['SMALL_SAMPLE']: fewer than 5 tasks".
                assert match.group(1).startswith(value), f"{page.name}: {line!r} printed {value!r}"
