"""不可变的键位、分组和应用配置，以及配置完整性校验。"""

from dataclasses import dataclass, field
from enum import IntFlag


class Modifier(IntFlag):
    NONE = 0
    ALT = 1
    CTRL = 2
    SHIFT = 4
    WIN = 8


MODIFIER_KEYS = (
    (Modifier.CTRL, 17),
    (Modifier.SHIFT, 16),
    (Modifier.ALT, 18),
    (Modifier.WIN, 91),
)
MOUSE_KEYS = frozenset((4, 5, 6))
KEY_NAMES = {
    4: "鼠标中键",
    5: "鼠标侧键1",
    6: "鼠标侧键2",
    8: "退格",
    9: "Tab",
    13: "回车",
    16: "Shift",
    17: "Ctrl",
    18: "Alt",
    27: "Esc",
    32: "空格",
    33: "PgUp",
    34: "PgDn",
    35: "End",
    36: "Home",
    37: "←",
    38: "↑",
    39: "→",
    40: "↓",
    45: "Insert",
    46: "Delete",
    91: "Win",
}


def key_name(vk: int) -> str:
    if vk in KEY_NAMES:
        return KEY_NAMES[vk]
    if 48 <= vk <= 90:
        return chr(vk)
    if 112 <= vk <= 135:
        return f"F{vk - 111}"
    return f"VK{vk:02X}"


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """一个虚拟键码及其修饰键；鼠标中键和侧键也使用虚拟键码表示。"""

    vk: int
    modifiers: Modifier = Modifier.NONE

    def __post_init__(self) -> None:
        if type(self.vk) is not int or not 1 <= self.vk <= 254:
            raise ValueError("键码必须在 1–254 之间")
        if not isinstance(self.modifiers, (Modifier, int)) or isinstance(self.modifiers, bool):
            raise ValueError("修饰键必须为整数标记")
        if int(self.modifiers) & ~15:
            raise ValueError("修饰键标记无效")

    @property
    def keys(self) -> tuple[int, ...]:
        """按修饰键在前、主键在后的顺序返回键码，排除重复的主键。"""
        modifiers = tuple(
            vk for flag, vk in MODIFIER_KEYS if self.modifiers & flag and vk != self.vk
        )
        return (*modifiers, self.vk)

    @property
    def label(self) -> str:
        return "+".join(key_name(vk) for vk in self.keys)


@dataclass(frozen=True, slots=True)
class Slot:
    binding: KeyBinding
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class CycleStep:
    """循环步骤使用从零开始的索引，显示时转为从一开始的“组号.按键序号”。"""

    set_index: int
    slot_index: int

    def __post_init__(self) -> None:
        if any(type(index) is not int or index < 0 for index in (self.set_index, self.slot_index)):
            raise ValueError("循环步骤的组号和按键序号必须为非负整数")

    @property
    def label(self) -> str:
        return f"{self.set_index + 1}.{self.slot_index + 1}"


@dataclass(frozen=True, slots=True)
class Hotkeys:
    toggle: KeyBinding | None = field(default_factory=lambda: KeyBinding(119))
    cycle: KeyBinding | None = field(default_factory=lambda: KeyBinding(120))
    switch_set: KeyBinding | None = None
    reset_cycle: KeyBinding | None = None
    auxiliary: KeyBinding | None = None
    switch_enabled: bool = False
    reset_enabled: bool = False

    def enabled_bindings(self) -> tuple[tuple[str, KeyBinding | None], ...]:
        """按处理优先级返回命令与键位；未设置或已关闭的键位为 None。"""
        return (
            ("toggle_running", self.toggle),
            ("next_slot", self.cycle),
            ("switch_set", self.switch_set if self.switch_enabled else None),
            ("reset_cycle", self.reset_cycle if self.reset_enabled else None),
            ("toggle_auxiliary", self.auxiliary),
        )


