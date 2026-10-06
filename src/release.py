"""原则三的留档：把当前版本快照进 releases/<版本>_<日期>/，并生成逐文件 SHA256。

与 git 并行：git 管"变化"，留档文件夹管"状态"（任何版本可直接打开就跑）。

  python -m src.release v2.0 "首次落地：预测 + 回测"
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

from .configs import CONFIG, ROOT

SKIP_DIRS = {"__pycache__", ".git", ".cache", "runs", "releases", ".venv"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def build(version: str, note: str) -> Path:
    dest = ROOT / "releases" / f"{version}_{dt.date.today().isoformat()}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copytree(CONFIG, dest / "config")
    shutil.copytree(ROOT / "src", dest / "src",
                    ignore=shutil.ignore_patterns(*SKIP_DIRS))
    # report.py / showprompts.py 也是项目的一部分：留档要"打开就能跑"，
    # 少了它们就无法复现决策报告与提示词检视。之前漏了两个，已补。
    # requirements.txt 同样必需 —— 没有依赖清单，"打开就能跑"就无从谈起。
    for name in ("README.md", ".gitignore", "requirements.txt", "selftest.py",
                 "repeat.py", "preflight.py", "report.py", "showprompts.py"):
        f = ROOT / name
        if f.exists():
            shutil.copy2(f, dest / name)
    # docs/ 也必须留档：KNOWN_ISSUES.md 是调参依据与已知缺陷台账，
    # 缺了它，留档里的判据就成了"没有出处的规则"，事后无法审计。
    docs = ROOT / "docs"
    if docs.is_dir():
        shutil.copytree(docs, dest / "docs", dirs_exist_ok=True)
    plan = ROOT / "PROJECT_PLAN.md"
    if plan.exists():
        shutil.copy2(plan, dest / "PROJECT_PLAN_V2.md")

    (dest / "CHANGELOG.md").write_text(
        f"# {version}（{dt.date.today().isoformat()}）\n\n{note}\n", encoding="utf-8")

    manifest = {str(p.relative_to(dest)).replace("\\", "/"): sha256(p)
                for p in sorted(dest.rglob("*")) if p.is_file()}
    (dest / "MANIFEST.json").write_text(
        json.dumps({"version": version, "files": manifest}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return dest


def verify(dest: Path) -> list[str]:
    """三重检验之一：哈希一致。返回不一致的文件列表。"""
    man = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    bad = [rel for rel, digest in man.items()
           if not (dest / rel).exists() or sha256(dest / rel) != digest]
    return bad


if __name__ == "__main__":
    ver = sys.argv[1]
    msg = sys.argv[2] if len(sys.argv) > 2 else ""
    d = build(ver, msg)
    bad = verify(d)
    print(f"留档目录：{d}")
    print(f"文件数：{len(json.loads((d / 'MANIFEST.json').read_text(encoding='utf-8'))['files'])}")
    print("哈希校验：" + ("全部一致" if not bad else f"不一致 {bad}"))
