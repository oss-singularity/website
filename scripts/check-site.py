#!/usr/bin/env python3
"""Validate the static production tree without third-party dependencies."""

from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime, timedelta, timezone
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
    if not path:
        return document
    if path == "/":
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
        ".htaccess", ".well-known/security.txt", "404.html",
        "dist-manifest.sha256", "index.html",
        "robots.txt", "sitemap.xml", "site.webmanifest",
        "assets/brand/oss-singularity-mark.svg",
        "assets/projects/chatgpt-usage-v030.webp",
        "assets/projects/nemo-action-bar.webp",
        "assets/projects/pdrive-control-center-v080.webp",
        "assets/scripts/reactive-field-v2.js",
        "assets/social/oss-singularity-social-preview.png",
        "assets/styles/site-v2.css",
        "assets/styles/hub-v1.css", "assets/scripts/atlas-v1.js",
        "assets/styles/home-v1.css", "assets/styles/activity-v1.css", "assets/scripts/commons-activity-v1.js",
        "assets/scripts/mission-lab-v1.js", "llms.txt",
        ".well-known/agent-home.json", "data/agent-home.schema.json",
        "data/atlas.json", "data/missions.json",
        "data/commons-openapi.json",
        "data/founding-mission.json", "mission/index.html",
        "data/help-wanted.json", "help/index.html", "roadmap/index.html",
        "data/help-wanted.schema.json",
        "observatory/index.html", "atlas/index.html", "lab/index.html",
        "guide/index.html", "connect/index.html",
        "workshop/index.html", "assets/styles/workshop-v1.css",
        "assets/scripts/workshop-v1.js", "assets/scripts/commons-pulse-v1.js",
        "assets/scripts/workshop-identity-v1.js",
        "singularity/index.html", "assets/styles/singularity-v1.css",
        "assets/scripts/singularity-v1.js", "assets/scripts/singularity-participation-v1.js",
    }
    social_path = "assets/social/oss-singularity-social-preview.png"
    social_bytes = (root / social_path).read_bytes()
    social_version = hashlib.sha256(social_bytes).hexdigest()[:12]
    social_versioned_path = f"assets/social/oss-singularity-social-preview.{social_version}.png"
    required.add(social_versioned_path)
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != required:
        fail(f"build allowlist mismatch: missing={sorted(required - actual)} extra={sorted(actual - required)}")

    if (root / social_versioned_path).read_bytes() != social_bytes:
        fail("fingerprinted social preview differs from the stable image alias")
    social_image = f"https://oss-singularity.io/{social_versioned_path}"
    html_bytes = 0
    script_allowlist = {
        "index.html": ["/assets/scripts/reactive-field-v2.js", "/assets/scripts/commons-pulse-v1.js", "/assets/scripts/commons-activity-v1.js"],
        "atlas/index.html": ["/assets/scripts/atlas-v1.js"],
        "lab/index.html": ["/assets/scripts/mission-lab-v1.js"],
        "observatory/index.html": ["/assets/scripts/commons-pulse-v1.js", "/assets/scripts/commons-activity-v1.js"],
        "workshop/index.html": ["/assets/scripts/workshop-v1.js", "/assets/scripts/workshop-identity-v1.js"],
        "singularity/index.html": ["/assets/scripts/singularity-v1.js", "/assets/scripts/singularity-participation-v1.js"],
    }
    documents = sorted(root.rglob("*.html"))
    for document in documents:
        relative = str(document.relative_to(root))
        parser = Document()
        text = document.read_text(encoding="utf-8")
        parser.feed(text)
        html_bytes += len(text.encode("utf-8"))
        # Only the Atlas embeds the complete catalog for no-JavaScript reading.
        html_budget = 45_000 if relative == "atlas/index.html" else 35_000
        if len(text.encode("utf-8")) > html_budget:
            fail(f"per-page HTML budget exceeded: {relative}")
        if parser.html_lang != "en":
            fail(f"{document.name} must declare lang=en")
        if not parser.title.strip():
            fail(f"{document.name} has no title")
        if parser.h1_count != 1:
            fail(f"{document.name} must contain exactly one h1")
        scripts = [attrs.get("src") for tag, attrs in parser.attrs if tag == "script"]
        if scripts != script_allowlist.get(relative, []):
            fail(f"unexpected scripts in {relative}: {scripts}")
        if relative != "404.html":
            suffix = "/" if relative == "index.html" else "/" + relative.removesuffix("index.html")
            canonical = "https://oss-singularity.io" + suffix
            if not any(tag == "link" and attrs.get("rel") == "canonical" and attrs.get("href") == canonical for tag, attrs in parser.attrs):
                fail(f"incorrect canonical in {relative}")
            for attribute, name in (("property", "og:image"), ("property", "og:image:secure_url"), ("name", "twitter:image")):
                images = [attrs.get("content") for tag, attrs in parser.attrs if tag == "meta" and attrs.get(attribute) == name]
                if images != [social_image]:
                    fail(f"{relative} must reference the current versioned {name} image")
            metadata = {attrs.get("property", attrs.get("name")): attrs.get("content") for tag, attrs in parser.attrs if tag == "meta"}
            for name, value in (("og:image:type", "image/png"), ("og:image:width", "1200"), ("og:image:height", "630"), ("twitter:card", "summary_large_image")):
                if metadata.get(name) != value:
                    fail(f"incorrect {name} in {relative}")
            for name in ("og:title", "og:description", "og:image:alt", "twitter:title", "twitter:description", "twitter:image:alt"):
                if not metadata.get(name, "").strip():
                    fail(f"missing {name} in {relative}")
        if len(parser.ids) != len(set(parser.ids)):
            fail(f"{document.name} contains duplicate ids")

        ids = set(parser.ids)
        for tag, attrs in parser.attrs:
            if any(key.startswith("on") for key in attrs) or "style" in attrs:
                fail(f"inline executable/style content in {relative}")
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
                live_api_routes = {"/api/v1", "/api/v1/missions", "/api/v1/contributions", "/api/v1/activity", "/api/v1/participations"}
                if target is not None and not target.exists() and urlsplit(value).path not in live_api_routes:
                    fail(f"broken local reference {value!r} in {document.name}")
                fragment = urlsplit(value).fragment
                if attribute == "href" and value.startswith("#") and fragment not in ids:
                    fail(f"broken fragment {value!r} in {document.name}")
                if fragment and target is not None and target.suffix == ".html" and not value.startswith("#"):
                    linked = Document()
                    linked.feed(target.read_text(encoding="utf-8"))
                    if fragment not in linked.ids:
                        fail(f"broken cross-page fragment {value!r} in {relative}")
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

    security_txt = (root / ".well-known/security.txt").read_text(encoding="utf-8")
    if not security_txt.endswith("\n") or "\r" in security_txt:
        fail("security.txt must be LF-terminated UTF-8 text")
    security_fields: dict[str, list[str]] = {}
    for line in security_txt.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition(":")
        if not separator or not value.strip():
            fail(f"malformed security.txt line: {line!r}")
        security_fields.setdefault(name, []).append(value.strip())
    expected_security_fields = {
        "Contact": ["mailto:mail@oss-singularity.io"],
        "Canonical": ["https://oss-singularity.io/.well-known/security.txt"],
        "Policy": ["https://github.com/oss-singularity/website/security/policy"],
        "Preferred-Languages": ["en, de"],
    }
    for name, expected in expected_security_fields.items():
        if security_fields.get(name) != expected:
            fail(f"security.txt {name} must be {expected[0]!r}")
    expires_values = security_fields.get("Expires", [])
    if len(expires_values) != 1:
        fail("security.txt must contain exactly one Expires field")
    try:
        expires = datetime.fromisoformat(expires_values[0].replace("Z", "+00:00"))
    except ValueError as error:
        fail(f"security.txt Expires is not RFC 3339: {error}")
    if expires.tzinfo is None:
        fail("security.txt Expires must include a timezone")
    now = datetime.now(timezone.utc)
    if expires <= now:
        fail("security.txt has expired")
    if expires - now > timedelta(days=366):
        fail("security.txt Expires must be less than one year ahead")

    css_bytes = sum(path.stat().st_size for path in (root / "assets/styles").glob("*.css"))
    for document in root.rglob("*.html"):
        parser = Document()
        parser.feed(document.read_text(encoding="utf-8"))
        linked_styles = {attrs.get("href") for tag, attrs in parser.attrs if tag == "link" and attrs.get("rel") == "stylesheet"}
        page_css_bytes = sum((root / href.lstrip("/")).stat().st_size for href in linked_styles)
        if page_css_bytes > 65_000:
            fail(f"per-page CSS budget exceeded: {document.relative_to(root)} {page_css_bytes} bytes")
    script_bytes = sum(path.stat().st_size for path in (root / "assets/scripts").glob("*.js"))
    for script in (root / "assets/scripts").glob("*.js"):
        if script.stat().st_size > 25_000:
            fail(f"per-page JavaScript budget exceeded: {script.name}")
    for image in (root / "assets/projects").iterdir():
        if image.stat().st_size > 180_000:
            fail(f"project image budget exceeded: {image.name} is {image.stat().st_size} bytes")
    transfer = 0
    for document in documents:
        parser = Document()
        parser.feed(document.read_text(encoding="utf-8"))
        assets = {document}
        for tag, attrs in parser.attrs:
            key = "src" if tag in {"img", "script"} else "href"
            if tag not in {"img", "script", "link"} or (tag == "link" and attrs.get("rel") not in {"stylesheet", "icon", "manifest"}):
                continue
            asset = local_target(root, document, attrs.get(key, ""))
            if asset is not None:
                assets.add(asset)
        size = sum(asset.stat().st_size for asset in assets)
        transfer = max(transfer, size)
        if size > 350_000:
            fail(f"initial transfer budget exceeded: {document.relative_to(root)} {size}")

    manifest_files = set()
    for line in (root / "dist-manifest.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        name = name.removeprefix("./")
        if name not in actual or name in manifest_files:
            fail(f"invalid manifest path {name}")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            fail(f"manifest digest mismatch {name}")
        manifest_files.add(name)
    if manifest_files != actual - {"dist-manifest.sha256"}:
        fail("manifest does not cover exact production tree")

    manifest = json.loads((root / "site.webmanifest").read_text(encoding="utf-8"))
    if manifest.get("start_url") != "/":
        fail("web manifest start_url must be canonical root")
    sitemap = ElementTree.parse(root / "sitemap.xml")
    locations = {node.text for node in sitemap.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
    expected_locations = {"https://oss-singularity.io/" + str(document.relative_to(root)).removesuffix("index.html") for document in documents if document.name != "404.html"}
    if locations != expected_locations:
        fail("sitemap must contain every canonical page exactly")
    ElementTree.parse(root / "assets/brand/oss-singularity-mark.svg")

    print(
        f"site checks passed: html={html_bytes} css={css_bytes} "
        f"js={script_bytes} transfer={transfer}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
