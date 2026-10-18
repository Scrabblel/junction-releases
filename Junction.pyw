"""Double-click to open Junction (runs without a console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from junction.app import main  # noqa: E402

main()
