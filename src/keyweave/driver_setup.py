"""下载并校验固定版本的 Interception 驱动，通过管理员授权运行安装器。"""

import base64
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/oblitum/Interception/releases/download/v1.0.1/Interception.zip"
SHA256 = "ad038963d6413055765128b0b931f6e765147c9916dba79e65d872b261f9af10"
SUCCESS = "Interception successfully installed. You must reboot for it to take effect."


def cached_dll() -> Path:
    """返回当前用户的 DLL 缓存路径；文件存在不代表驱动已安装或设备可用。"""
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "KeyWeave/drivers/interception.dll"


def extract_package(data: bytes, directory: Path) -> tuple[Path, Path]:
    """校验压缩包 SHA-256 后提取 x64 DLL 和安装器，按此顺序返回路径。"""
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("驱动下载校验失败，请重试")
    archive = directory / "Interception.zip"
    archive.write_bytes(data)
    with zipfile.ZipFile(archive) as package:
        dll = directory / "interception.dll"
        installer = directory / "install-interception.exe"
        dll.write_bytes(package.read("Interception/library/x64/interception.dll"))
        installer.write_bytes(
            package.read("Interception/command line installer/install-interception.exe")
        )
    return dll, installer


def _quote(value: Path) -> str:
    """将路径转为 PowerShell 单引号字符串，并转义路径内的单引号。"""
    return "'" + str(value).replace("'", "''") + "'"


def run_installer(installer: Path, directory: Path) -> None:
    """请求管理员授权并等待安装结束；退出码和成功提示必须同时满足要求。"""
    log = directory / "install-output.txt"
    # 提权后的 PowerShell 将输出写入文件，并将安装器退出码传回父进程。
    script = (
        f"& {_quote(installer)} /install *> {_quote(log)}; exit $LASTEXITCODE"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    launcher = (
        "$ErrorActionPreference='Stop'; try { "
        f"$p=Start-Process -FilePath {_quote(powershell)} -Verb RunAs -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile -NonInteractive -EncodedCommand {encoded}' -Wait -PassThru; "
        "exit $p.ExitCode } catch { Write-Output $_.Exception.Message; exit 1 }"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", launcher],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = log.read_text(encoding="utf-16") if log.exists() else ""
    if result.returncode or SUCCESS not in output:
        raise RuntimeError(
            "驱动安装未完成：管理员授权可能被取消，或安装器执行失败。"
            + ("\n" + output.strip()[-1500:] if output else "请重试并允许管理员授权。")
        )


def install_driver(report) -> None:
    """下载、校验并缓存 DLL 后安装驱动；report 接收进度文本，成功后仍需用户重启。"""
    if sys.platform != "win32":
        raise RuntimeError("驱动安装仅支持 Windows")
    report("正在下载 Interception 驱动…")
    request = urllib.request.Request(URL, headers={"User-Agent": "KeyWeave/0.2"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("驱动下载文件大小异常")
    target = cached_dll()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="setup-", dir=target.parent) as temporary:
        directory = Path(temporary)
        dll, installer = extract_package(data, directory)
        # 先写入 DLL，避免驱动安装成功后才发现缓存目录无法写入。
        staged = target.with_suffix(".tmp")
        staged.write_bytes(dll.read_bytes())
        staged.replace(target)
        report("正在安装驱动，请允许 Windows 管理员授权…")
        run_installer(installer, directory)
