"""按键发送调度：按调用方提供的单调时钟时间处理按下、释放和下一次发送。"""

from dataclasses import dataclass
from typing import Protocol

from .models import KeyBinding


class InputBackend(Protocol):
    """调度器使用的输入接口；发送失败由具体后端抛出异常。"""

    name: str

    def key_down(self, vk: int) -> None: ...
    def key_up(self, vk: int) -> None: ...
    def close(self) -> None: ...


class BackendError(OSError):
    """输入发送或释放失败，与配置保存等其他 I/O 错误区分。"""


class HeldKeys:
    """按来源引用计数，主键和辅助连发共享 Ctrl 等修饰键时不会提前释放。"""

    def __init__(self, backend: InputBackend):
        self.backend = backend
        self.owners: dict[str, tuple[int, ...]] = {}
        self.counts: dict[int, int] = {}

    def _send(self, action: str, vk: int) -> None:
        try:
            getattr(self.backend, action)(vk)
        except Exception as error:
            raise BackendError(str(error)) from error

    def press(self, owner: str, binding: KeyBinding) -> None:
        """记录来源已成功按下的键；共享键只发送一次按下，部分失败仍保留释放记录。"""
        if owner in self.owners:
            raise RuntimeError(f"按键来源尚未释放：{owner}")
        self.owners[owner] = ()
        for vk in binding.keys:
            if not self.counts.get(vk):
                self._send("key_down", vk)
            self.counts[vk] = self.counts.get(vk, 0) + 1
            self.owners[owner] += (vk,)

    def release(self, owner: str) -> None:
        """逆序释放该来源的按键；共享键留给其他来源，失败的释放留待 release_all 重试。"""
        keys = self.owners.pop(owner, ())
        first_error = None
        for vk in reversed(keys):
            count = self.counts[vk] - 1
            if count:
                self.counts[vk] = count
                continue
            try:
                self._send("key_up", vk)
                del self.counts[vk]
            except Exception as error:
                # 保留未释放键，release_all 还可重试，不能假装已经松开。
                first_error = first_error or error
        if first_error:
            raise first_error

    def release_all(self) -> None:
        """尝试释放全部已记录的键，继续处理其余键后再抛出首个错误。"""
        first_error = None
        for vk in reversed(tuple(self.counts)):
            try:
                self._send("key_up", vk)
                del self.counts[vk]
            except Exception as error:
                first_error = first_error or error
        self.owners.clear()
        if first_error:
            raise first_error


@dataclass(frozen=True, slots=True)
class InputPlan:
    """一次调度配置；interval、hold 和 initial_delay 的单位均为秒。"""

    main: tuple[KeyBinding, ...] = ()
    interval: float = 0.2
    hold: float = 0.03
    long_hold: bool = False
    extra: KeyBinding | None = None
    initial_delay: float = 0.0

    @property
    def main_signature(self) -> tuple:
        return self.main, self.interval, self.hold, self.long_hold, self.initial_delay


class Scheduler:
    """在同一线程内推进主连发和额外连发，通过截止时间安排按键释放。"""

    def __init__(self, backend: InputBackend):
        self.keys = HeldKeys(backend)
        self.plan = InputPlan()
        self.release_at: dict[str, float] = {}
        self.next_main = 0.0
        self.next_extra = 0.0
        self.main_index = 0

    def configure(self, plan: InputPlan, now: float) -> None:
        """仅重置发生变化的发送通道，避免无关设置打断另一通道的节奏。"""
        if plan.main_signature != self.plan.main_signature:
            self._release("main")
            self._release("hold")
            self.next_main = now + plan.initial_delay
            self.main_index = 0
        if plan.extra != self.plan.extra:
            self._release("extra")
            self.next_extra = now
        self.plan = plan

    def tick(self, now: float) -> None:
        """先处理到期释放，再推进主连发和额外连发；不会等待下一次发送时间。"""
        for owner, deadline in tuple(self.release_at.items()):
            if now >= deadline:
                self._release(owner)
        self._tick_main(now)
        self._tick_extra(now)

    def _tick_main(self, now: float) -> None:
        if not self.plan.main:
            return
        if self.plan.long_hold:
            if "hold" not in self.keys.owners:
                self.keys.press("hold", self.plan.main[0])
            return
        if now < self.next_main or "main" in self.keys.owners:
            return
        binding = self.plan.main[self.main_index % len(self.plan.main)]
        self._pulse("main", binding, now, self.plan.hold)
        self.main_index += 1
        # 线程延迟后不追赶积压事件，避免短时间突发连发。
        self.next_main = now + self.plan.interval

    def _tick_extra(self, now: float) -> None:
        if self.plan.extra is None or now < self.next_extra:
            return
        if "extra" in self.keys.owners:
            return
        self._pulse("extra", self.plan.extra, now, 0.02)
        self.next_extra = now + 0.1

    def _pulse(self, owner: str, binding: KeyBinding, now: float, duration: float) -> None:
        self.keys.press(owner, binding)
        self.release_at[owner] = now + duration

    def _release(self, owner: str) -> None:
        self.release_at.pop(owner, None)
        self.keys.release(owner)

    def next_deadline(self, now: float) -> float:
        """返回距最近调度时刻的秒数，供运行线程决定队列等待时间；最小为零。"""
        deadlines = list(self.release_at.values())
        if self.plan.main and not self.plan.long_hold:
            deadlines.append(self.next_main)
        if self.plan.extra:
            deadlines.append(self.next_extra)
        return max(0.0, min(deadlines, default=now + 0.02) - now)

    def stop(self) -> None:
        """清空调度计划并尝试释放全部按键，释放失败仍向调用方报错。"""
        self.plan = InputPlan()
        self.release_at.clear()
        self.keys.release_all()
