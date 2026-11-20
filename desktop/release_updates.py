"""Find matching desktop downloads using the repository's public release metadata."""

import json
import os
from pathlib import Path
import re
from threading import Thread
from time import time
from urllib.request import Request, urlopen

from gi.repository import GLib

REPOSITORY = "Pocketpac/SpokenMediaPlayPause"
RELEASES = f"https://github.com/{REPOSITORY}/releases/tag/"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
APPIMAGE = "Spoken-Media-Pause-All-Players-Ubuntu-22.04-x86_64.AppImage"
TAG = re.compile(r"(?:addon-)?v(\d{1,9})\.(\d{1,9})\.(\d{1,9})", re.ASCII)
DAY = 24 * 60 * 60
MAX_RESPONSE = 1_000_000


def version(tag):
    """Parse the combined release version without using the Windows beta number."""
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(map(int, match.groups())) if match else None


def latest_tag():
    """Return a validated tag for a full release containing the Linux download."""
    request = Request(API, headers={"User-Agent": "Spoken-Media-Pause",
                                    "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=5) as response:
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("GitHub response too large")
    release = json.loads(data)
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or version(tag) is None:
        raise ValueError("Invalid full release tag")
    return tag if any(item["name"] == APPIMAGE and item["state"] == "uploaded"
                      for item in release["assets"]) else None


class UpdateChecker:
    """Run one check off the GUI thread and share a daily cache across launches."""

    def __init__(self, installed=None, cache=None, fetch=latest_tag, now=time):
        release = installed or Path(__file__).with_name("release-version.txt").read_text().strip()
        self.installed = version("v" + release)
        if self.installed is None:
            raise ValueError("Invalid installed release version")
        cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        self.cache = cache or cache_root / "spoken-media-pause/release-check.json"
        self.fetch, self.now = fetch, now
        self.busy = False

    def check(self, manual, done):
        """Deliver (error, newer tag) on the GTK loop. Manual checks bypass the daily interval."""
        if self.busy:
            return
        self.busy = True

        def deliver(error, tag):
            self.busy = False
            available = version(tag)
            done(error, tag if available and available > self.installed else None)
            return GLib.SOURCE_REMOVE

        def run():
            try:
                with self.cache.open() as stream:
                    saved = json.loads(stream.read(4096))
                checked, tag = float(saved["checked"]), saved.get("tag")
            except (OSError, TypeError, ValueError, KeyError, AttributeError):
                checked, tag = 0, None
            now = self.now()
            error = False
            if manual or not 0 <= now - checked < DAY:
                try:
                    tag = self.fetch()
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    error = True
                try:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    self.cache.write_text(json.dumps({"checked": now, "tag": tag}), encoding="utf-8")
                except OSError:
                    pass
            GLib.idle_add(deliver, error, tag)

        Thread(target=run, daemon=True, name="release-check").start()
