"""Qt 共用样式和控件：卡片、按键按钮、省略文本及滚轮事件处理。"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

STYLE = """
QWidget { color: #0f172a; font-size: 12px; }
QWidget#window { background: #f4f6f9; }
QFrame#header, QFrame#footer { background: white; }
QFrame[card="true"] { background: white; border: 1px solid #e2e8f0; border-radius: 9px; }
QLabel { background: transparent; border: none; }
QLabel[muted="true"] { color: #64748b; }
QLabel[heading="true"] { font-weight: 600; }
QLabel#brand { font-size: 18px; font-weight: 700; }
QLabel[badge="true"] { padding: 5px 8px; border-radius: 5px; background: #f1f5f9; color: #64748b; }
QLabel[badge="true"][tone="success"] { background: #ecfdf5; color: #059669; }
QLabel[badge="true"][tone="warning"] { background: #fffbeb; color: #b45309; }
QLabel[badge="true"][tone="error"] { background: #fff1f2; color: #be123c; }
QLabel#notice { padding: 7px; border-radius: 5px; background: #f8fafc; color: #64748b; }
QLabel#notice[tone="error"] { background: #fff1f2; color: #be123c; }
QLabel#notice[tone="warning"] { background: #fffbeb; color: #b45309; }
QLabel#notice[tone="success"] { background: #ecfdf5; color: #047857; }
QPushButton { background: white; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 9px; }
QPushButton:hover { background: #f8fafc; border-color: #cbd5e1; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton:focus { border-color: #2563eb; }
QPushButton:disabled { color: #94a3b8; background: #f8fafc; }
QPushButton[role="accent"] { background: #eff6ff; border-color: #93c5fd; color: #1d4ed8; }
QPushButton[role="primary"] { background: #2563eb; color: white; border-color: #2563eb; font-weight: 600; padding: 10px; }
QPushButton[role="primary"]:hover { background: #1d4ed8; }
QPushButton[role="primary"]:disabled { background: #f1f5f9; border-color: #e2e8f0; color: #94a3b8; }
QPushButton[role="danger"] { color: #e11d48; }
QPushButton[role="tab"] { border: none; border-radius: 0; padding: 10px 3px; color: #64748b; }
QPushButton[role="tab"]:checked { color: #2563eb; border-bottom: 2px solid #2563eb; font-weight: 600; }
QPushButton[role="key"] { font-size: 16px; font-weight: 600; padding: 7px 3px; text-align: center; }
QPushButton[role="key"][compact="true"] { font-size: 12px; }
QPushButton[role="key"][selected="true"] { background: #eff6ff; border: 2px solid #2563eb; color: #1d4ed8; }
QPushButton[role="key"][inactive="true"] { color: #94a3b8; background: #f8fafc; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px; selection-background-color: #2563eb; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #2563eb; }
QSpinBox::up-button, QSpinBox::down-button { border: none; background: transparent; width: 16px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #eff6ff; selection-color: #1d4ed8; }
QCheckBox { spacing: 7px; padding: 5px 0; }
QCheckBox:focus { color: #2563eb; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #94a3b8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { background: #0f172a; color: white; border: none; border-radius: 5px; padding: 8px; }
QProgressBar { border: none; border-radius: 3px; background: #f1f5f9; max-height: 6px; }
QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
"""


def setup_qt(app: QApplication) -> None:
    """统一控件风格和中文字体；必须在 QApplication 创建后调用。"""
    app.setStyle("Fusion")
    families = set(QFontDatabase.families())
    family = next(
        (
            name
            for name in ("Microsoft YaHei UI", "Noto Sans CJK SC", "Segoe UI")
            if name in families
        ),
        app.font().family(),
    )
    app.setFont(QFont(family, 9))
    app.setApplicationName("KeyWeave")
    app.setOrganizationName("KeyWeave")


def refresh_style(widget):
    """动态属性变化后重新应用样式，让属性选择器立即生效。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def label(text="", *, muted=False, heading=False):
    widget = QLabel(text)
    widget.setProperty("muted", muted)
    widget.setProperty("heading", heading)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, command=None, role=None):
    widget = QPushButton(text)
    widget.setAutoDefault(False)
    if role:
        widget.setProperty("role", role)
    if command:
        widget.clicked.connect(command)
    return widget


def card(parent_layout=None):
    widget = QFrame()
    widget.setProperty("card", True)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    if parent_layout is not None:
        parent_layout.addWidget(widget)
    return widget, layout


class SpinBox(QSpinBox):
    """滚轮只改变自身数值，不向父滚动区传播（包括数值已到边界时）。"""

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ComboBox(QComboBox):
    """限制下拉框宽度，并接收滚轮事件，避免父页面同时滚动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumContentsLength(6)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class ElidedLabel(QLabel):
    """按可用宽度省略文本，悬浮提示始终显示完整内容。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setMinimumWidth(20)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.set_full_text(text)

    def sizeHint(self):
        return QSize(
            min(self.maximumWidth(), self.fontMetrics().horizontalAdvance(self.full_text) + 16),
            super().sizeHint().height(),
        )

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self._fit()
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        self.setText(
            self.fontMetrics().elidedText(
                self.full_text, Qt.TextElideMode.ElideRight, max(20, self.width() - 16)
            )
        )


class KeyButton(QPushButton):
    """展示键位和选中状态，双击通过 editRequested 请求录入。"""

    editRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "key")
        self.setMinimumWidth(60)
        self.setMinimumHeight(64)
        self.setAutoDefault(False)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.editRequested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def set_state(self, binding, selected, enabled, suffix=""):
        """按组合键长度排版；内容和状态均未变化时跳过文本与样式更新。"""
        full = binding.label
        font = QFont(self.font())
        font.setPixelSize(16)
        compact = QFontMetrics(font).horizontalAdvance(full) > 78
        font.setPixelSize(12 if compact else 16)
        metrics = QFontMetrics(font)
        parts, line = [], ""
        for part in full.split("+"):
            candidate = f"{line}+{part}" if line else part
            if line and metrics.horizontalAdvance(candidate + "+") > 78:
                parts.append(line + "+")
                line = part
            else:
                line = candidate
        parts.append(line)
        text = "\n".join(parts) if compact else full
        if suffix:
            text += "\n" + suffix
        state = (text, selected, enabled, compact)
        if getattr(self, "_state", None) == state:
            return
        self._state = state
        self.setText(text)
        self.setToolTip(full)
        self.setProperty("selected", selected)
        self.setProperty("inactive", not enabled)
        self.setProperty("compact", compact)
        refresh_style(self)
