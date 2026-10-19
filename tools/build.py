"""Builds Junction for other people. From the project folder:

    python tools/build.py                       # checks, Junction.exe, then the installer
    python tools/build.py --exe-only            # just dist/Junction/Junction.exe
    python tools/build.py --skip-store-check    # without building a new person's Store first (offline, say)

Results: dist/Junction/Junction.exe and dist/JunctionSetup-<version>.exe. The version comes from
junction/config.py (VERSION); raise it there before building a release. The installer needs Inno Setup 6.
"""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version():
    with open(os.path.join(ROOT, "junction", "config.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)


def iscc():
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        path = os.path.join(base or "", "Inno Setup 6", "ISCC.exe")
        if os.path.isfile(path):
            return path
    return None


def write_builtin_key():
    """Put the IsThereAnyDeal app key into junction/_builtin.py, which git ignores.

    It comes from JUNCTION_ITAD_KEY, or else "itadApiKey" in local_settings.json. The key itself is never printed.
    """
    key = os.environ.get("JUNCTION_ITAD_KEY", "").strip()
    if not key:
        try:
            with open(os.path.join(ROOT, "local_settings.json"), encoding="utf-8") as f:
                key = (json.load(f).get("itadApiKey") or "").strip()
        except (OSError, ValueError):
            key = ""
    path = os.path.join(ROOT, "junction", "_builtin.py")
    if not key:
        if os.path.exists(path):
            os.remove(path)
        print("No IsThereAnyDeal key found: this build has no built-in prices key.", flush=True)
        return
    sys.path.insert(0, ROOT)
    from junction.keys import mask
    with open(path, "w", encoding="utf-8") as f:
        f.write('"""Made by tools/build.py; not in git. The IsThereAnyDeal app key Junction comes with (masked)."""\n'
                f'ITAD = "{mask(key)}"\n')
    print(f"Built-in prices key: included (ends in {key[-4:]})", flush=True)


def check_code():
    """Stop the build on mistakes Python only notices when that code runs (an unknown or shadowed name, say).
    Uses pyflakes (pip install pyflakes); unused imports and the like don't stop it."""
    try:
        out = subprocess.run([sys.executable, "-m", "pyflakes", "junction", "Junction.pyw"],
                             cwd=ROOT, capture_output=True, text=True).stdout
    except OSError:
        out = None
    if out is None or "No module named pyflakes" in out:
        print("pyflakes isn't installed, so the code wasn't checked (pip install pyflakes).", flush=True)
        return
    harmless = ("imported but unused", "is assigned to but never used", "redefinition of unused", "f-string is missing placeholders")
    problems = [line for line in out.splitlines() if line.strip() and not any(h in line for h in harmless)]
    if problems:
        sys.exit("The code check found mistakes; nothing was built:\n  " + "\n  ".join(problems))
    print("Code check: no mistakes found", flush=True)


def check_store():
    """Build a new person's Store through the app (tools/check_store.py, about half a minute), and stop the
    build if it wouldn't load. It runs in its own Python, so it can't touch your own settings."""
    if "--skip-store-check" in sys.argv:
        print("Store check: skipped", flush=True)
        return
    if subprocess.run([sys.executable, os.path.join(ROOT, "tools", "check_store.py")], cwd=ROOT).returncode:
        sys.exit("Nothing was built. (Offline? python tools/build.py --skip-store-check builds anyway.)")


def main():
    v = version()
    print(f"Building Junction {v}", flush=True)
    check_code()
    write_builtin_key()
    check_store()
    subprocess.run([sys.executable, "-m", "PyInstaller", "Junction.spec", "--noconfirm"], cwd=ROOT, check=True)
    if "--exe-only" in sys.argv:
        return
    compiler = iscc()
    if not compiler:
        sys.exit("Inno Setup 6 isn't installed, so only Junction.exe was built (https://jrsoftware.org/isdl.php).")
    subprocess.run([compiler, f"/DAppVersion={v}", "Junction.iss"], cwd=ROOT, check=True)
    print(f"\nInstaller: {os.path.join(ROOT, 'dist', f'JunctionSetup-{v}.exe')}")


if __name__ == "__main__":
    main()
