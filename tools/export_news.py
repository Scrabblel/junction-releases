"""Add each Steam game's latest developer announcements (patch notes, news) to web/library.json.

    python tools/export_news.py        (also run at the end of tools/export_artifact.py)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from junction import library  # noqa: E402

WEB = os.path.join(ROOT, "web")


def main():
    path = os.path.join(WEB, "library.json")
    with open(path, encoding="utf-8") as f:
        lib = json.load(f)
    library.add_news(lib)
    print(f'News for {len(lib["news"])} Steam games')
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
