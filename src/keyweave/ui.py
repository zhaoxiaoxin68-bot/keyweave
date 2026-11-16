"""Qt 主窗口、小窗和循环编辑器；通过 Runtime 队列提交命令并读取状态。"""

import json
import queue
import re
import threading
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import from_dict, to_dict
from .driver_setup import install_driver
from .models import AppConfig, CycleStep
from .sound import play_sound
from .ui_widgets import (
    STYLE,
    ComboBox,
    ElidedLabel,
    KeyButton,
    SpinBox,
    button,
    card,
    label,
    refresh_style,
)

HOTKEY_LABELS = {
    "toggle": "启动 / 停止连发",
    "cycle": "选择下一个按键",
    "switch_set": "选择下一个分组",
    "reset_cycle": "循环归位",
    "auxiliary": "额外按键连发",
}
BACKENDS = {
    "自动（驱动优先）": "auto",
    "SendInput（免驱基础）": "sendinput",
    "Interception（驱动模式）": "interception",
}
HOTKEY_HELP = {
    "toggle": "按一次启动，再按一次停止主连发。",
    "cycle": "按自定义循环选择下一步；未设循环时选择组内下一个按键。",
    "switch_set": "选择下一个分组。启用开关控制此快捷键是否生效。",
    "reset_cycle": "回到循环的第一个可用步骤；未设循环时选择组内首个可用按键，不会清除配置。",
    "auxiliary": "重复发送此快捷键本身；再按一次停止，最长 10 秒，可独立运行。",
}
MODES = (
    ("quick_switch", "按已配置的键选中对应按键", "支持跨组选择，例如按 Q 选中另一组的 Q。"),
    (
        "sync_mode",
        "依次连发（每键间隔 50ms）",
        "当前组的启用按键轮流发送；按住连发时仅轮换按住的键。",
    ),
    ("hold_mode", "持续按住选中键", "由程序保持按下，直到停止；与依次连发、按住连发互斥。"),
    (
        "wait_start",
        "启动后，按任意键开始",
        "启动后先等待；非快捷键的实际按键触发运行，快捷键仍执行原操作。",
    ),
    ("hold_repeat", "按住连发，松开停止", "启动后，仅在你按住已配置按键时重复发送；松开即停止。"),
)


def parse_cycle(text: str) -> tuple[CycleStep, ...]:
    """将从一开始的“组号.按键序号”转为内部索引；此处只检查格式与步数。"""
    tokens = re.split(r"[\s,，→>-]+", text.strip()) if text.strip() else []
    steps = []
    for token in tokens:
        if not re.fullmatch(r"[1-9][0-9]*\.[1-9][0-9]*", token):
            raise ValueError(f"无效步骤 {token!r}；请用1.1、2.5、3.12这样的组号.按键序号")
        group, slot = token.split(".")
        steps.append(CycleStep(int(group) - 1, int(slot) - 1))
    if len(steps) > 60:
        raise ValueError("最多60步")
    return tuple(steps)


def row(layout):
    result = QHBoxLayout()
    result.setSpacing(6)
    layout.addLayout(result)
    return result


def scroll_area(content):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def set_combo(box, values, index):
    """更新下拉选项时阻断信号，防止程序同步被当成用户操作。"""
    with QSignalBlocker(box):
        box.clear()
        box.addItems(values)
        box.setCurrentIndex(index)


