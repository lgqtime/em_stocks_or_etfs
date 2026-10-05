"""基础文件加载（原则一 ②：本地读取与内存传递走同一套加载器）。

统一剥离 `_meta` / `_note` / `_notes` / `_usage` 等非业务字段——V1 曾出现 `_meta`
字段泄漏进提示词的故障，这里是唯一的入口，剥离只做一次。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # <项目根>/src/configs.py → <项目根>
CONFIG = ROOT / "config"
RUNS = ROOT / "runs"

# 从任意层级剥离的非业务字段
_META_KEYS = {"_note", "_notes", "_usage", "_file", "_batches", "source_file",
              "generated_from", "generated"}


def strip_meta(obj):
    """递归剥离非业务字段。所有外部 JSON 进来先过这一道。"""
    if isinstance(obj, dict):
        return {k: strip_meta(v) for k, v in obj.items() if k not in _META_KEYS}
    if isinstance(obj, list):
        return [strip_meta(v) for v in obj]
    return obj


def _read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclasses.dataclass(frozen=True)
class Item:
    """候选标的。个股与 ETF 同构，ETF 无 region（补空串）。"""
    code: str
    name: str
    level1: str
    level2: str
    level3: str
    scope: str = ""
    region: str = ""
    note: str = ""

    def brief(self) -> str:
        return f"{self.name}({self.code})｜{self.level1}/{self.level2}/{self.level3}｜{self.scope}"


class Pool:
    """个股池 / ETF 池。提供行业层级与反查。"""

    def __init__(self, raw: dict):
        self.kind = raw.get("instrument", "STOCK")
        self.data_date = raw.get("data_date", "")
        self.level1_names: list[str] = []
        self.level2_of: dict[str, list[str]] = {}          # level1 -> [level2]
        self.items_of_l2: dict[str, list[Item]] = {}        # level2 -> [Item]
        self.items_of_l1: dict[str, list[Item]] = {}        # level1 -> [Item]
        self.by_code: dict[str, Item] = {}
        self.by_name: dict[str, Item] = {}

        for l1 in raw.get("level1", []):
            n1 = l1["name"]
            self.level1_names.append(n1)
            self.level2_of[n1] = []
            for l2 in l1.get("level2", []):
                n2 = l2["name"]
                self.level2_of[n1].append(n2)
                bucket = self.items_of_l2.setdefault(n2, [])
                for l3 in l2.get("level3", []):
                    it = Item(code=l3["code"], name=l3["stock"],
                              level1=n1, level2=n2, level3=l3.get("name", n2),
                              scope=l3.get("scope", ""), region=l3.get("region", ""),
                              note=l3.get("note", ""))
                    bucket.append(it)
                    self.items_of_l1.setdefault(n1, []).append(it)
                    self.by_code[it.code] = it
                    self.by_name[it.name] = it
        self.excluded = raw.get("excluded", [])

    @property
    def count(self) -> int:
        return len(self.by_code)

    def find(self, token: str) -> Item | None:
        """按代码或名称找标的（Agent6 的输出只允许落在这里面）。"""
        token = (token or "").strip()
        return self.by_code.get(token) or self.by_name.get(token)

    def industry_map_text(self) -> str:
        """给 Agent3 的行业清单：一级 → 二级。"""
        return "\n".join(f"{n1}：{' / '.join(self.level2_of[n1])}"
                         for n1 in self.level1_names)

    def candidates_text(self, level1_names: list[str] | None = None) -> str:
        """给 Agent6 / Agent62 的候选清单。"""
        names = level1_names or self.level1_names
        lines = []
        for n1 in names:
            for it in self.items_of_l1.get(n1, []):
                lines.append(f"- {it.brief()}")
        return "\n".join(lines)


def load_pool(path: Path) -> Pool:
    raw = strip_meta(_read_json(path))
    if "counts" in raw:
        raw.pop("counts")
    return Pool(raw)


def load_stocks() -> Pool:
    return load_pool(CONFIG / "stocks.json")


def load_etfs() -> Pool:
    return load_pool(CONFIG / "etfs.json")


def load_blacklist() -> list[str]:
    return strip_meta(_read_json(CONFIG / "audit_blacklist.json"))["words"]


def load_tier_rules() -> dict:
    return strip_meta(_read_json(CONFIG / "tier_rules.json"))


def load_env() -> dict:
    """读 .env。容忍全角引号与空格。"""
    out = {}
    path = ROOT / ".env"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"\u201c\u201d\u2018\u2019\' ')
    return out


def run_dir(date: str) -> Path:
    return RUNS / date


def read_local(date: str, *parts: str):
    """原则一 ②：upstream 为 None 时从本地固定路径读取。"""
    p = run_dir(date).joinpath(*parts)
    if not p.exists():
        raise FileNotFoundError(f"本地输入不存在：{p}")
    return strip_meta(_read_json(p))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
