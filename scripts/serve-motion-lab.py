#!/usr/bin/env python3
"""Build and serve the local Observatory comparison on an owned loopback port."""

import argparse
from functools import partial
from html import escape
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urljoin


REPOSITORY = Path(__file__).resolve().parent.parent
LAB_FILES = (
    "index.html", "lab-shell.css", "lab-shell.js", "preview-bootstrap.js",
    "streams-lab.css", "streams-lab.js",
)
PRODUCT_FILES = (
    "assets/styles/site-v2.css", "assets/styles/hub-v1.css",
    "assets/styles/observatory-motion-v1.css",
    "assets/scripts/observatory-motion-v1.js",
    "assets/brand/oss-singularity-mark.svg",
)


class ObservatoryHero(HTMLParser):
    """Capture the fresh hero, retaining core motion but disabling its streams."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.depth = 0
        self.heroes = 0
        self.capabilities = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "section" and "observatory-hero" in values.get("class", "").split():
            self.heroes += 1
            if self.depth:
                raise ValueError("Nested Observatory hero is not supported.")
            self.depth = 1
        elif self.depth and tag == "section":
            self.depth += 1
        if not self.depth:
            return
        changed = False
        if "constellation" in values.get("class", "").split():
            self.capabilities += sum(name == "data-energy-streams" for name, _ in attrs)
            attrs = [(name, value) for name, value in attrs if name != "data-energy-streams"]
            changed = True
        if tag == "a":
            attrs = [(name, value) for name, value in attrs if name not in ("target", "rel")]
            attrs = [(name, urljoin("https://oss-singularity.io/", value) if name == "href" else value)
                     for name, value in attrs]
            attrs += [("target", "_blank"), ("rel", "noopener noreferrer")]
            changed = True
        if changed:
            rendered = "".join(" " + name + ("" if value is None else '="' + escape(value, quote=True) + '"')
                               for name, value in attrs)
            self.parts.append("<" + tag + rendered + ">")
        else:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            self.parts.append("</" + tag + ">")
            if tag == "section":
                self.depth -= 1

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text())

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data("&" + name + ";")

    def handle_charref(self, name):
        self.handle_data("&#" + name + ";")

    def handle_comment(self, data):
        if self.depth:
            self.parts.append("<!--" + data + "-->")

    def result(self):
        if self.heroes != 1 or self.depth or self.capabilities != 1:
            raise ValueError("Expected one complete Observatory hero with its stream capability.")
        return "".join(self.parts)


def build_preview(build_directory, public_directory):
    # A distinct process group lets interruption clean up only this owned build.
    process = subprocess.Popen(
        ["sh", str(REPOSITORY / "scripts/build-site.sh"), str(build_directory)],
        cwd=REPOSITORY, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        output, errors = process.communicate(timeout=120)
        if process.returncode:
            raise RuntimeError("Site build failed:\n" + output + errors)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    hero = ObservatoryHero()
    hero.feed((build_directory / "observatory/index.html").read_text(encoding="utf-8"))
    hero.close()
    for name in LAB_FILES:
        shutil.copyfile(REPOSITORY / "design/motion-lab" / name, public_directory / name)
    for name in PRODUCT_FILES:
        target = public_directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build_directory / name, target)
    preview = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Observatory motion preview</title>
  <link rel="stylesheet" href="/assets/styles/site-v2.css">
  <link rel="stylesheet" href="/assets/styles/hub-v1.css">
  <link rel="stylesheet" href="/assets/styles/observatory-motion-v1.css">
  <link rel="stylesheet" href="streams-lab.css">
  <script src="preview-bootstrap.js"></script>
  <script src="/assets/scripts/observatory-motion-v1.js" defer></script>
  <script src="streams-lab.js" defer></script>
</head>
<body class="hub-page hub-observatory">
  <div class="site-shell"><main class="hub-main">''' + hero.result() + '''</main></div>
</body>
</html>
'''
    (public_directory / "preview.html").write_text(preview, encoding="utf-8")


class LabHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, format, *args):
        pass


def stop(_signal, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4204, help="loopback port (default: 4204)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    signal.signal(signal.SIGTERM, stop)
    try:
        with TemporaryDirectory(prefix="oss-motion-lab-") as temporary:
            root = Path(temporary)
            public = root / "public"
            public.mkdir()
            handler = partial(LabHandler, directory=str(public))
            # Bind before building. Never stop or replace an existing server.
            with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                build_preview(root / "build", public)
                print(f"Motion Lab: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"Motion Lab could not start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
