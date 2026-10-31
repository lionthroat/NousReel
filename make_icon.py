"""Draws the app icon (reel_icon.py) to icon.ico for Windows and a 1024px
icon.png for Linux, the Mac and the website. Commit both files."""
import os
import sys

from PySide6.QtGui import QGuiApplication

app = QGuiApplication(sys.argv)
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from reel_icon import reel_pixmap  # noqa: E402

for name, size in (("icon.ico", 256), ("icon.png", 1024)):
    out = os.path.join(here, name)
    print("saved" if reel_pixmap(size).toImage().save(out) else "FAILED", out)