def default_sets() -> tuple[tuple[Slot, ...], ...]:
    return ((Slot(KeyBinding(49)),),)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """持久化配置；运行、等待启动和实际按住的键由 Controller 单独维护。"""

    sets: tuple[tuple[Slot, ...], ...] = field(default_factory=default_sets)
    group_names: tuple[str, ...] = ()
    active_set: int = 0
    selected_slot: int = 0
    interval_ms: int = 200
    hold_ms: int = 30
    hold_mode: bool = False
    sync_mode: bool = False
    wait_start: bool = False
    quick_switch: bool = True
    hold_repeat: bool = False
    scope_name: str = ""
    cycle_steps: tuple[CycleStep, ...] = ()
    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    backend: str = "auto"
    keyboard_device: int = 0
    volume: int = 25
    mini_alpha: float = 1.0

    @property
    def slots(self) -> tuple[Slot, ...]:
        return self.sets[self.active_set]

    @property
    def group_labels(self) -> tuple[str, ...]:
        return self.group_names or tuple(f"分组 {index + 1}" for index in range(len(self.sets)))

    def validate(self) -> None:
        """检查字段类型、取值范围、模式互斥和引用关系，无效时抛出 ValueError。"""
        for name in (
            "active_set",
            "selected_slot",
            "interval_ms",
            "hold_ms",
            "keyboard_device",
            "volume",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须为整数")
        for name in ("hold_mode", "sync_mode", "wait_start", "quick_switch", "hold_repeat"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须为布尔值")
        if not isinstance(self.sets, tuple) or not self.sets:
            raise ValueError("至少保留一个分组")
        if any(not isinstance(slots, tuple) for slots in self.sets):
            raise ValueError("分组按键格式错误")
        if not 0 <= self.active_set < len(self.sets):
            raise ValueError("当前分组不存在")
        if not 0 <= self.selected_slot < max(1, len(self.slots)):
            raise ValueError("当前按键不存在")
        if not isinstance(self.group_names, tuple):
            raise ValueError("分组名称格式错误")
        if self.group_names and (
            len(self.group_names) != len(self.sets)
            or any(not isinstance(name, str) or not name.strip() for name in self.group_names)
        ):
            raise ValueError("每个分组必须有一个非空名称")
        for slots in self.sets:
            for slot in slots:
                if not isinstance(slot, Slot) or not isinstance(slot.binding, KeyBinding):
                    raise ValueError("按键配置格式错误")
                if type(slot.enabled) is not bool:
                    raise ValueError("按键启用状态必须为布尔值")
        if not isinstance(self.hotkeys, Hotkeys):
            raise ValueError("快捷键配置格式错误")
        for flag in (self.hotkeys.switch_enabled, self.hotkeys.reset_enabled):
            if type(flag) is not bool:
                raise ValueError("快捷键启用状态必须为布尔值")
        for name in ("toggle", "cycle", "switch_set", "reset_cycle", "auxiliary"):
            binding = getattr(self.hotkeys, name)
            if binding is not None and not isinstance(binding, KeyBinding):
                raise ValueError("快捷键键位格式错误")
        if not isinstance(self.scope_name, str):
            raise ValueError("响应程序必须为文件名字符串")
        if any(not isinstance(step, CycleStep) for step in self.cycle_steps):
            raise ValueError("循环步骤格式错误")
        for step in self.cycle_steps:
            if step.set_index >= len(self.sets) or step.slot_index >= len(
                self.sets[step.set_index]
            ):
                raise ValueError(f"循环步骤 {step.label} 指向不存在的分组或按键")
        if not 5 <= self.interval_ms <= 2000:
            raise ValueError("连发间隔必须为 5–2000ms")
        if not 1 <= self.hold_ms <= 500:
            raise ValueError("按住时长必须为 1–500ms")
        if self.hold_mode and self.sync_mode:
            raise ValueError("持续按住选中键与依次连发不能同时开启")
        if self.hold_mode and self.hold_repeat:
            raise ValueError("持续按住选中键与按住连发不能同时开启")
        period_ms = 50 if self.sync_mode else self.interval_ms
        if not self.hold_mode and self.hold_ms > period_ms:
            raise ValueError(f"按住时长不能超过发送间隔 {period_ms}ms")
        if len(self.cycle_steps) > 60:
            raise ValueError("循环顺序最多60步")
        if self.backend not in ("auto", "sendinput", "interception"):
            raise ValueError("未知输入后端")
        if not 0 <= self.keyboard_device <= 10:
            raise ValueError("键盘设备号必须为0（自动）或1–10")
        if not 0 <= self.volume <= 100 or self.mini_alpha not in (1.0, 0.85, 0.7, 0.55):
            raise ValueError("音量或透明度超出范围")
        bindings = [binding for _, binding in self.hotkeys.enabled_bindings() if binding]
        if len(bindings) != len(set(bindings)):
            raise ValueError("启用的快捷键不能重复")
