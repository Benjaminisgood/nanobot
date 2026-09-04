#!/usr/bin/env python3
"""检查教学站的结构完整性：章节齐全、链接可达、骨架元素与练习卡片格式正确。

与 ``verify_snippets.py`` 分工：
  · verify_snippets.py 保证「源码引用逐字准确」
  · check_site.py      保证「站点结构不散架」

用法：
    python3 sites_cn/tools/check_site.py
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class TagBalanceChecker(HTMLParser):
    """检查标签是否配对。未闭合的 div 会让整个页面布局塌掉。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in VOID_TAGS:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"第 {self.getpos()[0]} 行：多余的 </{tag}>")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        # 尝试在栈里回溯，报告被跳过的未闭合标签
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.stack[depth][0] == tag:
                for skipped, line in self.stack[depth + 1:]:
                    self.errors.append(f"第 {line} 行：<{skipped}> 未闭合")
                del self.stack[depth:]
                return
        self.errors.append(f"第 {self.getpos()[0]} 行：</{tag}> 没有匹配的开始标签")

    def finish(self) -> list[str]:
        for tag, line in self.stack:
            self.errors.append(f"第 {line} 行：<{tag}> 未闭合（文件结束）")
        return self.errors

SITE_DIR = Path(__file__).resolve().parent.parent
APP_JS = SITE_DIR / "assets" / "app.js"

# app.js 里的 CHAPTERS 是唯一的章节数据源，站点结构必须与它一致。
CHAPTER_RE = re.compile(r'\{\s*file:\s*"([^"]+)",\s*num:\s*"([^"]+)",\s*title:\s*"([^"]+)"')

REQUIRED_IDS = ["progress-bar", "sidebar", "topbar", "pager", "done-toggle"]

HREF_RE = re.compile(r'href="([^"]+)"')
QUIZ_RE = re.compile(r'<div class="quiz">(.*?)(?=<div class="quiz">|<h2|<div class="callout"|$)', re.DOTALL)
CODE_OPEN_RE = re.compile(r'<div class="code"([^>]*)>')


def main() -> int:
    problems: list[str] = []

    chapters = CHAPTER_RE.findall(APP_JS.read_text(encoding="utf-8"))
    if not chapters:
        print("无法从 assets/app.js 解析 CHAPTERS，检查中止。")
        return 1

    print(f"app.js 声明了 {len(chapters)} 个章节。\n")

    # 1) 章节文件齐全
    for file, num, title in chapters:
        if not (SITE_DIR / file).is_file():
            problems.append(f"[缺失章节] {num} {title} -> {file} 不存在")

    pages = sorted(SITE_DIR.glob("*.html"))
    declared = {file for file, _, _ in chapters}
    redirects: list[Path] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if 'http-equiv="refresh"' in text.lower().replace(" ", ""):
            redirects.append(page)
            continue
        if page.name not in declared:
            problems.append(f"[未登记] {page.name} 存在但未在 app.js 的 CHAPTERS 里声明")

    # 2) 逐页检查（跳过旧书签跳转页）
    for page in pages:
        if page in redirects:
            continue
        text = page.read_text(encoding="utf-8")
        where = page.name

        for ident in REQUIRED_IDS:
            if f'id="{ident}"' not in text:
                problems.append(f"[骨架缺失] {where} 缺少 id=\"{ident}\"")

        if "assets/app.js" not in text:
            problems.append(f"[骨架缺失] {where} 未引入 assets/app.js")
        if "assets/style.css" not in text:
            problems.append(f"[骨架缺失] {where} 未引入 assets/style.css")
        if page.name != "index.html" and 'id="toc"' not in text:
            problems.append(f"[骨架缺失] {where} 缺少 id=\"toc\"（页内目录）")

        # 内部链接可达
        for href in HREF_RE.findall(text):
            if href.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = href.split("#", 1)[0]
            if not target:
                continue
            if not (SITE_DIR / target).exists():
                problems.append(f"[死链] {where} -> {href}")

        # 练习卡片必须同时有问题与答案
        for i, body in enumerate(QUIZ_RE.findall(text), start=1):
            if '<div class="q">' not in body:
                problems.append(f"[练习格式] {where} 第 {i} 个 .quiz 缺少 .q 问题块")
            if '<div class="ans">' not in body:
                problems.append(f"[练习格式] {where} 第 {i} 个 .quiz 缺少 .ans 答案块")
            if 'class="tag' not in body:
                problems.append(f"[练习格式] {where} 第 {i} 个 .quiz 缺少难度 tag")

        # 代码块必须声明 data-file，否则渲染不出文件头
        for attrs in CODE_OPEN_RE.findall(text):
            if "data-file=" not in attrs:
                problems.append(f"[代码块] {where} 有一个 .code 未声明 data-file：{attrs.strip()[:60]}")

        # 标签闭合
        checker = TagBalanceChecker()
        checker.feed(text)
        for err in checker.finish():
            problems.append(f"[标签] {where} {err}")

    # 3) 统计
    print("每章练习数量与代码块数量：")
    for file, num, title in chapters:
        path = SITE_DIR / file
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        quizzes = text.count('<div class="quiz">')
        codes = text.count('<div class="code"')
        print(f"  {num} {title:<20} 练习 {quizzes:>2} 道 · 代码块 {codes:>2} 个")

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题：\n")
        for p in problems:
            print("  " + p)
        return 1
    print("结构检查全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
