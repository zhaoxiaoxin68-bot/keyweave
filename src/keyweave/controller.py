"""连发与选键状态机：处理业务命令和物理输入，生成调度计划。"""

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .engine import InputPlan, Scheduler
from .models import AppConfig, CycleStep, KeyBinding, Modifier, Slot


@dataclass(frozen=True, slots=True)
class Snapshot:
    """供界面读取的不可变状态，包含配置、运行状态和录制结果。"""

    config: AppConfig
    status: str
    running: bool
    armed: bool
    scope_allowed: bool
    auxiliary_remaining: float
    recording: bool
    recorded_steps: tuple[CycleStep, ...]
    held_keys: tuple[KeyBinding, ...] = ()


class Controller:
    """由 Runtime 线程调用，统一维护运行状态、配置和循环位置。"""

    def __init__(
        self,
        config: AppConfig,
        scheduler: Scheduler,
        save: Callable[[AppConfig], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        config.validate()
        self.config = config
        self.scheduler = scheduler
        self.save = save
        self.clock = clock
        self.running = False
        self.armed = False
        self.scope_allowed = False
        self.auxiliary_until: float | None = None
        self.cycle_position = -1
        self.recorded_steps: list[CycleStep] = []
        self.recording = False
        self.physical_held: set[KeyBinding] = set()

    def apply_config(self, config: AppConfig) -> None:
        """校验并保存配置后更新状态；保存失败时保留原配置。"""
        config.validate()
        if config.slots and not config.slots[config.selected_slot].enabled:
            first = next((i for i, slot in enumerate(config.slots) if slot.enabled), 0)
            config = replace(config, selected_slot=first)
        self.save(config)
        if config.cycle_steps != self.config.cycle_steps:
            self.cycle_position = -1
        self.config = config
        if config.hotkeys.auxiliary is None:
            self.auxiliary_until = None
        if not any(slot.enabled for slot in config.slots):
            self.running = self.armed = False
        self._configure()

    def set_options(self, **values) -> None:
        self.apply_config(replace(self.config, **values))

    def set_slot(self, index: int, binding: KeyBinding) -> None:
        self._slot_index(index)
        slots = list(self.config.slots)
        slots[index] = replace(slots[index], binding=binding)
        self._replace_slots(tuple(slots))

    def toggle_slot(self, index: int) -> None:
        self._slot_index(index)
        slots = list(self.config.slots)
        slots[index] = replace(slots[index], enabled=not slots[index].enabled)
        self._replace_slots(tuple(slots))

    def _replace_slots(self, slots: tuple) -> None:
        sets = list(self.config.sets)
        sets[self.config.active_set] = slots
        self.apply_config(replace(self.config, sets=tuple(sets)))

    def _group_index(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self.config.sets):
            raise ValueError("该分组不存在")

    def _slot_index(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self.config.slots):
            raise ValueError("该按键不存在")

    def _apply_structure(self, config: AppConfig) -> None:
        """增删分组或按键时先停止发送，再保存结构并清空循环录制状态。"""
        config.validate()
        self.stop()
        self.apply_config(config)
        self.cycle_position = -1
        self.recorded_steps.clear()

    def add_group(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("分组名称不能为空")
        self._apply_structure(
            replace(
                self.config,
                sets=(*self.config.sets, ()),
                group_names=(*self.config.group_labels, name.strip()),
                active_set=len(self.config.sets),
                selected_slot=0,
            )
        )

    def rename_group(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("分组名称不能为空")
        names = list(self.config.group_labels)
        names[self.config.active_set] = name.strip()
        self.set_options(group_names=tuple(names))

    def remove_group(self) -> None:
        """删除当前分组，移除指向它的循环步骤，并修正后续分组索引。"""
        if len(self.config.sets) == 1:
            raise ValueError("至少保留一个分组")
        removed = self.config.active_set
        sets = self.config.sets[:removed] + self.config.sets[removed + 1 :]
        names = self.config.group_labels
        active = min(removed, len(sets) - 1)
        steps = tuple(
            CycleStep(step.set_index - (step.set_index > removed), step.slot_index)
            for step in self.config.cycle_steps
            if step.set_index != removed
        )
        self._apply_structure(
            replace(
                self.config,
                sets=sets,
                group_names=names[:removed] + names[removed + 1 :],
                active_set=active,
                selected_slot=0,
                cycle_steps=steps,
            )
        )

    def add_slot(self, binding: KeyBinding) -> None:
        slots = (*self.config.slots, Slot(binding))
        sets = list(self.config.sets)
        sets[self.config.active_set] = slots
        self._apply_structure(
            replace(
                self.config,
                sets=tuple(sets),
                selected_slot=len(slots) - 1,
            )
        )

    def remove_slot(self, index: int) -> None:
        """删除当前分组中的按键，同时修正选中位置和循环中的按键索引。"""
        self._slot_index(index)
        group = self.config.active_set
        slots = self.config.slots[:index] + self.config.slots[index + 1 :]
        sets = list(self.config.sets)
        sets[group] = slots
        steps = tuple(
            CycleStep(
                step.set_index,
                step.slot_index - (step.set_index == group and step.slot_index > index),
            )
            for step in self.config.cycle_steps
            if not (step.set_index == group and step.slot_index == index)
        )
        selected = self.config.selected_slot - (self.config.selected_slot > index)
        selected = max(0, min(selected, len(slots) - 1))
        self._apply_structure(
            replace(
                self.config,
                sets=tuple(sets),
                selected_slot=selected,
                cycle_steps=steps,
            )
        )

    def set_hotkey(self, name: str, binding: KeyBinding | None) -> None:
        """更新快捷键；切组和重置循环的独立开关随键位是否为空同步调整。"""
        if name not in ("toggle", "cycle", "switch_set", "reset_cycle", "auxiliary"):
            raise ValueError("未知快捷键")
        values = {name: binding}
        if name in ("switch_set", "reset_cycle"):
            flag = "switch_enabled" if name == "switch_set" else "reset_enabled"
            values[flag] = binding is not None
        self.set_options(hotkeys=replace(self.config.hotkeys, **values))
        if name == "auxiliary" and binding is None:
            self.auxiliary_until = None
            self._configure()

    def set_hotkey_enabled(self, name: str, enabled: bool) -> None:
        if name not in ("switch_enabled", "reset_enabled"):
            raise ValueError("该快捷键没有独立开关")
        self.set_options(hotkeys=replace(self.config.hotkeys, **{name: enabled}))

    def toggle_running(self) -> None:
        """切换停止、运行或等待按键启动状态，并清空此前记录的物理按键。"""
        if self.running or self.armed:
            self.running = self.armed = False
            self.physical_held.clear()
            self._configure()
            return
        if not any(slot.enabled for slot in self.config.slots):
            raise ValueError("当前分组没有启用的按键，请先添加或启用按键")
        self._select_enabled()
        self.armed = self.config.wait_start
        self.running = not self.armed
        self.physical_held.clear()
        self._configure()

    def select(self, index: int) -> None:
        self._slot_index(index)
        if not self.config.slots[index].enabled:
            raise ValueError("该按键未启用")
        self.set_options(selected_slot=index)

    def switch_set(self, index: int | None = None) -> None:
        target = (self.config.active_set + 1) % len(self.config.sets) if index is None else index
        self._group_index(target)
        selected = min(self.config.selected_slot, max(0, len(self.config.sets[target]) - 1))
        self.set_options(active_set=target, selected_slot=selected)

    def next_slot(self) -> None:
        """有自定义循环时按步骤切换，否则在当前分组内选择下一个启用的按键。"""
        if self.config.cycle_steps:
            self._advance_cycle()
            return
        for offset in range(1, len(self.config.slots) + 1):
            index = (self.config.selected_slot + offset) % len(self.config.slots)
            if self.config.slots[index].enabled:
                self.select(index)
                return
        raise ValueError("当前分组没有启用的按键")

    def _advance_cycle(self, start: int | None = None) -> None:
        """从指定位置向后查找启用步骤，保留重复步骤并允许跨组切换。"""
        steps = self.config.cycle_steps
        start = self.cycle_position if start is None else start
        for offset in range(1, len(steps) + 1):
            position = (start + offset) % len(steps)
            step = steps[position]
            if self.config.sets[step.set_index][step.slot_index].enabled:
                self.set_options(active_set=step.set_index, selected_slot=step.slot_index)
                self.cycle_position = position
                return
        raise ValueError("循环中的所有按键均已禁用")

    def reset_cycle(self) -> None:
        """回到首个可用循环步骤；未设循环时选择当前分组的首个启用按键。"""
        if self.config.cycle_steps:
            self._advance_cycle(start=-1)
            return
        self.cycle_position = -1
        first = next((i for i, slot in enumerate(self.config.slots) if slot.enabled), None)
        if first is not None:
            self.select(first)

    def set_cycle(self, steps: tuple[CycleStep, ...]) -> None:
        self.set_options(cycle_steps=steps)
        self.cycle_position = -1

    def toggle_auxiliary(self) -> None:
        """开启十秒额外连发，或提前取消；其状态独立于主连发开关。"""
        if self.auxiliary_until is not None:
            self.auxiliary_until = None
        elif self.config.hotkeys.auxiliary is None:
            raise ValueError("请先设置额外按键连发的快捷键")
        else:
            self.auxiliary_until = self.clock() + 10
        self._configure()

    def set_scope_allowed(self, allowed: bool) -> None:
        """根据前台窗口暂停或恢复发送，离开目标窗口时清空物理按住状态。"""
        if not allowed and self.physical_held:
            self.physical_held.clear()
        if allowed != self.scope_allowed:
            self.scope_allowed = allowed
            self._configure()

    def handle_input(self, binding: KeyBinding, pressed: bool = True) -> None:
        """依次处理录制、窗口范围、松键、快捷键和选键，避免一次输入触发多个动作。"""
        if self.recording:
            if pressed:
                self._record(binding)
            return
        if not self.scope_allowed:
            if not pressed and self.physical_held:
                to_remove = {b for b in self.physical_held if b.vk == binding.vk}
                if to_remove:
                    self.physical_held.difference_update(to_remove)
                    self._configure()
            return
        if not pressed:
            if self.config.hold_repeat and self.physical_held:
                to_remove = {b for b in self.physical_held if b.vk == binding.vk}
                if to_remove:
                    self.physical_held.difference_update(to_remove)
                    self._configure()
            return
        for action, hotkey in self.config.hotkeys.enabled_bindings():
            if binding == hotkey:
                getattr(self, action)()
                return
        if self.armed:
            self.armed, self.running = False, True
            self._configure()
            if not self.config.hold_repeat:
                return
        step = self._find_binding(binding)
        if step is not None:
            if self.config.quick_switch:
                if (step.set_index, step.slot_index) != (
                    self.config.active_set,
                    self.config.selected_slot,
                ):
                    self.set_options(active_set=step.set_index, selected_slot=step.slot_index)
            if self.config.hold_repeat:
                slot_binding = self.config.sets[step.set_index][step.slot_index].binding
                if slot_binding not in self.physical_held:
                    self.physical_held.add(slot_binding)
                    self._configure()

    def _find_binding(self, binding: KeyBinding) -> CycleStep | None:
        """优先匹配当前组的完整组合键，再匹配其他组；找不到时尝试不带修饰键的键位。"""
        order = (
            self.config.active_set,
            *(index for index in range(len(self.config.sets)) if index != self.config.active_set),
        )
        for set_index in order:
            for index, slot in enumerate(self.config.sets[set_index]):
                if slot.enabled and slot.binding == binding:
                    return CycleStep(set_index, index)
        if binding.modifiers != Modifier.NONE:
            plain = KeyBinding(binding.vk)
            for set_index in order:
                for index, slot in enumerate(self.config.sets[set_index]):
                    if slot.enabled and slot.binding == plain:
                        return CycleStep(set_index, index)
        return None

    def start_recording(self) -> None:
        """停止发送并清空旧记录，开始记录已配置按键对应的循环步骤。"""
        self.stop()
        self.recorded_steps.clear()
        self.recording = True

    def stop_recording(self) -> None:
        self.recording = False

    def _record(self, binding: KeyBinding) -> None:
        step = self._find_binding(binding)
        if step is None or len(self.recorded_steps) >= 60:
            return
        self.recorded_steps.append(step)

    def _select_enabled(self) -> None:
        if self.config.slots[self.config.selected_slot].enabled:
            return
        first = next((i for i, slot in enumerate(self.config.slots) if slot.enabled), 0)
        self.config = replace(self.config, selected_slot=first)

    def _configure(self) -> None:
        """将业务状态转为 InputPlan；毫秒在此换算为秒，窗口限制同时作用于两路连发。"""
        config = self.config
        main = ()
        initial_delay = 0.0
        if self.running and self.scope_allowed:
            if config.hold_repeat:
                held_active = tuple(
                    slot.binding
                    for slot in config.slots
                    if slot.enabled and slot.binding in self.physical_held
                )
                if config.sync_mode:
                    main = held_active
                elif held_active:
                    selected_binding = config.slots[config.selected_slot].binding
                    if selected_binding in held_active:
                        main = (selected_binding,)
                    else:
                        main = (held_active[0],)
                if main:
                    initial_delay = config.interval_ms / 1000
            else:
                main = tuple(slot.binding for slot in config.slots if slot.enabled)
                if not config.sync_mode:
                    main = (config.slots[config.selected_slot].binding,)
        extra = config.hotkeys.auxiliary if self.auxiliary_until and self.scope_allowed else None
        plan = InputPlan(
            main,
            0.05 if config.sync_mode else config.interval_ms / 1000,
            config.hold_ms / 1000,
            config.hold_mode,
            extra,
            initial_delay,
        )
        self.scheduler.configure(plan, self.clock())

    def tick(self, now: float) -> None:
        if self.auxiliary_until is not None and now >= self.auxiliary_until:
            self.auxiliary_until = None
            self._configure()
        self.scheduler.tick(now)

    def stop(self) -> None:
        """停止主连发、额外连发和录制，并释放调度器持有的按键。"""
        self.running = self.armed = self.recording = False
        self.auxiliary_until = None
        self.physical_held.clear()
        self.scheduler.stop()

    def snapshot(self) -> Snapshot:
        """生成界面状态；剩余时间取一位小数，不向界面暴露可变录制列表。"""
        status = "已停止"
        if self.armed:
            status = "等待按键"
        elif self.running:
            if not self.scope_allowed:
                status = "已暂停：不在目标窗口"
            elif self.config.hold_repeat:
                held = [
                    slot.binding.label
                    for slot in self.config.slots
                    if slot.enabled and slot.binding in self.physical_held
                ]
                if held:
                    status = f"连发中 · {'+'.join(held)}"
                else:
                    status = "按住连发就绪"
            else:
                status = "运行中"
        remaining = round(max(0.0, (self.auxiliary_until or 0.0) - self.clock()), 1)
        return Snapshot(
            self.config,
            status,
            self.running,
            self.armed,
            self.scope_allowed,
            remaining,
            self.recording,
            tuple(self.recorded_steps),
            tuple(self.physical_held),
        )
