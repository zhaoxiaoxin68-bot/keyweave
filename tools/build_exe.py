"""在 Windows x64 上生成包含 Python、Qt 运行库的单文件程序。"""

import argparse
import hashlib
import importlib.util
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """检查构建环境后生成单文件 exe，并写出 SHA-256；构建失败时以非零状态结束。"""
    parser = argparse.ArgumentParser(description="生成 artifacts/KeyWeave.exe（Windows x64）")
    parser.parse_args()
    if sys.platform != "win32":
        parser.error("请在 Windows x64 上运行；PyInstaller 不支持从 Linux 交叉生成 Windows exe")
    if struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("请使用 Windows x64 版本的 Python 3.11 或更新版本")
    if any(importlib.util.find_spec(name) is None for name in ("PyInstaller", "PySide6")):
        parser.error('缺少打包依赖，请先运行 python -m pip install ".[build]"')

    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="keyweave-build-") as directory:
        temporary = Path(directory)
        command = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", "KeyWeave",
            "--paths", str(ROOT / "src"),
            "--distpath", str(temporary / "dist"),
            "--workpath", str(temporary / "build"),
            "--specpath", str(temporary),
            "--add-data", f"{ROOT / 'LICENSE'}:.",
            str(ROOT / "run.py"),
        ]
        try:
            subprocess.run(command, cwd=ROOT, check=True)
        except subprocess.CalledProcessError as error:
            return error.returncode
        executable = output / "KeyWeave.exe"
        pending = output / "KeyWeave.exe.tmp"
        shutil.copy2(temporary / "dist/KeyWeave.exe", pending)
        pending.replace(executable)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    executable.with_suffix(".exe.sha256").write_text(
        f"{digest}  {executable.name}\n", encoding="utf-8"
    )
    print(f"已生成：{executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
