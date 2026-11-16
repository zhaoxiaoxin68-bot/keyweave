"""从源码目录启动；依赖安装见 pyproject.toml。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from keyweave.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
