"""Keeping Junction up to date.

New versions are published as GitHub Releases in a public repository that holds only the installers
(RELEASES). Junction asks GitHub for the newest release now and then; when it's newer than this copy,
the page offers it. Updating downloads that release's JunctionSetup-<version>.exe, checks it against the
SHA-256 GitHub publishes for it, and runs it: the installer closes Junction, updates it and opens it again.

Publishing a release: raise VERSION in config.py, run tools/build.py, then on GitHub create a release with
the tag v<VERSION> and attach dist/JunctionSetup-<VERSION>.exe. Its description is shown as "what's new".

Only Junction.exe updates itself. Running from source, the page still says when there's a new version.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from . import config

log = logging.getLogger("junction")
RELEASES = "scrabblel/junction-releases"
LATEST = f"https://api.github.com/repos/{RELEASES}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{RELEASES}/releases/download/".lower()
CHECK_EVERY = 6 * 3600   # seconds between checks while Junction stays open
FIRST_CHECK = 20         # seconds after starting, so it never slows the start down
UA = {"User-Agent": f"Junction/{config.VERSION} (update check)", "Accept": "application/vnd.github+json"}


def parse_version(text):
    """'v0.9.1' -> (0, 9, 1, 0), so versions of any length compare properly; anything else -> None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (text or "").strip())
    return tuple(int(n) for n in m.group(1).split(".")) + (0,) * (3 - m.group(1).count(".")) if m else None


def latest(timeout=15):
    """The newest published release, or None if there's none (or GitHub can't be reached)."""
    with urllib.request.urlopen(urllib.request.Request(LATEST, headers=UA), timeout=timeout) as r:
        rel = json.load(r)
    version = parse_version(rel.get("tag_name"))
    if not version or rel.get("draft") or rel.get("prerelease"):
        return None
    asset = next((a for a in rel.get("assets") or []
                  if re.fullmatch(r"JunctionSetup-[\d.]+\.exe", a.get("name") or "")
                  and (a.get("browser_download_url") or "").lower().startswith(DOWNLOAD_PREFIX)), None)
    digest = (asset or {}).get("digest") or ""
    return {
        "version": rel["tag_name"].strip().lstrip("vV"),
        "tag": rel.get("tag_name"),
        "title": rel.get("name") or rel.get("tag_name"),
        "notes": (rel.get("body") or "").strip()[:6000],
        "published": rel.get("published_at"),
        "page": rel.get("html_url"),
        "file": asset and asset["name"],
        "url": asset and asset["browser_download_url"],
        "size": asset and asset.get("size"),
        "sha256": digest[7:].lower() if digest.lower().startswith("sha256:") else None,
    }


class Updater:
    """Checks in the background and installs on request. `notify(info)` tells the page."""

    def __init__(self, notify):
        self.notify = notify
        self.info = {"current": config.VERSION, "canInstall": bool(getattr(sys, "frozen", False)),
                     "checked": None, "available": None, "error": None}
        self._lock = threading.Lock()
        self._downloading = False

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(FIRST_CHECK)
        while True:
            self.check()
            time.sleep(CHECK_EVERY)

    def check(self):
        """Ask GitHub now. Returns what the page shows (also when nothing changed)."""
        with self._lock:
            try:
                rel = latest()
                newer = rel and rel["url"] and parse_version(rel["version"]) > parse_version(config.VERSION)
                self.info.update(available=rel if newer else None, error=None, checked=int(time.time()))
                if newer:
                    log.info("Update available: %s", rel["version"])
            except Exception as e:  # noqa: BLE001 - offline, GitHub down, rate limit: try again later
                self.info.update(error="Junction couldn't reach GitHub to check for updates.", checked=int(time.time()))
                log.warning("Update check failed: %s", e)
        self.notify(self.info)
        return self.info

    def install(self, progress, quit_app):
        """Download the new installer, check it, run it, and let Junction close so it can be replaced."""
        rel = self.info.get("available")
        if not rel:
            return {"error": "There's no update to install."}
        if not self.info["canInstall"]:
            return {"error": "This copy runs from source; download the installer from GitHub instead.", "page": rel["page"]}
        if self._downloading:
            return {"error": "The update is already downloading."}
        self._downloading = True
        try:
            path = self._download(rel, progress)
            progress({"stage": "installing"})
            # /SILENT shows the installer's progress but asks nothing; /relaunch=1 opens Junction again after.
            subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"], close_fds=True)
            log.info("Installing update %s", rel["version"])
            threading.Timer(1.5, quit_app).start()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            log.exception("Update failed")
            return {"error": str(e) if isinstance(e, UpdateError) else "Junction couldn't download the update. Try again later."}
        finally:
            self._downloading = False

    def _download(self, rel, progress):
        if not (rel.get("url") or "").lower().startswith(DOWNLOAD_PREFIX):
            raise UpdateError("That update doesn't come from Junction's own release page, so Junction won't run it.")
        folder = os.path.join(tempfile.gettempdir(), "Junction update")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, rel["file"])
        sha, done, total = hashlib.sha256(), 0, rel.get("size") or 0
        req = urllib.request.Request(rel["url"], headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
            total = int(r.headers.get("Content-Length") or total)
            last = 0
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if time.time() - last > 0.2:
                    progress({"stage": "downloading", "done": done, "total": total})
                    last = time.time()
        progress({"stage": "downloading", "done": done, "total": total})
        if rel.get("size") and done != rel["size"]:
            os.remove(path + ".part")
            raise UpdateError("The download was incomplete. Try again.")
        if rel.get("sha256") and sha.hexdigest() != rel["sha256"]:
            os.remove(path + ".part")
            raise UpdateError("The download doesn't match the published installer, so Junction didn't run it.")
        os.replace(path + ".part", path)
        return path


class UpdateError(Exception):
    pass
