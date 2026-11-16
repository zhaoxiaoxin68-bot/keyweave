"""Win32 类型与函数声明，以及进程识别、单实例和窗口边框操作。"""

import ctypes as ct
import os
from pathlib import PureWindowsPath

UINT = ct.c_uint32
WORD = ct.c_uint16
LONG = ct.c_int32
HANDLE = ct.c_void_p
ULONG_PTR = ct.c_size_t
LRESULT = ct.c_ssize_t
CALLBACK = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)
HOOKPROC = CALLBACK(LRESULT, ct.c_int, ULONG_PTR, ct.c_ssize_t)
ENUMPROC = CALLBACK(ct.c_int32, HANDLE, ct.c_ssize_t)
INPUT_MARKER = 0x4B575631  # 只用于辨认本程序的事件，防止热键递归。


class Point(ct.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MouseInput(ct.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", UINT),
        ("dwFlags", UINT),
        ("time", UINT),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KeyboardInput(ct.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", UINT),
        ("time", UINT),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HardwareInput(ct.Structure):
    _fields_ = [("message", UINT), ("param_low", WORD), ("param_high", WORD)]


class InputUnion(ct.Union):
    _fields_ = [
        ("mouse", MouseInput),
        ("keyboard", KeyboardInput),
        ("hardware", HardwareInput),
    ]


class Input(ct.Structure):
    _fields_ = [("type", UINT), ("data", InputUnion)]


class KeyboardHookData(ct.Structure):
    _fields_ = [
        ("vkCode", UINT),
        ("scanCode", UINT),
        ("flags", UINT),
        ("time", UINT),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MouseHookData(ct.Structure):
    _fields_ = [
        ("point", Point),
        ("mouseData", UINT),
        ("flags", UINT),
        ("time", UINT),
        ("dwExtraInfo", ULONG_PTR),
    ]


class Message(ct.Structure):
    _fields_ = [
        ("hwnd", HANDLE),
        ("message", UINT),
        ("wParam", ULONG_PTR),
        ("lParam", ct.c_ssize_t),
        ("time", UINT),
        ("point", Point),
        ("private", UINT),
    ]


def declare(library, name: str, arguments: list, result) -> None:
    """为 ctypes 函数设置参数和返回类型，避免句柄被默认整数类型截断。"""
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = result


class WinAPI:
    """加载 Windows 系统库并声明调用签名；实例持有单实例互斥量句柄。"""

    def __init__(self):
        if os.name != "nt":
            raise OSError("按键输入仅支持 Windows")
        self.user32 = ct.WinDLL("user32", use_last_error=True)
        self.kernel32 = ct.WinDLL("kernel32", use_last_error=True)
        user = self.user32
        kernel = self.kernel32
        declare(user, "SendInput", [UINT, ct.POINTER(Input), ct.c_int], UINT)
        declare(user, "MapVirtualKeyW", [UINT, UINT], UINT)
        declare(user, "GetForegroundWindow", [], HANDLE)
        declare(user, "GetWindowThreadProcessId", [HANDLE, ct.POINTER(UINT)], UINT)
        declare(user, "GetAsyncKeyState", [ct.c_int], ct.c_int16)
        declare(user, "IsWindowVisible", [HANDLE], ct.c_int32)
        declare(user, "GetWindowTextW", [HANDLE, ct.c_wchar_p, ct.c_int], ct.c_int)
        declare(user, "EnumWindows", [ENUMPROC, ct.c_ssize_t], ct.c_int32)
        declare(user, "SetWindowsHookExW", [ct.c_int, HOOKPROC, HANDLE, UINT], HANDLE)
        declare(user, "CallNextHookEx", [HANDLE, ct.c_int, ULONG_PTR, ct.c_ssize_t], LRESULT)
        declare(user, "UnhookWindowsHookEx", [HANDLE], ct.c_int32)
        declare(user, "GetMessageW", [ct.POINTER(Message), HANDLE, UINT, UINT], ct.c_int32)
        declare(
            user,
            "PeekMessageW",
            [ct.POINTER(Message), HANDLE, UINT, UINT, UINT],
            ct.c_int32,
        )
        declare(
            user,
            "PostThreadMessageW",
            [UINT, UINT, ULONG_PTR, ct.c_ssize_t],
            ct.c_int32,
        )
        declare(kernel, "GetCurrentThreadId", [], UINT)
        declare(kernel, "GetModuleHandleW", [ct.c_wchar_p], HANDLE)
        declare(kernel, "CreateMutexW", [ct.c_void_p, ct.c_int32, ct.c_wchar_p], HANDLE)
        declare(kernel, "OpenProcess", [UINT, ct.c_int32, UINT], HANDLE)
        declare(kernel, "CloseHandle", [HANDLE], ct.c_int32)
        declare(
            kernel,
            "QueryFullProcessImageNameW",
            [HANDLE, UINT, ct.c_wchar_p, ct.POINTER(UINT)],
            ct.c_int32,
        )

        self.instance_handle = None

    def acquire_instance(self) -> None:
        """申请当前会话的命名互斥量；已有实例时关闭新句柄并报错。"""
        handle = self.kernel32.CreateMutexW(None, False, "Local\\KeyWeave")
        if not handle:
            raise ct.WinError(ct.get_last_error())
        if ct.get_last_error() == 183:
            self.kernel32.CloseHandle(handle)
            raise RuntimeError("键序已在运行，请先关闭现有窗口")
        self.instance_handle = handle

    def release_instance(self) -> None:
        if self.instance_handle:
            self.kernel32.CloseHandle(self.instance_handle)
            self.instance_handle = None

    def process_name(self, window) -> str:
        """查询窗口所属进程的文件名；进程不可访问时返回空字符串。"""
        process_id = UINT()
        self.user32.GetWindowThreadProcessId(window, ct.byref(process_id))
        handle = self.kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return ""
        try:
            buffer = ct.create_unicode_buffer(32768)
            size = UINT(len(buffer))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ct.byref(size)):
                return PureWindowsPath(buffer.value).name
            return ""
        finally:
            self.kernel32.CloseHandle(handle)

    def foreground_name(self) -> str:
        return self.process_name(self.user32.GetForegroundWindow())

    def window_names(self) -> list[str]:
        """收集有标题的可见窗口所属进程，返回去重并排序后的文件名。"""
        names = set()

        @ENUMPROC
        def visit(window, _):
            if self.user32.IsWindowVisible(window):
                title = ct.create_unicode_buffer(512)
                self.user32.GetWindowTextW(window, title, len(title))
                name = self.process_name(window) if title.value else ""
                if name:
                    names.add(name)
            return 1

        if not self.user32.EnumWindows(visit, 0):
            raise ct.WinError(ct.get_last_error())
        return sorted(names, key=str.casefold)


def scope_matches(target: str, foreground: str) -> bool:
    """忽略路径和大小写比较进程文件名；目标为空时允许所有前台程序。"""
    if not target:
        return True
    target = PureWindowsPath(target).name.casefold()
    foreground = PureWindowsPath(foreground).name.casefold()
    return target == foreground


def configure_window_frame(window_id: int) -> None:
    """保留系统最小化/任务栏行为，移除重复标题栏，并请求 Windows 11 圆角。"""
    user = ct.WinDLL("user32", use_last_error=True)
    declare(user, "GetParent", [HANDLE], HANDLE)
    declare(user, "GetWindowLongW", [HANDLE, ct.c_int], LONG)
    declare(user, "SetWindowLongW", [HANDLE, ct.c_int, LONG], LONG)
    declare(user, "SetWindowPos", [HANDLE, HANDLE, ct.c_int, ct.c_int, ct.c_int, ct.c_int, UINT], ct.c_int)
    handle = user.GetParent(window_id) or window_id
    style = user.GetWindowLongW(handle, -16)
    # WS_CAPTION、WS_THICKFRAME、WS_MAXIMIZEBOX；保留 WS_SYSMENU/WS_MINIMIZEBOX。
    style &= ~(0x00C00000 | 0x00040000 | 0x00010000)
    ct.set_last_error(0)
    previous = user.SetWindowLongW(handle, -16, style)
    if not previous and ct.get_last_error():
        raise ct.WinError(ct.get_last_error())
    if not user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0037):
        raise ct.WinError(ct.get_last_error())
    try:
        dwm = ct.WinDLL("dwmapi", use_last_error=True)
        declare(dwm, "DwmSetWindowAttribute", [HANDLE, UINT, ct.c_void_p, UINT], LONG)
        preference = ct.c_int(2)  # DWMWCP_ROUND；旧系统忽略不支持的属性。
        dwm.DwmSetWindowAttribute(handle, 33, ct.byref(preference), ct.sizeof(preference))
    except OSError:
        pass
