"""Write the OpenAPI contract: ``python -m smerisk_service.openapi [path]`` (standard output by default)."""

from __future__ import annotations

import json
import sys
from typing import Any

from smerisk_service.main import create_app
from smerisk_service.settings import Settings


def schema() -> dict[str, Any]:
    return create_app(Settings()).openapi()


def main(argv: list[str]) -> None:
    text = json.dumps(schema(), indent=2, ensure_ascii=False) + "\n"
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as file:
            file.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main(sys.argv)
