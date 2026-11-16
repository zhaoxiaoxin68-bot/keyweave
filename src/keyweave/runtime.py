"""输入运行线程：串行执行界面命令、消费物理输入并发布状态快照。"""

import logging
import queue
import threading
import time
from dataclasses import replace
from pathlib import Path

from .backends import UnavailableBackend, create_backend
from .config import ConfigStore
from .controller import Controller
from .engine import BackendError, Scheduler
from .hooks import MODIFIER_FLAGS, InputListener, KeyEvent
from .models import AppConfig, KeyBinding
from .windows import WinAPI, scope_matches

LOGGER = logging.getLogger(__name__)
COMMANDS = frozenset(
    {
        "set_options",
        "set_slot",
        "toggle_slot",
        "add_group",
        "rename_group",
        "remove_group",
        "add_slot",
        "remove_slot",
        "set_hotkey",
        "set_hotkey_enabled",
        "toggle_running",
        "select",
        "switch_set",
        "next_slot",
        "reset_cycle",
        "set_cycle",
        "toggle_auxiliary",
        "stop",
        "start_recording",
        "stop_recording",
        "apply_config",
        "reset",
    }
)


class Runtime(threading.Thread):
    """拥有 Win32 资源、控制器和后端；跨线程通信使用命令、通知及快照队列。"""

    def __init__(self, config: AppConfig, store: ConfigStore, dll_path: Path):
        super().__init__(name="key-runtime", daemon=False)
        self.initial_config = config
        self.store = store
        self.dll_path = dll_path
        self.commands: queue.Queue = queue.Queue()
        self.input_events: queue.Queue = queue.Queue()
        self.notifications: queue.Queue = queue.Queue()
        self.snapshots: queue.Queue = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.controller: Controller | None = None
        self.backend = None
        self.api: WinAPI | None = None
        self.listener: InputListener | None = None
        self.capture_until: float | None = None
        self.capture_modifier: KeyBinding | None = None
        self.backend_note = ""
        self.backend_error = ""
        self.shutdown_errors: list[str] = []

    def command(self, name: str, *args, **kwargs) -> None:
        """校验命令名并入队，不等待执行结果；后续执行错误通过 notifications 返回。"""
        if self.ready.is_set() and not self.is_alive():
            raise RuntimeError("输入服务已经退出，请重新启动程序")
        if name not in COMMANDS and name not in (
            "capture",
            "cancel_capture",
            "list_windows",
        ):
            raise ValueError(f"未知命令：{name}")
        self.commands.put((name, args, kwargs))

    def run(self) -> None:
        """建立单实例、后端和监听线程；退出或启动失败时统一清理资源。"""
        try:
            self.api = WinAPI()
            self.api.acquire_instance()
            try:
                self._open_backend(self.initial_config)
            except (OSError, AttributeError) as error:
                self.backend = UnavailableBackend(str(error))
                self.backend_error = str(error) or "输入后端异常"
                LOGGER.warning("后端初始化失败：%s", error)
            self.controller = Controller(
                self.initial_config, Scheduler(self.backend), self.store.save
            )
            self.listener = InputListener(self.api, self.input_events)
            self.listener.start()
            if not self.listener.ready.wait(3):
                raise RuntimeError("键鼠监听启动超时")
            if self.listener.startup_error:
                raise self.listener.startup_error
            self.ready.set()
            self._loop()
        except Exception as error:
            LOGGER.exception("运行时退出")
            self.notifications.put(("error", str(error)))
        finally:
            self._cleanup()
            self.capture_until = self.capture_modifier = None
            self.notifications.put(("capture_cancelled", ""))
            self.ready.set()

    def _loop(self) -> None:
        """交替处理输入、调度和界面命令，等待时长由调度截止时间决定。"""
        while not self.stopping.is_set():
            try:
                self._update_scope()
            except BackendError as error:
                self._handle_failure(error)
            self._drain_inputs()
            self._safe_tick()
            self._publish()
            delay = min(0.02, self.controller.scheduler.next_deadline(time.monotonic()))
            try:
                command = self.commands.get(timeout=max(0.001, delay))
            except queue.Empty:
                continue
            self._execute(*command)

    def _execute(self, name: str, args: tuple, kwargs: dict) -> None:
        """仅在运行线程执行命令；结构已变化时拒绝旧界面提交的操作。"""
        try:
            expected = kwargs.pop("_expected_config", None)
            current = self.controller.config
            if expected is not None and (
                current.active_set != expected.active_set
                or current.sets != expected.sets
                or current.group_labels != expected.group_labels
            ):
                raise ValueError("分组或按键已变化，请重新操作，未修改任何内容。")
            if self.backend_error and name in {"toggle_running", "toggle_auxiliary"}:
                raise ValueError("输入后端异常，请重新应用发送方式后再启动。")
            if name == "reset":
                self._reset()
                return
            if self.capture_until is not None and name in {
                "switch_set",
                "add_group",
                "remove_group",
                "rename_group",
                "add_slot",
                "remove_slot",
                "apply_config",
            }:
                raise ValueError("请先完成按键录入，或按 Esc 取消后再修改分组")
            if name == "capture":
                self.controller.stop()
                self.capture_until = time.monotonic() + 12
                self.capture_modifier = None
                return
            if name == "cancel_capture":
                self.capture_until = None
                self.capture_modifier = None
                return
            if name == "list_windows":
                self.notifications.put(("windows", self.api.window_names()))
                return
            if name == "apply_config":
                self.controller.stop()
            candidate = self._backend_candidate(name, args, kwargs)
            if candidate is not None:
                self._change_backend(candidate)
                return
            getattr(self.controller, name)(*args, **kwargs)
            if name == "start_recording":
                self.capture_until = None
        except ValueError as error:
            self.notifications.put(("error", str(error)))
            if name == "capture":
                self.notifications.put(("capture_cancelled", ""))
        except Exception as error:
            self._handle_failure(error)
            if name == "capture":
                self.notifications.put(("capture_cancelled", ""))

    def _reset(self) -> None:
        """停止发送并取消录入，恢复默认配置；后端参数改变时重新创建后端。"""
        self.controller.stop()
        self.capture_until = None
        self.capture_modifier = None
        self.notifications.put(("capture_cancelled", ""))
        config = AppConfig()
        current = self.controller.config
        if (current.backend, current.keyboard_device) != (config.backend, config.keyboard_device):
            self._change_backend(config)
        else:
            self.controller.apply_config(config)
        self.controller.recorded_steps.clear()
        self.controller.cycle_position = -1
        self.notifications.put(("reset_complete", "已恢复默认设置"))

    def _backend_candidate(self, name: str, args: tuple, kwargs: dict) -> AppConfig | None:
        """返回需要重建后端的配置；明确应用同一发送方式也会触发重建。"""
        current = self.controller.config
        candidate = args[0] if name == "apply_config" else current
        if name == "set_options":
            candidate = replace(current, **kwargs)
        before = (current.backend, current.keyboard_device)
        after = (candidate.backend, candidate.keyboard_device)
        explicit_apply = name == "set_options" and bool(
            {"backend", "keyboard_device"} & kwargs.keys()
        )
        return candidate if before != after or explicit_apply else None

    def _change_backend(self, config: AppConfig) -> None:
        """停止旧发送，创建候选后端并保存配置；提交失败时关闭候选后端。"""
        config.validate()
        self.controller.stop()
        old_backend = self.backend
        replacement, note = self._build_backend(config)
        try:
            self.controller.apply_config(config)
        except Exception:
            replacement.close()
            raise
        self.backend, self.backend_note = replacement, note
        self.controller.scheduler = Scheduler(replacement)
        old_backend.close()
        self.backend_error = ""

    def _open_backend(self, config: AppConfig) -> None:
        self.backend, self.backend_note = self._build_backend(config)

    def _build_backend(self, config: AppConfig):
        return create_backend(self.api, config.backend, self.dll_path, config.keyboard_device)

    def _update_scope(self) -> None:
        allowed = scope_matches(self.controller.config.scope_name, self.api.foreground_name())
        self.controller.set_scope_allowed(allowed)

    def _drain_inputs(self) -> None:
        """每轮最多消费一百个输入；录入优先，后端故障时仍允许录入和录制。"""
        now = time.monotonic()
        if self.capture_until is not None and now >= self.capture_until:
            self.capture_until = None
            self.capture_modifier = None
            self.notifications.put(("capture_timeout", "设置按键超时"))
        for _ in range(100):
            try:
                event = self.input_events.get_nowait()
            except queue.Empty:
                return
            if isinstance(event, Exception):
                raise event
            if self.capture_until is not None:
                self._capture_event(event)
                continue
            if self.backend_error and not self.controller.recording:
                continue
            try:
                self.controller.handle_input(event.binding, pressed=event.pressed)
            except ValueError as error:
                self.notifications.put(("error", str(error)))
            except Exception as error:
                self._handle_failure(error)

    def _capture_event(self, event: KeyEvent) -> None:
        """捕获一个键位；单独修饰键在松开时确认，Esc 作为取消结果返回。"""
        binding = event.binding
        if event.pressed and binding.vk in MODIFIER_FLAGS:
            self.capture_modifier = binding
            return
        if not event.pressed:
            if self.capture_modifier is None or binding.vk != self.capture_modifier.vk:
                return
            binding = self.capture_modifier
        self.capture_until = None
        self.capture_modifier = None
        self.notifications.put(("captured", None if binding.vk == 27 else binding))

    def _safe_tick(self) -> None:
        try:
            self.controller.tick(time.monotonic())
        except Exception as error:
            self._handle_failure(error)

    def _handle_failure(self, error: Exception) -> None:
        """停止发送并报告错误；仅发送或释放失败会将后端标记为故障。"""
        LOGGER.error("输入操作失败", exc_info=error)
        if isinstance(error, BackendError):
            self.backend_error = str(error) or "输入后端异常"
        try:
            self.controller.stop()
        except Exception as release_error:
            if isinstance(release_error, BackendError):
                self.backend_error = str(release_error) or "按键释放失败"
            LOGGER.exception("失败后的按键释放未成功")
        self.notifications.put(("error", str(error)))

    def _publish(self) -> None:
        """用最新快照替换队列中的旧值，避免界面积压；后端故障时同时发布原因。"""
        snapshot = self.controller.snapshot()
        if self.snapshots.full():
            try:
                self.snapshots.get_nowait()
            except queue.Empty:
                pass
        name = UnavailableBackend.name if self.backend_error else self.backend.name
        note = (
            f"{self.backend.name}：{self.backend_error}"
            if self.backend_error
            else self.backend_note
        )
        self.snapshots.put_nowait((snapshot, name, note))

    def close(self) -> None:
        """唤醒并等待运行线程退出；超时则报错，由界面保留窗口供用户处理。"""
        self.stopping.set()
        self.commands.put(("stop", (), {}))
        self.join(timeout=5)
        if self.is_alive():
            raise RuntimeError("输入线程仍未退出，暂不关闭窗口")

    def _cleanup(self) -> None:
        """按监听、按键、后端、单实例的顺序清理；单项失败仍继续处理其余资源。"""
        for cleanup in (
            self.listener.stop if self.listener else None,
            self.controller.stop if self.controller else None,
            self.backend.close if self.backend else None,
            self.api.release_instance if self.api else None,
        ):
            if cleanup is None:
                continue
            try:
                cleanup()
            except Exception as error:
                LOGGER.exception("清理资源失败")
                self.shutdown_errors.append(str(error))
                self.notifications.put(("error", f"退出清理失败：{error}"))
