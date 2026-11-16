"""Windows 键鼠钩子线程：过滤注入事件，将物理按下和松开事件写入队列。"""

import ctypes as ct
import logging
import queue
import threading
from dataclasses import dataclass

from .models import KeyBinding, Modifier
from .windows import (
    HOOKPROC,
    INPUT_MARKER,
    KeyboardHookData,
    Message,
    MouseHookData,
    WinAPI,
)

LOGGER = logging.getLogger(__name__)
NORMALIZED_KEYS = {160: 16, 161: 16, 162: 17, 163: 17, 164: 18, 165: 18, 92: 91}
MODIFIER_FLAGS = {
    16: Modifier.SHIFT,
    17: Modifier.CTRL,
    18: Modifier.ALT,
    91: Modifier.WIN,
}


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """标准化后的物理输入；pressed 为 False 表示松开。"""

    binding: KeyBinding
    pressed: bool = True


class InputListener(threading.Thread):
    """独立运行 Windows 消息循环；钩子回调只入队，业务处理留给 Runtime。"""

    def __init__(self, api: WinAPI, events: queue.Queue):
        super().__init__(name="input-hooks", daemon=False)
        self.api = api
        self.events = events
        self.ready = threading.Event()
        self.startup_error: Exception | None = None
        self.thread_id = 0
        self.pressed: set[int] = set()
        self.keyboard_callback = HOOKPROC(self._keyboard)
        self.mouse_callback = HOOKPROC(self._mouse)

    def run(self) -> None:
        """安装键鼠钩子并处理消息；启动失败通过 startup_error 和事件队列报告。"""
        user = self.api.user32
        self.thread_id = self.api.kernel32.GetCurrentThreadId()
        message = Message()
        user.PeekMessageW(ct.byref(message), None, 0, 0, 0)
        module = self.api.kernel32.GetModuleHandleW(None)
        keyboard = user.SetWindowsHookExW(13, self.keyboard_callback, module, 0)
        mouse = user.SetWindowsHookExW(14, self.mouse_callback, module, 0)
        try:
            if not keyboard or not mouse:
                raise ct.WinError(ct.get_last_error())
            self.pressed.update(vk for vk in NORMALIZED_KEYS if user.GetAsyncKeyState(vk) & 0x8000)
            self.ready.set()
            while True:
                result = user.GetMessageW(ct.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ct.WinError(ct.get_last_error())
        except Exception as error:
            self.startup_error = error
            self.events.put(error)
        finally:
            self.ready.set()
            for handle in (keyboard, mouse):
                if handle:
                    user.UnhookWindowsHookEx(handle)

    def stop(self) -> None:
        """向监听线程发送退出消息并等待卸载钩子；超时或发送失败时抛出异常。"""
        self.ready.wait(2)
        if self.is_alive() and self.thread_id:
            if not self.api.user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0):
                raise ct.WinError(ct.get_last_error())
        self.join(timeout=3)
        if self.is_alive():
            raise RuntimeError("键鼠钩子线程未结束")

    def _modifiers(self, exclude: int = 0) -> Modifier:
        result = Modifier.NONE
        for raw in self.pressed:
            vk = NORMALIZED_KEYS.get(raw, raw)
            if vk != exclude:
                result |= MODIFIER_FLAGS.get(vk, Modifier.NONE)
        return result

    def _keyboard(self, code: int, message: int, address: int) -> int:
        try:
            if code == 0:
                event = ct.cast(address, ct.POINTER(KeyboardHookData)).contents
                if event.dwExtraInfo != INPUT_MARKER and not event.flags & 0x10:
                    self._keyboard_event(event.vkCode, message)
        except Exception:
            LOGGER.exception("处理键盘钩子失败")
        return self.api.user32.CallNextHookEx(None, code, message, address)

    def _keyboard_event(self, raw_vk: int, message: int) -> None:
        """合并左右修饰键的键码，忽略系统重复按下，并为松键保留独立事件。"""
        if message in (0x101, 0x105):
            self.pressed.discard(raw_vk)
            vk = NORMALIZED_KEYS.get(raw_vk, raw_vk)
            self.events.put(KeyEvent(KeyBinding(vk, self._modifiers(exclude=vk)), False))
            return
        if message not in (0x100, 0x104) or raw_vk in self.pressed:
            return
        self.pressed.add(raw_vk)
        vk = NORMALIZED_KEYS.get(raw_vk, raw_vk)
        self.events.put(KeyEvent(KeyBinding(vk, self._modifiers(exclude=vk))))

    def _mouse(self, code: int, message: int, address: int) -> int:
        try:
            if code == 0 and message in (0x207, 0x208, 0x20B, 0x20C):
                event = ct.cast(address, ct.POINTER(MouseHookData)).contents
                if event.dwExtraInfo != INPUT_MARKER and not event.flags & 1:
                    vk = 4 if message in (0x207, 0x208) else 4 + (event.mouseData >> 16)
                    if vk in (4, 5, 6):
                        self.events.put(
                            KeyEvent(KeyBinding(vk, self._modifiers()), message in (0x207, 0x20B))
                        )
        except Exception:
            LOGGER.exception("处理鼠标钩子失败")
        return self.api.user32.CallNextHookEx(None, code, message, address)
