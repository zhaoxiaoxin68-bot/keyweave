"""配置与 JSON 的相互转换，以及 SQLite 中的整份配置读写。"""

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .models import AppConfig, CycleStep, Hotkeys, KeyBinding, Modifier, Slot


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return base / "KeyWeave" / "config.sqlite3"


def binding_from_dict(data: dict | None) -> KeyBinding | None:
    """将 JSON 键位还原为 KeyBinding；None 表示未设置快捷键。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("键位必须为JSON对象")
    modifiers = data.get("modifiers", 0)
    if type(modifiers) is not int or not 0 <= modifiers <= 15:
        raise ValueError("修饰键必须为 0–15 之间的整数标记")
    return KeyBinding(data["vk"], Modifier(modifiers))


def to_dict(config: AppConfig) -> dict:
    """校验配置并转为带 schema_version 的字典，供导出和数据库保存使用。"""
    config.validate()
    return {"schema_version": 1, **asdict(config)}


def from_dict(data: dict) -> AppConfig:
    """还原版本 1 的配置并校验；缺失的配置字段采用默认值，无效版本或配置抛出异常。"""
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须为JSON对象")
    if data.get("schema_version") != 1:
        raise ValueError("配置版本不受支持")
    values = {name: data[name] for name in AppConfig.__dataclass_fields__ if name in data}
    if "group_names" in values:
        if not isinstance(values["group_names"], (list, tuple)):
            raise ValueError("分组名称必须为JSON数组")
        values["group_names"] = tuple(values["group_names"])
    if "sets" in values:
        values["sets"] = tuple(
            tuple(
                Slot(_required_binding(slot["binding"]), slot.get("enabled", True))
                for slot in slots
            )
            for slots in values["sets"]
        )
    values["cycle_steps"] = tuple(CycleStep(**step) for step in data.get("cycle_steps", []))
    hotkeys = dict(data.get("hotkeys", {}))
    for name in ("toggle", "cycle", "switch_set", "reset_cycle", "auxiliary"):
        if name in hotkeys:
            hotkeys[name] = binding_from_dict(hotkeys[name])
    values["hotkeys"] = Hotkeys(**hotkeys)
    result = AppConfig(**values)
    result.validate()
    return result


def _required_binding(data: dict) -> KeyBinding:
    binding = binding_from_dict(data)
    if binding is None:
        raise ValueError("按键配置缺少键位")
    return binding


class ConfigStore:
    """将整份配置作为 JSON 保存到 SQLite 的单行记录中。"""

    def __init__(self, path: Path | None = None):
        self.path = path or config_path()

    def load(self) -> AppConfig:
        """只读加载配置；文件或记录不存在时返回默认值，损坏时交由入口处理。"""
        if not self.path.exists():
            return AppConfig()
        try:
            with closing(
                sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            ) as connection:
                row = connection.execute("SELECT payload FROM app_config WHERE id = 1").fetchone()
            return from_dict(json.loads(row[0])) if row else AppConfig()
        except sqlite3.Error as error:
            raise ValueError(f"本地数据库读取失败：{error}") from error

    def save(self, config: AppConfig) -> None:
        """先校验配置，再在同一事务中建表并更新记录；写入失败向调用方报错。"""
        payload = json.dumps(to_dict(config), ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=0.1)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS app_config ("
                    "id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO app_config (id, payload) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                    (payload,),
                )
