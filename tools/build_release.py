"""从干净的源码副本构建 wheel、源码 ZIP 和对应校验和。"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("LICENSE", "README.md", "pyproject.toml", "run.py", "build_exe.bat")
DIRECTORIES = ("src", "tools", "assets")


def main():
    """从临时源码副本构建分发包，核对 wheel 内容后写出文件大小和 SHA-256。"""
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory) / "keyweave"
        stage.mkdir()
        for name in FILES:
            shutil.copy2(ROOT / name, stage / name)
        for name in DIRECTORIES:
            shutil.copytree(
                ROOT / name, stage / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
            )
        archive = Path(directory) / "keyweave-source.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, Path("keyweave") / path.relative_to(stage))
        wheels = Path(directory) / "wheels"
        subprocess.run(
            [sys.executable, "-c", "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])", str(wheels)],
            cwd=stage, check=True,
        )
        wheel, = wheels.glob("*.whl")
        # 发布前核对实际构建内容，避免旧 build/ 副本混入分发包。
        with ZipFile(wheel) as bundle:
            for path in (stage / "src/keyweave").rglob("*"):
                if path.is_file() and path.suffix in (".py", ".svg"):
                    assert bundle.read(path.relative_to(stage / "src").as_posix()) == path.read_bytes()
        checksums = {}
        for path in (archive, wheel):
            target = output / path.name
            shutil.copy2(path, target)
            checksums[target.name] = {
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        (output / "checksums.json").write_text(
            json.dumps(checksums, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print("Release built and verified:", ", ".join(checksums))


if __name__ == "__main__":
    main()
