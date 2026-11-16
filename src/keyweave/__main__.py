"""程序入口：读取配置、初始化 Qt，并管理输入线程的启动与退出。"""

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import ConfigStore, config_path
from .driver_setup import cached_dll
from .models import AppConfig
from .runtime import Runtime
from .ui import Application
from .ui_widgets import setup_qt


def main(argv=None) -> int:
    """解析启动参数并进入 Qt 事件循环；配置损坏时改用恢复文件保存。"""
    parser = argparse.ArgumentParser(description="键序 · KeyWeave")
    parser.add_argument("--config", type=Path, help="使用指定的本地 SQLite 数据库")
    parser.add_argument("--backend", choices=("auto", "sendinput", "interception"))
    parser.add_argument("--driver-dll", type=Path, help="明确指定现有的x64 interception.dll")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("键序仅支持 Windows")
    path = args.config or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=path.with_name("app.log"),
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = ConfigStore(path)
    load_error = None
    try:
        config = store.load()
    except (ValueError, TypeError, KeyError, OSError) as error:
        config = AppConfig()
        load_error = str(error)
        # 使用单独的恢复文件，不让后续保存覆盖原始坏配置。
        store = ConfigStore(path.with_name("recovered-config.sqlite3"))
    if args.backend:
        config = replace(config, backend=args.backend)
    application_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )
    dll_path = args.driver_dll or application_dir / "assets/interception.dll"
    if args.driver_dll is None and cached_dll().exists():
        dll_path = cached_dll()
    qt = QApplication.instance() or QApplication(sys.argv[:1])
    setup_qt(qt)
    runtime = Runtime(config, store, dll_path)
    window = Application(runtime, config)
    window.show()
    runtime.start()
    if load_error:
        QTimer.singleShot(
            100,
            lambda: QMessageBox.warning(
                window, "配置读取失败", f"已使用默认配置，原文件已保留。\n{load_error}"
            ),
        )
    try:
        return qt.exec()
    finally:
        if runtime.is_alive():
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
