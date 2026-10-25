"""Every URL in docs/llms.txt must point to a page of the documentation site."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://oussamaelguerraoui.github.io/smerisk/"
LINK = re.compile(r"\]\((" + re.escape(SITE) + r"[^)]*)\)")


def test_llms_txt_urls_match_pages() -> None:
    urls = LINK.findall((ROOT / "docs" / "llms.txt").read_text("utf-8"))
    assert urls
    for url in urls:
        path = url.removeprefix(SITE).rstrip("/")
        page = ROOT / "docs" / (f"{path}.md" if path else "index.md")
        assert page.exists(), f"{url} has no page {page.name}"
