"""Colours, stylesheet, icon glyphs and the logo."""

import winreg

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap

ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9dff"
ACCENT_PRESS = "#3b78ea"
GREEN = "#34c759"
ORANGE = "#ff9f0a"
RED = "#ff453a"

DARK = dict(bg="#16181d", side="#1b1e24", card="#22262e", card2="#2a2f38", border="#30353f",
            text="#eef1f6", muted="#9aa3b2", faint="#6b7280", input="#1b1e24", hover="#2c313b")
LIGHT = dict(bg="#f4f6fa", side="#eceff4", card="#ffffff", card2="#f6f8fb", border="#e1e5ec",
             text="#151820", muted="#5c6575", faint="#8a93a3", input="#ffffff", hover="#e6eaf1")

current = DARK   # active palette, set by the app via apply()
is_dark = True


def apply(theme_setting):
    global current, is_dark
    current, is_dark = palette_for(theme_setting)
    return current


# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points
ICON = dict(home="", face="", app="", lock="", unlock="",
            shield="", history="", settings="", camera="", delete="",
            add="", pause="", play="", power="", search="",
            check="", warning="", close="", keyboard="", people="",
            photo="", sync="", folder="", key="", walk="",
            monitor="", eye="", sun="", moon="", admin="",
            bolt="", info="")

_icon_family = None


def icon_family():
    global _icon_family
    if _icon_family is None:
        fams = set(QFontDatabase.families())
        _icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "Segoe UI Symbol")
    return _icon_family


def icon_font(size=14):
    f = QFont(icon_family())
    f.setPixelSize(size)
    return f


def icon_css(size):
    """Stylesheet fragment for icon-glyph labels (stylesheets override setFont)."""
    return f'font-family: "{icon_family()}"; font-size: {size}px;'


def glyph_icon(name, color, size=32):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(icon_font(int(size * 0.8)))
    p.setPen(QColor(color))
    p.drawText(pm.rect(), Qt.AlignCenter, ICON[name])
    p.end()
    return QIcon(pm)


def system_is_dark():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def palette_for(theme):
    dark = system_is_dark() if theme == "system" else theme == "dark"
    return (DARK if dark else LIGHT), dark


def logo_pixmap(size=64, bg=ACCENT):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    path = QPainterPath()
    path.addRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 16 * s, 16 * s)
    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#6aa1ff"))
    grad.setColorAt(1, QColor("#3a6cf0"))
    p.fillPath(path, grad)
    pen = QPen(QColor("white"), 4 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    for x, y, dx, dy in ((14, 14, 1, 1), (50, 14, -1, 1), (14, 50, 1, -1), (50, 50, -1, -1)):
        p.drawPolyline([QPointF((x + 9 * dx) * s, y * s), QPointF(x * s, y * s), QPointF(x * s, (y + 9 * dy) * s)])
    p.drawLine(QPointF(25 * s, 25 * s), QPointF(25 * s, 29 * s))
    p.drawLine(QPointF(39 * s, 25 * s), QPointF(39 * s, 29 * s))
    p.drawPolyline([QPointF(32 * s, 26 * s), QPointF(32 * s, 34 * s), QPointF(30 * s, 34 * s)])
    p.drawArc(QRectF(24 * s, 30 * s, 16 * s, 10 * s), 200 * 16, 140 * 16)
    p.end()
    return pm


def stylesheet(c):
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; color: {c['text']}; }}
QMainWindow, QDialog, #Page {{ background: {c['bg']}; }}
QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
#Sidebar {{ background: {c['side']}; border-right: 1px solid {c['border']}; }}
#NavButton {{ text-align: left; padding: 10px 14px; border: none; border-radius: 8px; background: transparent;
             color: {c['muted']}; font-size: 14px; }}
#NavButton:hover {{ background: {c['hover']}; color: {c['text']}; }}
#NavButton:checked {{ background: {c['card']}; color: {c['text']}; font-weight: 600; }}
#Card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 14px; }}
#SubCard {{ background: {c['card2']}; border: 1px solid {c['border']}; border-radius: 10px; }}
#H1 {{ font-size: 26px; font-weight: 700; }}
#H2 {{ font-size: 17px; font-weight: 600; }}
#Muted {{ color: {c['muted']}; }}
#Faint {{ color: {c['faint']}; font-size: 12px; }}
#Big {{ font-size: 30px; font-weight: 700; }}
QPushButton {{ background: {c['card2']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#Danger {{ color: {RED}; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT}; padding: 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#IconButton {{ background: transparent; border: none; padding: 6px; border-radius: 6px; }}
QPushButton#IconButton:hover {{ background: {c['hover']}; }}
QPushButton#Seg {{ border-radius: 0; padding: 7px 14px; }}
QPushButton#Seg:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px;
             padding: 8px 10px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['border']};
             selection-background-color: {ACCENT}; outline: none; padding: 4px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 8px; padding: 6px; }}
QListWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected {{ background: {c['hover']}; color: {c['text']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: 4px solid {ACCENT}; width: 12px; height: 12px;
             margin: -8px 0; border-radius: 10px; }}
QProgressBar {{ background: {c['border']}; border: none; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QMenu {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
"""
