#!/usr/bin/env python3
"""校验教学站里的源码片段是否与仓库源文件逐字一致。

每个代码块以 ``data-file`` 声明源文件路径、``data-start`` 声明起始行号（1-based）。
本脚本把 HTML 中的片段与源文件对应行做精确比对，任何漂移都会被报出来——
源码升级后重跑一次即可发现失效的引用。

用法：
    python3 sites_cn/tools/verify_snippets.py
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent

BLOCK_RE = re.compile(
    r'<div class="code"([^>]*)>\s*<pre><code>(.*?)</code></pre>',
    re.DOTALL,
)
ATTR_RE = re.compile(r'data-(file|start|lang)="([^"]*)"')


def parse_blocks(text: str):
    for attrs_raw, body in BLOCK_RE.findall(text):
        attrs = dict(ATTR_RE.findall(attrs_raw))
        yield attrs, body


def main() -> int:
    checked = 0
    skipped = 0
    failures: list[str] = []

    for page in sorted(SITE_DIR.glob("*.html")):
        for attrs, body in parse_blocks(page.read_text(encoding="utf-8")):
            path_attr = attrs.get("file", "")
            start_attr = attrs.get("start", "")

            source = REPO_ROOT / path_attr
            if not start_attr or not source.is_file():
                # 说明性片段（伪代码、shell 命令、配置示例）不做行级比对。
                skipped += 1
                continue

            start = int(start_attr)
            snippet = html.unescape(body).lstrip("\n").rstrip()
            snippet_lines = snippet.split("\n")

            source_lines = source.read_text(encoding="utf-8").split("\n")
            expected = source_lines[start - 1 : start - 1 + len(snippet_lines)]
            expected = [line.rstrip() for line in expected]
            actual = [line.rstrip() for line in snippet_lines]

            checked += 1
            if actual != expected:
                detail = [
                    f"{page.name}  ->  {path_attr}:{start}",
                ]
                for i, (got, want) in enumerate(zip(actual, expected)):
                    if got != want:
                        detail.append(f"    行 {start + i}")
                        detail.append(f"      站内: {got!r}")
                        detail.append(f"      源码: {want!r}")
                if len(actual) != len(expected):
                    detail.append(
                        f"    行数不符: 站内 {len(actual)} 行 / 源码可取 {len(expected)} 行"
                    )
                failures.append("\n".join(detail))

    print(f"逐字比对 {checked} 个代码块，跳过 {skipped} 个说明性片段。")
    if failures:
        print(f"\n发现 {len(failures)} 处不一致：\n")
        print("\n\n".join(failures))
        return 1
    print("全部一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