class CycleDialog(QDialog):
    """编辑或录制循环步骤；按键事件由输入线程接收，界面只显示录制快照。"""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.last_recorded = None
        self.setWindowTitle("自定义循环顺序")
        self.setMinimumSize(460, 310)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.addWidget(label("自定义循环顺序 · 最多 60 步", heading=True))
        hint = label("输入组号.按键序号，以空格或逗号分隔；也可实时录制。", muted=True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(label("示例：1.1 → 1.1 → 2.7", muted=True))
        self.status = label("未在录制 · 可直接编辑顺序", muted=True)
        layout.addWidget(self.status)
        self.text = QPlainTextEdit(" → ".join(step.label for step in owner.config.cycle_steps))
        layout.addWidget(self.text, 1)
        controls = row(layout)
        self.start_button = button("开始记录", self.start_recording)
        self.stop_button = button("停止记录", lambda: owner._command("stop_recording"))
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        controls.addWidget(button("保存顺序", self.save, "primary"))
        if owner.snapshot is not None:
            self.render(owner.snapshot)
        else:
            self.stop_button.setEnabled(False)

    def start_recording(self):
        self.last_recorded = None
        self.owner._command("start_recording")

    def render(self, snapshot):
        """同步录制状态；仅在录制内容变化时覆盖文本，保留手动编辑内容。"""
        self.start_button.setEnabled(not snapshot.recording)
        self.stop_button.setEnabled(snapshot.recording)
        self.status.setText(
            f"正在记录 · {len(snapshot.recorded_steps)} / 60 步"
            if snapshot.recording
            else "未在录制 · 可直接编辑顺序"
        )
        if snapshot.recording and snapshot.recorded_steps != self.last_recorded:
            self.last_recorded = snapshot.recorded_steps
            self.text.setPlainText(" → ".join(step.label for step in snapshot.recorded_steps))

    def save(self):
        """先校验步骤引用再提交保存；本地校验失败时保持编辑器打开。"""
        try:
            steps = parse_cycle(self.text.toPlainText())
            # 在关闭编辑器之前完成引用校验，避免无效输入直接丢失。
            from dataclasses import replace

            replace(self.owner.config, cycle_steps=steps).validate()
        except ValueError as error:
            QMessageBox.warning(self, "循环顺序无效", str(error))
            return
        self.owner._command("set_cycle", steps)
        self.accept()

    def done(self, result):
        if not self.owner.closed:
            self.owner._command("stop_recording")
        self.owner.cycle_dialog = None
        super().done(result)


class MiniWindow(QWidget):
    """复用主窗口的运行状态和命令入口，关闭小窗时返回主窗口。"""

    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setObjectName("window")
        self.setStyleSheet(STYLE)
        self.setWindowTitle("键序 · 小窗")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(7)
        top = row(layout)
        self.status = ElidedLabel()
        top.addWidget(self.status, 1)
        self.alpha = button("100%", self.cycle_alpha)
        top.addWidget(self.alpha)
        top.addWidget(button("展开", self.close, "accent"))
        self.groups = ComboBox()
        self.groups.activated.connect(lambda i: owner._command("switch_set", i))
        layout.addWidget(self.groups)
        self.keys = QWidget()
        self.grid = QGridLayout(self.keys)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.area = scroll_area(self.keys)
        layout.addWidget(self.area)
        actions = row(layout)
        self.auxiliary = button("额外连发", lambda: owner._command("toggle_auxiliary"))
        self.toggle = button("启动连发", lambda: owner._command("toggle_running"), "primary")
        actions.addWidget(self.auxiliary)
        actions.addWidget(self.toggle, 1)
        self.cards = []
        self.group_state = None
        self.slot_count = None

    def render(self, snapshot):
        config = snapshot.config
        group_state = (config.group_labels, config.active_set)
        if self.group_state != group_state:
            set_combo(self.groups, config.group_labels, config.active_set)
            self.group_state = group_state
        self.groups.setEnabled(self.owner.pending_capture is None)
        if self.slot_count != len(config.slots):
            while self.grid.count():
                widget = self.grid.takeAt(0).widget()
                widget.hide()
                widget.deleteLater()
            self.cards = []
            self.slot_count = len(config.slots)
            if not config.slots:
                self.grid.addWidget(label("此分组没有按键", muted=True), 0, 0, 1, 3)
            for index in range(len(config.slots)):
                key = KeyButton()
                key.clicked.connect(lambda checked=False, i=index: self.owner._command("select", i))
                self.grid.addWidget(key, index // 3, index % 3)
                self.cards.append(key)
            for column in range(3):
                self.grid.setColumnStretch(column, 1)
        for index, slot in enumerate(config.slots):
            self.cards[index].set_state(slot.binding, index == config.selected_slot, slot.enabled)
        self.status.set_full_text(snapshot.status + " · " + config.group_labels[config.active_set])
        self.toggle.setText(
            "取消等待" if snapshot.armed else "停止连发" if snapshot.running else "启动连发"
        )
        self.toggle.setEnabled(self.owner.toggle_button.isEnabled())
        self.auxiliary.setEnabled(self.owner.auxiliary_btn.isEnabled())
        self.alpha.setText(f"{config.mini_alpha:.0%}")
        self.setWindowOpacity(config.mini_alpha)
        # 固定显示完整行，更多按键通过滚动访问。
        row_count = min(3, max(1, (len(config.slots) + 2) // 3))
        row_height = max((key.sizeHint().height() for key in self.cards), default=32)
        row_height = max(64, row_height) if self.cards else 32
        self.area.setFixedHeight(row_count * row_height + (row_count - 1) * 4 + 2)
        self.adjustSize()

    def cycle_alpha(self):
        options = (1.0, 0.85, 0.7, 0.55)
        self.owner._command(
            "set_options",
            mini_alpha=options[(options.index(self.owner.config.mini_alpha) + 1) % len(options)],
        )

    def closeEvent(self, event):
        self.owner.mini = None
        if not self.owner.closed:
            self.owner.show()
            self.owner.raise_()
        event.accept()


class Application(QWidget):
    """创建并保留两页控件，通过定时读取快照更新界面，不直接操作输入后端。"""

    def __init__(self, runtime, config: AppConfig, parent=None):
        super().__init__(parent)
        self.runtime, self.config = runtime, config
        self.snapshot = None
        self.rendered_backend = self.rendered_backend_note = ""
        self.rendered_capture = False
        self.pending_capture = None
        self.force_refresh = True
        self.closed = False
        self.mini = self.cycle_dialog = None
        self.driver_installing = False
        self.driver_events = queue.Queue()
        self.card_count = None
        self.cards = []
        self.group_state = None
        self.setObjectName("window")
        self.setStyleSheet(STYLE)
        self.setWindowTitle("键序 · KeyWeave")
        self.resize(480, 680)
        self.setMinimumSize(460, 600)
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def _build(self):
        """一次性建立主页面、设置页面和底栏，后续页面切换复用已有控件。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        from PySide6.QtWidgets import QFrame

        header = QFrame()
        header.setObjectName("header")
        top = QHBoxLayout(header)
        top.setContentsMargins(14, 10, 14, 10)
        brand = label("键序")
        brand.setObjectName("brand")
        top.addWidget(brand)
        self.header_status = ElidedLabel("正在初始化…")
        self.header_status.setProperty("badge", True)
        top.addWidget(self.header_status, 1)
        self.backend_badge = label("初始化")
        self.backend_badge.setProperty("badge", True)
        top.addWidget(self.backend_badge)
        top.addWidget(button("小窗 ↗", self.open_mini))
        layout.addWidget(header)
        nav_frame = QFrame()
        nav_frame.setObjectName("header")
        nav = QHBoxLayout(nav_frame)
        nav.setContentsMargins(14, 0, 14, 0)
        self.tab_buttons = []
        for index, text in enumerate(("按键控制", "高级设置")):
            tab = button(text, lambda checked=False, i=index: self.select_page(i), "tab")
            tab.setCheckable(True)
            nav.addWidget(tab)
            self.tab_buttons.append(tab)
        self.tab_buttons[0].setChecked(True)
        nav.addStretch()
        self.active_group = ElidedLabel()
        self.active_group.setMaximumWidth(150)
        self.active_group.setProperty("muted", True)
        nav.addWidget(self.active_group)
        layout.addWidget(nav_frame)
        self.pages = QStackedWidget()
        self.main_page = QWidget()
        main = QVBoxLayout(self.main_page)
        main.setContentsMargins(12, 10, 12, 10)
        main.setSpacing(8)
        self._build_main(main)
        settings_content = QWidget()
        settings = QVBoxLayout(settings_content)
        settings.setContentsMargins(12, 10, 12, 10)
        settings.setSpacing(8)
        self._build_advanced(settings)
        settings.addStretch()
        self.settings_area = scroll_area(settings_content)
        self.pages.addWidget(self.main_page)
        self.pages.addWidget(self.settings_area)
        layout.addWidget(self.pages, 1)
        self.pages.currentChanged.connect(
            lambda i: [tab.setChecked(n == i) for n, tab in enumerate(self.tab_buttons)]
        )
        footer = QFrame()
        footer.setObjectName("footer")
        actions = QVBoxLayout(footer)
        actions.setContentsMargins(14, 10, 14, 10)
        actions.setSpacing(6)
        self.toggle_button = button("启动连发", lambda: self._command("toggle_running"), "primary")
        actions.addWidget(self.toggle_button)
        sub = row(actions)
        self.auxiliary_btn = button("额外按键连发", lambda: self._command("toggle_auxiliary"))
        self.stop_button = button("停止全部", lambda: self._command("stop"))
        for action in (self.toggle_button, self.auxiliary_btn, self.stop_button):
            action.setEnabled(False)
        sub.addWidget(self.auxiliary_btn)
        sub.addStretch()
        sub.addWidget(self.stop_button)
        self.notice = label()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        actions.addWidget(self.notice)
        layout.addWidget(footer)

    def select_page(self, index):
        """切换已有页面，保留控件内容和各自的滚动位置。"""
        self.pages.setCurrentIndex(index)

    def _build_main(self, layout):
        _, group = card(layout)
        controls = row(group)
        controls.addWidget(label("按键组", heading=True))
        self.group_box = ComboBox()
        self.group_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.group_box.setMinimumWidth(80)
        self.group_box.activated.connect(lambda i: self._command("switch_set", i))
        controls.addWidget(self.group_box, 1)
        self.group_buttons = [
            button("+ 新建", self._add_group),
            button("重命名", self._rename_group),
            button("删除", self._remove_group, "danger"),
        ]
        for action in self.group_buttons:
            controls.addWidget(action)
        _, keys = card(layout)
        tools = row(keys)
        tools.addWidget(label("按键序列", heading=True))
        instruction = label("单击选择 · 双击改键", muted=True)
        instruction.setToolTip("单击选择 · 双击改键 · 右键启用、禁用或删除")
        tools.addWidget(instruction, 1)
        tools.addWidget(button("循环顺序", self.open_cycle))
        tools.addWidget(button("+ 添加键", self._add_key, "accent"))
        self.key_content = QWidget()
        self.key_grid = QGridLayout(self.key_content)
        self.key_grid.setContentsMargins(0, 0, 0, 0)
        self.key_grid.setSpacing(6)
        self.key_area = scroll_area(self.key_content)
        self.key_area.setMinimumHeight(78)
        self.key_area.setMaximumHeight(164)
        keys.addWidget(self.key_area)
        _, params = card(layout)
        timing = row(params)
        timing.addWidget(label("连发间隔"))
        self.interval_entry = SpinBox()
        self.interval_entry.setRange(5, 2000)
        self.interval_entry.setSuffix(" ms")
        self.interval_entry.setKeyboardTracking(False)
        self.interval_entry.editingFinished.connect(self._apply_timing)
        timing.addWidget(self.interval_entry, 1)
        timing.addSpacing(8)
        timing.addWidget(label("按住时长"))
        self.hold_entry = SpinBox()
        self.hold_entry.setRange(1, 500)
        self.hold_entry.setSuffix(" ms")
        self.hold_entry.setKeyboardTracking(False)
        self.hold_entry.editingFinished.connect(self._apply_timing)
        timing.addWidget(self.hold_entry, 1)
        scope = row(params)
        scope.addWidget(label("响应窗口", heading=True))
        self.scope_box = ComboBox()
        self.scope_box.setEditable(True)
        self.scope_box.addItem("所有程序")
        self.scope_box.setMinimumWidth(70)
        self.scope_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scope_box.activated.connect(self._apply_scope)
        self.scope_box.lineEdit().editingFinished.connect(self._apply_scope)
        scope.addWidget(self.scope_box, 1)
        scope.addWidget(button("刷新", self._refresh_windows))
        self.scope_status = label(muted=True)
        self.scope_status.setWordWrap(True)
        params.addWidget(self.scope_status)
        layout.addStretch(1)

    def _build_advanced(self, layout):
        _, hotkeys = card(layout)
        head = row(hotkeys)
        head.addWidget(label("快捷键", heading=True))
        head.addWidget(label("点击按键修改", muted=True))
        head.addStretch()
        self.hotkey_buttons, self.hotkey_checks = {}, {}
        self.option_labels = {}
        for name, title in HOTKEY_LABELS.items():
            line = row(hotkeys)
            title_widget = label(title)
            title_widget.setToolTip(HOTKEY_HELP[name])
            self.option_labels[name] = title_widget
            line.addWidget(title_widget, 1)
            if name in {"switch_set", "reset_cycle"}:
                flag = "switch_enabled" if name == "switch_set" else "reset_enabled"
                check = QCheckBox("启用")
                check.setToolTip(HOTKEY_HELP[name])
                check.clicked.connect(
                    lambda checked, n=flag: self._command("set_hotkey_enabled", n, checked)
                )
                self.hotkey_checks[flag] = check
                line.addWidget(check)
            key = button("未设置", lambda checked=False, n=name: self._capture_hotkey(n), "accent")
            key.setFixedWidth(110)
            self.hotkey_buttons[name] = key
            line.addWidget(key)
        _, modes = card(layout)
        modes.addWidget(label("运行方式", heading=True))
        self.flags = {}
        for name, title, help_text in MODES:
            check = QCheckBox(title)
            check.setToolTip(help_text)
            check.clicked.connect(lambda checked, n=name: self._toggle_option(n, checked))
            modes.addWidget(check)
            self.flags[name] = check
            self.option_labels[name] = check
        _, backend = card(layout)
        backend.addWidget(label("按键发送方式", heading=True))
        self.backend_box = ComboBox()
        self.backend_box.addItems(BACKENDS)
        self.backend_box.activated.connect(self._apply_backend)
        backend.addWidget(self.backend_box)
        device_row = row(backend)
        device_label = label("键盘设备号", muted=True)
        device_label.setToolTip("0 自动选择可用键盘；1–10 指定 Interception 键盘设备。")
        device_row.addWidget(device_label)
        self.device = SpinBox()
        self.device.setRange(0, 10)
        device_row.addWidget(self.device)
        device_row.addWidget(button("应用", self._apply_backend))
        self.backend_status = label(muted=True)
        self.backend_status.setWordWrap(True)
        backend.addWidget(self.backend_status)
        self.driver_button = button("安装 / 修复驱动…", self._install_driver)
        backend.addWidget(self.driver_button)
        self.driver_status = label(muted=True)
        self.driver_status.setWordWrap(True)
        backend.addWidget(self.driver_status)
        _, volume = card(layout)
        sound = row(volume)
        sound.addWidget(label("提示音量", heading=True), 1)
        self.volume = SpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(" %")
        sound.addWidget(self.volume)
        sound.addWidget(
            button("应用", lambda: self._command("set_options", volume=self.volume.value()))
        )
        _, config = card(layout)
        tools = row(config)
        tools.addWidget(button("导入配置…", self.import_config))
        tools.addWidget(button("导出配置…", self.export_config))
        tools.addStretch()
        tools.addWidget(button("恢复默认设置…", self._reset_settings, "danger"))
        config.addWidget(label("修改自动保存到本机", muted=True))

    def _command(self, name, *args, **kwargs):
        """返回命令是否成功入队；True 不代表运行线程已执行或保存成功。"""
        try:
            self.runtime.command(name, *args, **kwargs)
            return True
        except (RuntimeError, ValueError) as error:
            self._show_notice(str(error), "error")
            return False

    def _show_notice(self, text, kind="info"):
        self.notice.setText(str(text))
        self.notice.setProperty("tone", kind)
        self.notice.setVisible(bool(text))
        refresh_style(self.notice)

    def _apply_timing(self):
        values = self.interval_entry.value(), self.hold_entry.value()
        if values != (self.config.interval_ms, self.config.hold_ms):
            self._command("set_options", interval_ms=values[0], hold_ms=values[1])

    def _toggle_option(self, name, enabled):
        values = {name: enabled}
        if enabled and name in {"hold_mode", "sync_mode"}:
            values["sync_mode" if name == "hold_mode" else "hold_mode"] = False
        if enabled and name in {"hold_mode", "hold_repeat"}:
            values["hold_repeat" if name == "hold_mode" else "hold_mode"] = False
        self._command("set_options", **values)

    def _apply_scope(self, *_):
        target = self.scope_box.currentText().strip()
        target = "" if target == "所有程序" else target
        if target != self.config.scope_name:
            self._command("set_options", scope_name=target)

    def _refresh_windows(self):
        self._command("list_windows")

    def _apply_backend(self, *_):
        self._command(
            "set_options",
            backend=BACKENDS[self.backend_box.currentText()],
            keyboard_device=self.device.value(),
        )

    def _add_group(self):
        if self.pending_capture is not None:
            return
        name, accepted = QInputDialog.getText(self, "新建分组", "分组名称")
        if accepted:
            self._command("add_group", name)

    def _rename_group(self):
        if self.pending_capture is not None:
            return
        expected = self.config
        name, accepted = QInputDialog.getText(
            self, "重命名分组", "分组名称", text=expected.group_labels[expected.active_set]
        )
        if accepted:
            self._command("rename_group", name, _expected_config=expected)

    def _remove_group(self):
        if self.pending_capture is not None:
            return
        if len(self.config.sets) == 1:
            self._show_notice("至少保留一个分组；可以删除组内所有按键。")
            return
        expected = self.config
        if (
            QMessageBox.question(
                self,
                "删除分组",
                f"删除“{expected.group_labels[expected.active_set]}”及其中的按键？",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self._command("remove_group", _expected_config=expected)

    def _add_key(self):
        expected = self.config
        self._capture(
            lambda binding: self._command("add_slot", binding, _expected_config=expected), expected
        )

    def _capture_slot(self, index, expected=None):
        expected = expected or self.config
        self._capture(
            lambda binding: self._command("set_slot", index, binding, _expected_config=expected),
            expected,
        )

    def _capture_hotkey(self, name):
        self._capture(lambda binding: self._command("set_hotkey", name, binding))

    def _capture(self, callback, expected=None):
        """登记一次键位捕获回调；命令入队失败时撤销登记，允许再次录入。"""
        if self.pending_capture is not None:
            return
        self.pending_capture = callback
        if not self._command("capture", _expected_config=expected or self.config):
            self.pending_capture = None
            return
        self._show_notice("请按下要设置的键或组合键，Esc 取消（12 秒超时）")

    def _key_menu(self, index, point):
        """记录菜单打开时的配置，避免菜单操作误改随后切换到的分组。"""
        if self.pending_capture is not None:
            return
        expected = self.config
        menu = QMenu(self)
        menu.addAction("修改按键…", lambda: self._capture_slot(index, expected))
        menu.addAction(
            "禁用" if self.config.slots[index].enabled else "启用",
            lambda: self._command("toggle_slot", index, _expected_config=expected),
        )
        menu.addSeparator()
        menu.addAction(
            "删除按键", lambda: self._command("remove_slot", index, _expected_config=expected)
        )
        menu.exec(self.cards[index].mapToGlobal(point))

    def _render_keys(self, snapshot, unavailable):
        """按键数量变化时才重建卡片，其余更新复用控件并刷新状态。"""
        slots = self.config.slots
        if self.card_count != len(slots):
            while self.key_grid.count():
                widget = self.key_grid.takeAt(0).widget()
                widget.hide()
                widget.deleteLater()
            self.cards = []
            self.card_count = len(slots)
            if not slots:
                self.key_grid.addWidget(label("此分组还没有按键", muted=True), 0, 0, 1, 4)
                self.key_grid.addWidget(
                    button("添加第一个按键", self._add_key, "accent"), 1, 0, 1, 4
                )
            for index in range(len(slots)):
                key = KeyButton()
                key.clicked.connect(lambda checked=False, i=index: self._command("select", i))
                key.editRequested.connect(lambda i=index: self._capture_slot(i))
                key.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                key.customContextMenuRequested.connect(
                    lambda point, i=index: self._key_menu(i, point)
                )
                self.key_grid.addWidget(key, index // 4, index % 4)
                self.cards.append(key)
            for column in range(4):
                self.key_grid.setColumnStretch(column, 1)
        held = set(snapshot.held_keys)
        for index, slot in enumerate(slots):
            selected = index == self.config.selected_slot
            active = snapshot.running and snapshot.scope_allowed and not unavailable
            sending = active and (slot.binding in held if self.config.hold_repeat else selected)
            suffix = (
                "已禁用"
                if not slot.enabled
                else "连发中"
                if sending
                else "当前选中"
                if selected
                else ""
            )
            self.cards[index].set_state(
                slot.binding, selected or (active and slot.binding in held), slot.enabled, suffix
            )
            self.cards[index].setEnabled(self.pending_capture is None)
        height = max((key.sizeHint().height() for key in self.cards), default=64)
        rows = min(2, max(1, (len(slots) + 3) // 4))
        self.key_area.setFixedHeight(min(190, rows * max(64, height) + (rows - 1) * 6 + 2))

    def _sync_settings(self, previous, initial):
        """同步配置并阻断信号；数值和下拉框仅在对应配置变化或强制刷新时回填。"""
        for field, widget in (
            ("interval_ms", self.interval_entry),
            ("hold_ms", self.hold_entry),
            ("keyboard_device", self.device),
            ("volume", self.volume),
        ):
            if (
                initial
                or self.force_refresh
                or getattr(previous, field) != getattr(self.config, field)
            ):
                with QSignalBlocker(widget):
                    widget.setValue(getattr(self.config, field))
        if initial or self.force_refresh or previous.scope_name != self.config.scope_name:
            with QSignalBlocker(self.scope_box):
                self.scope_box.setCurrentText(self.config.scope_name or "所有程序")
        if initial or self.force_refresh or previous.backend != self.config.backend:
            with QSignalBlocker(self.backend_box):
                self.backend_box.setCurrentIndex(
                    tuple(BACKENDS.values()).index(self.config.backend)
                )
        for name, check in self.flags.items():
            with QSignalBlocker(check):
                check.setChecked(getattr(self.config, name))
        for name, key in self.hotkey_buttons.items():
            binding = getattr(self.config.hotkeys, name)
            full = binding.label if binding else "未设置"
            key.setText(key.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, 86))
            key.setToolTip(full + "\n" + HOTKEY_HELP[name])
            key.setEnabled(self.pending_capture is None)
        for name, check in self.hotkey_checks.items():
            binding = (
                self.config.hotkeys.switch_set
                if name == "switch_enabled"
                else self.config.hotkeys.reset_cycle
            )
            with QSignalBlocker(check):
                check.setChecked(getattr(self.config.hotkeys, name))
            check.setEnabled(binding is not None and self.pending_capture is None)
        self.force_refresh = False

    def _render(self, snapshot, backend, note):
        """将最新快照同步到已有控件，并更新小窗与循环编辑器。"""
        previous = self.config
        initial = self.snapshot is None
        if self.snapshot is not None and self.snapshot.running != snapshot.running:
            play_sound("on" if snapshot.running else "off", snapshot.config.volume)
        self.snapshot, self.config = snapshot, snapshot.config
        self.rendered_backend, self.rendered_backend_note = backend, note
        self.rendered_capture = self.pending_capture is not None
        self._sync_settings(previous, initial)
        interception = "Interception" in backend
        unavailable = not (interception or "SendInput" in backend)
        backend_label = (
            "驱动 Interception"
            if interception
            else "免驱 SendInput"
            if not unavailable
            else "输入不可用"
        )
        self.backend_badge.setText(backend_label)
        self.backend_badge.setToolTip(backend + ("\n" + note if note else ""))
        self.backend_status.setText(backend + ("\n" + note if note else ""))
        self.driver_button.setVisible(not interception)
        group = self.config.group_labels[self.config.active_set]
        self.active_group.set_full_text(group)
        group_state = (self.config.group_labels, self.config.active_set)
        if self.group_state != group_state:
            set_combo(self.group_box, self.config.group_labels, self.config.active_set)
            self.group_state = group_state
        self.group_box.setToolTip(group)
        self.group_box.setEnabled(self.pending_capture is None)
        for action in self.group_buttons:
            action.setEnabled(self.pending_capture is None)
        key = (
            self.config.slots[self.config.selected_slot].binding.label
            if self.config.slots
            else "未添加按键"
        )
        tone = "info"
        if unavailable:
            status, tone = "输入不可用", "error"
        elif snapshot.armed or (snapshot.running and not snapshot.scope_allowed):
            status, tone = snapshot.status, "warning"
        elif snapshot.running and self.config.hold_repeat and not snapshot.held_keys:
            status, tone = "按住连发就绪", "warning"
        elif snapshot.running:
            status, tone = f"连发中 · {key}", "success"
        else:
            status = f"已就绪 · {key}" if self.config.slots else "已就绪"
        self.header_status.set_full_text(status)
        self.header_status.setProperty("tone", tone)
        refresh_style(self.header_status)
        toggle = self.config.hotkeys.toggle
        caption = "取消等待" if snapshot.armed else "停止连发" if snapshot.running else "启动连发"
        full_caption = caption + (f"（{toggle.label}）" if toggle else "")
        self.toggle_button.setText(
            self.toggle_button.fontMetrics().elidedText(
                full_caption, Qt.TextElideMode.ElideRight, max(260, self.width() - 60)
            )
        )
        self.toggle_button.setToolTip(full_caption)
        can_start = (
            any(slot.enabled for slot in self.config.slots)
            and not unavailable
            and self.pending_capture is None
        )
        self.toggle_button.setEnabled(snapshot.running or snapshot.armed or can_start)
        self.auxiliary_btn.setEnabled(
            self.config.hotkeys.auxiliary is not None
            and not unavailable
            and self.pending_capture is None
        )
        self.stop_button.setEnabled(
            snapshot.running
            or snapshot.armed
            or snapshot.recording
            or bool(snapshot.auxiliary_remaining)
        )
        self._update_auxiliary(snapshot)
        scope = self.config.scope_name
        self.scope_status.setText(
            f"仅在 {scope} 前台响应" if scope else "全局生效 · 对任意前台窗口有效"
        )
        self._render_keys(snapshot, unavailable)
        if self.mini is not None:
            self.mini.render(snapshot)
        if self.cycle_dialog is not None:
            self.cycle_dialog.render(snapshot)

    def _update_auxiliary(self, snapshot):
        remaining = snapshot.auxiliary_remaining
        self.auxiliary_btn.setText(f"额外连发：{remaining:.1f}s" if remaining else "额外按键连发")
        if self.mini is not None:
            self.mini.auxiliary.setToolTip(self.auxiliary_btn.text())

    def _needs_render(self, snapshot, backend, note):
        """判断可见状态是否变化；仅倒计时数值变化时使用局部更新。"""
        before = self.snapshot
        if before is None or self.force_refresh:
            return True
        return (
            self.rendered_capture != (self.pending_capture is not None)
            or backend != self.rendered_backend
            or note != self.rendered_backend_note
            or any(
                getattr(before, field) != getattr(snapshot, field)
                for field in (
                    "config",
                    "status",
                    "scope_allowed",
                    "running",
                    "armed",
                    "recording",
                    "recorded_steps",
                    "held_keys",
                )
            )
            or bool(before.auxiliary_remaining) != bool(snapshot.auxiliary_remaining)
        )

    def _poll(self):
        """在 GUI 线程消费通知并取最新快照，状态未变化时只更新额外连发倒计时。"""
        if self.closed:
            return
        self._read_notifications()
        self._read_driver_events()
        latest = None
        while True:
            try:
                latest = self.runtime.snapshots.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            snapshot, backend, note = latest
            if self._needs_render(snapshot, backend, note):
                self._render(snapshot, backend, note)
            else:
                self.snapshot = snapshot
                self._update_auxiliary(snapshot)

    def _read_notifications(self):
        """处理运行线程的录入结果、错误和窗口列表，统一释放已结束的捕获回调。"""
        for _ in range(100):
            try:
                kind, value = self.runtime.notifications.get_nowait()
            except queue.Empty:
                break
            if kind == "captured":
                callback, self.pending_capture = self.pending_capture, None
                if value is not None and callback is not None:
                    callback(value)
                self._show_notice(
                    "设置已取消" if value is None else f"已捕获：{value.label}",
                    "info" if value is None else "success",
                )
            elif kind == "capture_cancelled":
                self.pending_capture = None
            elif kind == "windows":
                text = self.scope_box.currentText()
                with QSignalBlocker(self.scope_box):
                    self.scope_box.clear()
                    self.scope_box.addItems(("所有程序", *value))
                    self.scope_box.setCurrentText(text)
            else:
                if kind == "capture_timeout":
                    self.pending_capture = None
                self.force_refresh = True
                self._show_notice(
                    value,
                    "success"
                    if kind == "reset_complete"
                    else "warning"
                    if kind == "capture_timeout"
                    else "error",
                )

    def _install_driver(self):
        """启动独立安装线程；进度和结果入队，工作线程不直接修改 Qt 控件。"""
        if self.driver_installing:
            return
        self._command("stop")
        self.driver_installing = True
        self.driver_button.setEnabled(False)
        self.driver_status.setText("正在准备下载驱动…")

        def worker():
            try:
                install_driver(lambda text: self.driver_events.put(("progress", text)))
            except Exception as error:
                self.driver_events.put(("error", str(error)))
            else:
                self.driver_events.put(("done", "驱动安装完成，请重启电脑后再使用 Interception。"))

        threading.Thread(target=worker, name="driver-setup", daemon=True).start()

    def _read_driver_events(self):
        """在 GUI 线程显示安装进度，收到完成或失败事件后恢复安装按钮。"""
        while True:
            try:
                kind, text = self.driver_events.get_nowait()
            except queue.Empty:
                break
            self.driver_status.setText(text)
            if kind != "progress":
                self.driver_installing = False
                self.driver_button.setEnabled(True)
                self._show_notice(text, "success" if kind == "done" else "error")

    def open_cycle(self):
        if self.cycle_dialog is None:
            self.cycle_dialog = CycleDialog(self)
        self.cycle_dialog.show()
        self.cycle_dialog.raise_()
        self.cycle_dialog.activateWindow()

    def open_mini(self):
        if self.snapshot is None:
            return
        if self.mini is None:
            self.mini = MiniWindow(self)
        self.mini.render(self.snapshot)
        self.mini.show()
        self.hide()

    def import_config(self):
        """读取并校验 JSON 后交给运行线程应用，由运行线程负责停止发送和保存。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 配置 (*.json)")
        if not path:
            return
        try:
            config = from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        self._command("apply_config", config)

    def export_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", "keyweave-config.json", "JSON 配置 (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(to_dict(self.config), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self._show_notice("配置已成功导出", "success")

    def _reset_settings(self):
        if (
            QMessageBox.question(
                self, "恢复默认设置", "清除自定义分组、按键、循环和快捷键，恢复所有默认设置？"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.pending_capture = None
        if self.cycle_dialog is not None:
            self.cycle_dialog.reject()
        self._command("reset")

    def closeEvent(self, event):
        """安装未结束或输入线程未退出时保留窗口，完成资源清理后关闭附属窗口。"""
        if self.driver_installing:
            self._show_notice("正在安装驱动，请等待完成或取消管理员授权后再退出。", "warning")
            event.ignore()
            return
        try:
            if self.runtime.is_alive():
                self.runtime.close()
        except RuntimeError as error:
            self._show_notice(str(error), "error")
            event.ignore()
            return
        self.closed = True
        self.timer.stop()
        if self.mini is not None:
            self.mini.close()
        if self.cycle_dialog is not None:
            self.cycle_dialog.reject()
        if self.runtime.shutdown_errors:
            QMessageBox.warning(self, "退出清理异常", "\n".join(self.runtime.shutdown_errors))
        event.accept()
