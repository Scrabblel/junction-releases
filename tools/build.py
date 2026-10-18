"""Builds Junction for other people. From the project folder:

    python tools/build.py              # Junction.exe, then the installer
    python tools/build.py --exe-only   # just dist/Junction/Junction.exe

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


def main():
    v = version()
    print(f"Building Junction {v}", flush=True)
    write_builtin_key()
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
