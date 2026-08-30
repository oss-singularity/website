#!/usr/bin/env python3
"""Validate the static production tree without third-party dependencies."""

from __future__ import annotations

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: list[tuple[str, dict[str, str]]] = []
        self.ids: list[str] = []
        self.h1_count = 0
        self.html_lang = ""
        self.title_depth = 0
        self.title = ""
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        self.attrs.append((tag, values))
        if tag == "html":
            self.html_lang = values.get("lang", "")
        if tag == "h1":
            self.h1_count += 1
        if tag == "title":
            self.title_depth += 1
        if tag == "script":
            self.scripts += 1
        if values.get("id"):
            self.ids.append(values["id"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title += data


def fail(message: str) -> None:
    raise SystemExit(f"site check failed: {message}")


def local_target(root: Path, document: Path, value: str) -> Path | None:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "tel:")):
        return None
    path = parsed.path
    if not path or path == "/":
        return root / "index.html"
    if path.startswith("/"):
        target = root / path.removeprefix("/")
    else:
        target = document.parent / path
    if target.is_dir():
        target /= "index.html"
    return target


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    if not root.is_dir():
        fail(f"missing build directory {root}")

    required = {
        ".htaccess", "404.html", "dist-manifest.sha256", "index.html",
        "robots.txt", "sitemap.xml", "site.webmanifest",
        "assets/brand/oss-singularity-mark.svg",
        "assets/projects/chatgpt-usage-v030.webp",
        "assets/projects/nemo-action-bar.webp",
        "assets/projects/pdrive-control-center-v080.webp",
        "assets/scripts/reactive-field.js",
        "assets/social/oss-singularity-social-preview.png",
        "assets/styles/site-v1.css",
    }
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != required:
        fail(f"build allowlist mismatch: missing={sorted(required - actual)} extra={sorted(actual - required)}")

    html_bytes = 0
    for document in (root / "index.html", root / "404.html"):
        parser = Document()
        text = document.read_text(encoding="utf-8")
        parser.feed(text)
        html_bytes += len(text.encode("utf-8"))
        if parser.html_lang != "en":
            fail(f"{document.name} must declare lang=en")
        if not parser.title.strip():
            fail(f"{document.name} has no title")
        if parser.h1_count != 1:
            fail(f"{document.name} must contain exactly one h1")
        expected_scripts = 1 if document.name == "index.html" else 0
        if parser.scripts != expected_scripts:
            fail(f"{document.name} must contain {expected_scripts} script elements")
        if len(parser.ids) != len(set(parser.ids)):
            fail(f"{document.name} contains duplicate ids")

        ids = set(parser.ids)
        for tag, attrs in parser.attrs:
            if tag == "img":
                if "alt" not in attrs:
                    fail(f"image without alt in {document.name}")
                if not attrs.get("width") or not attrs.get("height"):
                    fail(f"image without explicit dimensions in {document.name}")
            for attribute in ("href", "src"):
                value = attrs.get(attribute, "")
                if not value:
                    continue
                target = local_target(root, document, value)
                if target is not None and not target.exists():
                    fail(f"broken local reference {value!r} in {document.name}")
                fragment = urlsplit(value).fragment
                if attribute == "href" and value.startswith("#") and fragment not in ids:
                    fail(f"broken fragment {value!r} in {document.name}")
            if tag in {"img", "script"}:
                source = attrs.get("src", "")
                if source.startswith(("http://", "https://", "//")):
                    fail(f"third-party runtime request in {document.name}: {source}")
            if tag == "link" and attrs.get("rel") in {"stylesheet", "icon", "manifest"}:
                href = attrs.get("href", "")
                if href.startswith(("http://", "https://", "//")):
                    fail(f"third-party linked asset in {document.name}: {href}")

    index = (root / "index.html").read_text(encoding="utf-8")
    for marker in (
        '<link rel="canonical" href="https://oss-singularity.io/">',
        'property="og:image"',
        'name="twitter:card"',
        'name="description"',
    ):
        if marker not in index:
            fail(f"missing index metadata: {marker}")
    if 'name="robots" content="noindex"' not in (root / "404.html").read_text(encoding="utf-8"):
        fail("404 page must be noindex")

    if html_bytes > 35_000:
        fail(f"HTML budget exceeded: {html_bytes} bytes")
    css_bytes = (root / "assets/styles/site-v1.css").stat().st_size
    if css_bytes > 40_000:
        fail(f"CSS budget exceeded: {css_bytes} bytes")
    script_bytes = (root / "assets/scripts/reactive-field.js").stat().st_size
    if script_bytes > 8_000:
        fail(f"JavaScript budget exceeded: {script_bytes} bytes")
    for image in (root / "assets/projects").iterdir():
        if image.stat().st_size > 180_000:
            fail(f"project image budget exceeded: {image.name} is {image.stat().st_size} bytes")
    transfer = sum(
        path.stat().st_size for path in root.rglob("*")
        if path.is_file() and "assets/social" not in str(path.relative_to(root))
    )
    if transfer > 350_000:
        fail(f"initial transfer budget exceeded: {transfer} bytes")

    manifest = json.loads((root / "site.webmanifest").read_text(encoding="utf-8"))
    if manifest.get("start_url") != "/":
        fail("web manifest start_url must be canonical root")
    ElementTree.parse(root / "sitemap.xml")
    ElementTree.parse(root / "assets/brand/oss-singularity-mark.svg")

    print(
        f"site checks passed: html={html_bytes} css={css_bytes} "
        f"js={script_bytes} transfer={transfer}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
