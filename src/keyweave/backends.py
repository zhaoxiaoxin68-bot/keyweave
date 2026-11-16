"""Windows 输入发送后端：将虚拟键码转换为 SendInput 或 Interception 事件。"""

import ctypes as ct
from pathlib import Path

from .models import MOUSE_KEYS
from .windows import INPUT_MARKER, Input, KeyboardInput, MouseInput, WinAPI, declare


class InterceptionKeyStroke(ct.Structure):
    _fields_ = [
        ("code", ct.c_uint16),
        ("state", ct.c_uint16),
        ("information", ct.c_uint32),
    ]


class InterceptionMouseStroke(ct.Structure):
    _fields_ = [
        ("state", ct.c_uint16),
        ("flags", ct.c_uint16),
        ("rolling", ct.c_int16),
        ("x", ct.c_int32),
        ("y", ct.c_int32),
        ("information", ct.c_uint32),
    ]


class SendInputBackend:
    """使用系统 SendInput 发送键鼠事件，并为本程序的事件附加识别标记。"""

    name = "SendInput（扫描码）"

    def __init__(self, api: WinAPI):
        self.api = api

    def key_down(self, vk: int) -> None:
        self._send(vk, False)

    def key_up(self, vk: int) -> None:
        self._send(vk, True)

    def _send(self, vk: int, up: bool) -> None:
        event = Input()
        if vk in MOUSE_KEYS:
            event.type = 0
            flags = (0x40 if up else 0x20) if vk == 4 else (0x100 if up else 0x80)
            button = 0 if vk == 4 else vk - 4
            event.data.mouse = MouseInput(0, 0, button, flags, 0, INPUT_MARKER)
        else:
            event.type = 1
            scan = self.api.user32.MapVirtualKeyW(vk, 4)
            flags = (0x8 if scan else 0) | (0x2 if up else 0)
            if scan >> 8 in (0xE0, 0xE1):
                flags |= 0x1
            event.data.keyboard = KeyboardInput(
                0 if scan else vk, scan & 0xFF, flags, 0, INPUT_MARKER
            )
        if self.api.user32.SendInput(1, ct.byref(event), ct.sizeof(event)) != 1:
            raise OSError("SendInput 未成功发送，可能受权限或目标程序限制")

    def close(self) -> None:
        pass


class InterceptionBackend:
    """持有驱动上下文；初始化时通过硬件 ID 查询确认设备可用。"""

    def __init__(self, api: WinAPI, dll_path: Path, keyboard_device: int = 0):
        self.api = api
        self.context = None
        self.library = ct.CDLL(str(dll_path.resolve()))
        library = self.library
        declare(library, "interception_create_context", [], ct.c_void_p)
        declare(library, "interception_destroy_context", [ct.c_void_p], None)
        declare(
            library,
            "interception_get_hardware_id",
            [ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_uint32],
            ct.c_uint32,
        )
        declare(
            library,
            "interception_send",
            [ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_uint32],
            ct.c_int,
        )
        self.context = library.interception_create_context()
        if not self.context:
            raise OSError("Interception 上下文创建失败")
        try:
            keyboards = self._connected_devices(range(1, 11))
            mice = self._connected_devices(range(11, 21))
            self.keyboard = keyboard_device or next(iter(keyboards), 0)
            if self.keyboard not in keyboards:
                raise OSError("没有可用的驱动键盘设备，请检查驱动或设备号")
            self.mouse = next(iter(mice), 0)
        except Exception:
            self.close()
            raise
        self.name = f"Interception（键盘{self.keyboard} / 鼠标{self.mouse or '无'}）"

    def _connected_devices(self, devices: range) -> list[int]:
        """返回驱动能查询到硬件 ID 的设备号，不以 DLL 文件存在作为可用依据。"""
        buffer = ct.create_string_buffer(4096)
        return [
            device
            for device in devices
            if self.library.interception_get_hardware_id(
                self.context, device, buffer, ct.sizeof(buffer)
            )
        ]

    def key_down(self, vk: int) -> None:
        self._send(vk, False)

    def key_up(self, vk: int) -> None:
        self._send(vk, True)

    def _send(self, vk: int, up: bool) -> None:
        if not self.context:
            raise OSError("驱动上下文已关闭")
        if vk in MOUSE_KEYS:
            if not self.mouse:
                raise OSError("Interception 没有可用鼠标设备")
            down_state, up_state = {
                4: (0x10, 0x20),
                5: (0x40, 0x80),
                6: (0x100, 0x200),
            }[vk]
            stroke = InterceptionMouseStroke(
                up_state if up else down_state, 0, 0, 0, 0, INPUT_MARKER
            )
            device = self.mouse
        else:
            scan = self.api.user32.MapVirtualKeyW(vk, 4)
            if not scan:
                raise OSError(f"键码 {vk} 无法映射到扫描码")
            prefix = scan >> 8
            state = (1 if up else 0) | {0xE0: 2, 0xE1: 4}.get(prefix, 0)
            stroke = InterceptionKeyStroke(scan & 0xFF, state, INPUT_MARKER)
            device = self.keyboard
        sent = self.library.interception_send(self.context, device, ct.byref(stroke), 1)
        if sent != 1:
            raise OSError(f"Interception 未成功发送到设备 {device}")

    def close(self) -> None:
        """销毁驱动上下文；重复调用不会再次释放。"""
        if self.context:
            self.library.interception_destroy_context(self.context)
            self.context = None


class UnavailableBackend:
    """后端初始化失败时保留界面可用，任何按键发送均报告原始失败原因。"""

    name = "输入后端不可用"

    def __init__(self, reason: str):
        self.reason = reason

    def key_down(self, vk: int) -> None:
        raise OSError(self.reason)

    def key_up(self, vk: int) -> None:
        raise OSError(self.reason)

    def close(self) -> None:
        pass


def create_backend(api: WinAPI, mode: str, dll_path: Path, device: int = 0):
    """返回后端及状态说明；auto 模式允许回退，显式选择 interception 时将错误交给调用方。"""
    if mode == "sendinput":
        return SendInputBackend(api), ""
    try:
        return InterceptionBackend(api, dll_path, device), ""
    except (OSError, AttributeError) as error:
        if mode == "interception":
            raise
        return SendInputBackend(api), f"驱动不可用，已使用 SendInput：{error}"
