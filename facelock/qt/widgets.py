"""Reusable widgets: the Face ID scanner ring, toggles, setting rows, hotkey recorder."""

import math

from PySide6.QtCore import (Property, QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import vision
from . import theme


def colors():
    return theme.current


class FaceScanner(QWidget):
    """Circular live camera preview with an animated progress ring, like Face ID."""

    finished = Signal()
    statusChanged = Signal(str)

    def __init__(self, diameter=220, parent=None):
        super().__init__(parent)
        self.d = diameter
        self.setFixedSize(diameter + 28, diameter + 28)
        self.worker = None
        self.image = None
        self.state = vision.SEARCH
        self.target_progress = 0.0
        self.progress = 0.0
        self.focus = None
        self.zoom_center = None
        self.angle = 0.0
        self.ok_t = 0.0
        self.status_text = ""
        self._emitted = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def start(self, hub, processor):
        self.stop()
        self.processor = processor
        self.worker = vision.VisionWorker(hub, processor)
        self.worker.start()
        self.state, self.progress, self.ok_t, self._emitted = vision.SEARCH, 0.0, 0.0, False
        self.timer.start(33)

    def stop(self):
        if self.worker:
            self.worker.stop()

    @property
    def camera_failed(self):
        return bool(self.worker and self.worker.camera_failed)

    def _tick(self):
        self.angle = (self.angle + 5) % 360
        w = self.worker
        if w:
            latest = w.latest
            if latest:
                rgb, status = latest
                if rgb is not None:
                    h, wd = rgb.shape[:2]
                    self.image = QImage(rgb.data, wd, h, 3 * wd, QImage.Format_RGB888).copy()
                else:
                    self.image, self.state = None, vision.OFF
                if status != self.status_text:
                    self.status_text = status
                    self.statusChanged.emit(status)
            p = self.processor
            if self.state != vision.OK and rgb_ok(latest):
                self.state = getattr(p, "state", vision.SCAN)
            self.target_progress = float(getattr(p, "progress", 0.0))
            self.focus = getattr(p, "focus", None)
            if w.finished and not self._emitted:
                self._emitted = True
                self.state, self.target_progress = vision.OK, 1.0
                QTimer.singleShot(650, self.finished.emit)
        self.progress += (self.target_progress - self.progress) * 0.18
        if self.state == vision.OK:
            self.ok_t = min(1.0, self.ok_t + 0.07)
        # Smoothly follow the face so the circle stays centred on it.
        if self.focus:
            fx, fy, fw = 1 - self.focus[0], self.focus[1], self.focus[2]
            if self.zoom_center is None:
                self.zoom_center = [fx, fy, fw]
            else:
                for i, v in enumerate((fx, fy, fw)):
                    self.zoom_center[i] += (v - self.zoom_center[i]) * 0.15
        self.update()

    def paintEvent(self, _):
        c = colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        m = 14
        rect = QRectF(m, m, self.d, self.d)
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.save()
        p.setClipPath(clip)
        if self.image is not None:
            iw, ih = self.image.width(), self.image.height()
            if self.zoom_center:
                cx, cy, fw = self.zoom_center
                s = min(max(fw * iw * 2.4, ih * 0.5), ih)
                cx, cy = cx * iw, cy * ih
            else:
                s, cx, cy = ih, iw / 2, ih / 2
            cx = min(max(cx, s / 2), iw - s / 2)
            cy = min(max(cy, s / 2), ih - s / 2)
            p.drawImage(rect, self.image, QRectF(cx - s / 2, cy - s / 2, s, s))
            vignette = QRadialGradient(rect.center(), self.d / 2)
            vignette.setColorAt(0.75, QColor(0, 0, 0, 0))
            vignette.setColorAt(1.0, QColor(0, 0, 0, 90))
            p.fillRect(rect, vignette)
        else:
            p.fillRect(rect, QColor(c["card2"]))
            p.setFont(theme.icon_font(int(self.d * 0.22)))
            p.setPen(QColor(c["faint"]))
            p.drawText(rect, Qt.AlignCenter, theme.ICON["camera"])
        if self.ok_t > 0:
            p.fillRect(rect, QColor(52, 199, 89, int(110 * self.ok_t)))
        p.restore()

        if self.ok_t > 0:
            self._draw_check(p, rect)

        ring = rect.adjusted(-7, -7, 7, 7)
        track = QColor(c["border"])
        p.setPen(QPen(track, 5, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(ring)
        st = self.state
        if st == vision.SEARCH:
            p.setPen(QPen(QColor(theme.ACCENT), 5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, int(-self.angle * 16), 70 * 16)
        elif st in (vision.SCAN, vision.TURN):
            p.setPen(QPen(QColor(theme.ACCENT), 5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-max(0.03, self.progress) * 360 * 16))
        elif st == vision.FAIL:
            a = int(140 + 115 * (0.5 + 0.5 * math.sin(self.angle / 18)))
            col = QColor(theme.ORANGE)
            col.setAlpha(a)
            p.setPen(QPen(col, 5))
            p.drawEllipse(ring)
        elif st == vision.OK:
            p.setPen(QPen(QColor(theme.GREEN), 5))
            p.drawEllipse(ring)
        elif st == vision.OFF:
            p.setPen(QPen(QColor(theme.RED), 5, Qt.DashLine))
            p.drawEllipse(ring)

        if st == vision.TURN:
            bob = 4 * math.sin(self.angle / 12)
            p.setFont(theme.icon_font(22))
            p.setPen(QColor("white"))
            yc = rect.center().y()
            p.drawText(QRectF(rect.left() + 10 - bob, yc - 16, 30, 32), Qt.AlignCenter, "")
            p.drawText(QRectF(rect.right() - 40 + bob, yc - 16, 30, 32), Qt.AlignCenter, "")
        p.end()

    def _draw_check(self, p, rect):
        t = QEasingCurve(QEasingCurve.OutBack).valueForProgress(self.ok_t)
        cx, cy, r = rect.center().x(), rect.center().y(), self.d * 0.18 * t
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.GREEN))
        p.drawEllipse(QPointF(cx, cy), r, r)
        pen = QPen(QColor("white"), max(2.0, r * 0.18), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        k = r / 30
        p.drawPolyline([QPointF(cx - 13 * k, cy + 1 * k), QPointF(cx - 4 * k, cy + 10 * k),
                        QPointF(cx + 14 * k, cy - 9 * k)])

    def hideEvent(self, e):
        self.stop()
        super().hideEvent(e)


def rgb_ok(latest):
    return latest is not None and latest[0] is not None


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self, duration=160, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = v
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _):
        c = colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, 44, 24)
        off, on = QColor(c["border"]), QColor(theme.ACCENT)
        mix = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * self._pos,
                              off.greenF() + (on.greenF() - off.greenF()) * self._pos,
                              off.blueF() + (on.blueF() - off.blueF()) * self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(r, 12, 12)
        p.setBrush(QColor("white"))
        x = 4 + self._pos * 20
        p.drawEllipse(QRectF(x, 4, 18, 18))
        p.end()


class IconBadge(QLabel):
    def __init__(self, name, color=theme.ACCENT, size=36, parent=None):
        super().__init__(theme.ICON[name], parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(theme.icon_font(int(size * 0.45)))
        bg = QColor(color)
        bg.setAlpha(38)
        self.setStyleSheet(f"background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
                           f"color: {color}; border-radius: {size // 4}px;" + theme.icon_css(int(size * 0.45)))


def label(text, name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 18)
        self.lay.setSpacing(4)
        self._rows = 0
        if title:
            self.lay.addWidget(label(title, "H2"))
        if subtitle:
            self.lay.addWidget(label(subtitle, "Muted", wrap=True))
        if title or subtitle:
            self.lay.addSpacing(8)

    def add(self, widget):
        if self._rows and isinstance(widget, SettingRow):
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {colors()['border']};")
            self.lay.addWidget(line)
        self._rows += 1
        self.lay.addWidget(widget)
        return widget


class SettingRow(QWidget):
    def __init__(self, icon, title, subtitle=None, control=None, color=theme.ACCENT, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(14)
        if icon:
            lay.addWidget(IconBadge(icon, color), 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        t = label(title)
        t.setStyleSheet("font-weight: 600;")
        text.addWidget(t)
        if subtitle:
            self.sub = label(subtitle, "Muted", wrap=True)
            text.addWidget(self.sub)
        lay.addLayout(text, 1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignVCenter)


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options, value, parent=None):
        """options: [(value, text)]"""
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        for i, (val, text) in enumerate(options):
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(val == value)
            b.setCursor(Qt.PointingHandCursor)
            radius = "border-top-left-radius:8px;border-bottom-left-radius:8px;" if i == 0 else ""
            if i == len(options) - 1:
                radius += "border-top-right-radius:8px;border-bottom-right-radius:8px;"
            b.setStyleSheet(radius)
            b.clicked.connect(lambda _=False, v=val: self.changed.emit(v))
            self.group.addButton(b)
            lay.addWidget(b)


_KEY_NAMES = {Qt.Key_Space: "space", Qt.Key_Home: "home", Qt.Key_End: "end", Qt.Key_Insert: "insert",
              Qt.Key_Pause: "pause", Qt.Key_Delete: "delete", Qt.Key_Tab: "tab"}


class HotkeyEdit(QLineEdit):
    """Click, then press a shortcut to record it."""

    recording = Signal(bool)
    chosen = Signal(str)

    def __init__(self, value, parent=None):
        super().__init__(value, parent)
        self.value = value
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(190)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click, then press your shortcut")

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.setText("Press a shortcut…")
        self.recording.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.setText(self.value)
        self.recording.emit(False)

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_unknown):
            return
        mods = e.modifiers()
        if key == Qt.Key_Escape and not mods:
            self.clearFocus()
            return
        parts = []
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.AltModifier:
            parts.append("alt")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.MetaModifier:
            parts.append("win")
        if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
            name = chr(key).lower()
        elif Qt.Key_F1 <= key <= Qt.Key_F24:
            name = f"f{key - Qt.Key_F1 + 1}"
        else:
            name = _KEY_NAMES.get(key)
        if not name or not parts:
            self.setText("Use a modifier + key")
            return
        combo = "+".join(parts + [name])
        self.value = combo
        self.clearFocus()
        self.chosen.emit(combo)


class PinField(QLineEdit):
    def __init__(self, placeholder="Enter PIN", parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.Password)
        self.setPlaceholderText(placeholder)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(40)
        self.setStyleSheet("font-size: 16px; letter-spacing: 4px;")


def hspacer():
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w
