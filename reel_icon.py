"""The app icon, drawn with QPainter: a film reel with a strip of film running
off it. Used for the window, the right-click entry, and make_icon.py."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient


def reel_pixmap(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    u = size / 256

    # film strip curling off the bottom right
    strip = QPainterPath()
    strip.moveTo(150 * u, 214 * u)
    strip.cubicTo(196 * u, 226 * u, 226 * u, 206 * u, 246 * u, 168 * u)
    qp.setPen(QPen(QColor("#bf616a"), 34 * u, Qt.SolidLine, Qt.FlatCap))
    qp.setBrush(Qt.NoBrush)
    qp.drawPath(strip)
    # sprocket holes along the strip
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor("#2e3440"))
    for i in range(1, 6):
        t = i / 6
        p = strip.pointAtPercent(t)
        a = math.radians(strip.angleAtPercent(t))
        for side in (-1, 1):
            off = QPointF(math.sin(a) * 10 * u * side, math.cos(a) * 10 * u * side)
            qp.drawRect(QRectF(p.x() + off.x() - 3 * u, p.y() + off.y() - 3 * u, 6 * u, 6 * u))

    # the reel
    c = QPointF(116 * u, 116 * u)
    r = 106 * u
    g = QRadialGradient(c, r)
    g.setColorAt(0.0, QColor("#88a6cc"))
    g.setColorAt(1.0, QColor("#4c6f9c"))
    qp.setBrush(g)
    qp.setPen(QPen(QColor("#2e3440"), 6 * u))
    qp.drawEllipse(c, r, r)
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor("#2e3440"))
    for i in range(5):
        a = math.radians(-90 + i * 72)
        hc = QPointF(c.x() + math.cos(a) * 58 * u, c.y() + math.sin(a) * 58 * u)
        qp.drawEllipse(hc, 26 * u, 26 * u)
    qp.setBrush(QColor("#e5e9f0"))
    qp.drawEllipse(c, 16 * u, 16 * u)
    qp.setBrush(QColor("#2e3440"))
    qp.drawEllipse(c, 6 * u, 6 * u)
    qp.end()
    return pm


def reel_icon():
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(reel_pixmap(s))
    return icon
