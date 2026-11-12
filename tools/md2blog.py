#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md2blog.py — 把 database/ 下的 markdown 批量转换为 blog/ 下的文章页。

用法：
    python tools/md2blog.py                    # 全量转换
    python tools/md2blog.py --dry-run          # 只打印将执行的操作，不写任何文件
    python tools/md2blog.py --single <片段>     # 只转换路径含该片段的一篇 md

规则：
- 每个 database 子文件夹 = 一个分类栏目；blog/index.html 与 blog/feed.xml
  只重写注释标记（POSTS / TOPICS / ITEMS）之间的区域，区域外的手动修改一律保留。
- 文章页文件名 = 「日期-拼音slug.html」（pypinyin 不可用时退化为 ASCII）。
- 重复运行结果一致（幂等）；唯一例外是 feed.xml 的 lastBuildDate（RSS 规范要求构建时间）。
- 源 md 的标题取自文件名（正文首行与标题相同时自动剔除）。
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

# ---------------------------------------------------------------- CONFIG（按需修改）

BLOG_NAME_EN = "Hawk's Notes"
BLOG_NAME_CN = "随想录"
AUTHOR_CN = "Hawk"
YEAR = 2026
SITE_URL = "https://hawkoding.github.io"
RSS_TITLE = f"{BLOG_NAME_EN} RSS"

MAX_SLUG_LEN = 40            # 拼音 slug 长度上限（在连字符边界截断）
PSEUDO_HEADING_MAX_LEN = 15  # "1. xxx" 内容不超过该字数时视为章节标题而非列表
POSTS_PER_MIN = 400          # 阅读速度：汉字/分钟
DECK_MIN_LEN = 60            # 摘要至少累计到该字数（否则继续取下一块）
DECK_MAX_LEN = 100           # 摘要截断上限
DECK_MAX_BLOCKS = 3          # 摘要最多取自前几个正文块

TZ_CN = timezone(timedelta(hours=8))
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

REPO_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = REPO_ROOT / "database"
POSTS_DIR = REPO_ROOT / "blog" / "posts"
INDEX_PATH = REPO_ROOT / "blog" / "index.html"
FEED_PATH = REPO_ROOT / "blog" / "feed.xml"

HARD_BREAK = "\x00"  # 行尾双空格硬换行的内部哨兵，最后统一替换为 <br>

# ---------------------------------------------------------------- 正则

RE_FILENAME = re.compile(r"^(\d{8})\s+(.+)\.md$")
RE_WEEKLY_SUFFIX = re.compile(r"（(\d{8})）$")
WEEKLY_PREFIX = "一周随想-"

RE_OBSIDIAN = re.compile(r"^!\[\[(.+?)\]\]\s*$")
RE_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
RE_H = re.compile(r"^(#{1,6})\s+(.+)$")
RE_BRACKET_H = re.compile(r"^【(.+?)】\s*$")
RE_QUOTE = re.compile(r"^\s*>\s?(.*)$")
RE_UL = re.compile(r"^(\s*)([-*+])\s+(.+)$")
RE_OL = re.compile(r"^(\s*)(\d+)[.．]\s+(.+)$")

RE_CODE = re.compile(r"`([^`]+)`")
RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
RE_URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
URL_TRAILING_PUNCT = "。，、；：！？）】」〉》\"'…"

GEN_NAME_RE = re.compile(r"^\d{8}-[a-z0-9-]+\.html$")

# ---------------------------------------------------------------- 基础工具

def escape_html(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s):
    return escape_html(s).replace('"', "&quot;").replace("'", "&#39;")


def plain_text(html_str):
    """去掉 HTML 标签与实体，得到纯文本。"""
    t = re.sub(r"<[^>]+>", "", html_str)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"')):
        t = t.replace(a, b)
    return t


def indent_level(ws):
    """前导空白 → 缩进层数（1 个 tab 或 2 个空格算 1 层）。"""
    return ws.count("\t") + ws.count(" ") // 2


def is_block_start(line):
    """该行是否为一个新块的起始（用于终止段落收集）。"""
    if not line:
        return True
    return (RE_OBSIDIAN.match(line) or RE_HR.match(line) or RE_H.match(line)
            or RE_QUOTE.match(line) or RE_UL.match(line) or RE_OL.match(line)
            or RE_BRACKET_H.match(line))


def format_rfc822(dt):
    """RFC 822 日期（+0800）。不能用 strftime：Windows 中文区域会输出中文星期/月份。"""
    return (f"{WEEKDAYS[dt.weekday()]}, {dt.day:02d} {MONTHS[dt.month - 1]} "
            f"{dt.year} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} +0800")


def atomic_write(path, content):
    """先写同目录临时文件，再原子替换；content 中换行原样写入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp, path)


def replace_region(text, begin_marker, end_marker, new_content):
    """替换 text 中 begin_marker 与 end_marker 之间的内容（两个标记本身保留）。"""
    b = text.find(begin_marker)
    e = text.find(end_marker)
    if b == -1 or e == -1 or e < b:
        sys.exit(f"错误：文件中缺少完整标记对 {begin_marker} … {end_marker}，"
                 f"请先手工插入。")
    return text[: b + len(begin_marker)] + new_content + text[e:]


# ---------------------------------------------------------------- 文件名与 slug

def parse_filename(name):
    """从文件名解析 (日期YYYYMMDD, 标题)。解析不出日期时返回 (None, None)。"""
    stem = name[:-3] if name.lower().endswith(".md") else name
    if stem.startswith(WEEKLY_PREFIX):
        title = stem[len(WEEKLY_PREFIX):]
        m = RE_WEEKLY_SUFFIX.search(title)
        if m:
            return m.group(1), title[: m.start()].strip()
        return None, title
    m = RE_FILENAME.match(name)
    if not m:
        return None, None
    return m.group(1), m.group(2).strip()


def slugify(title):
    """标题 → 拼音 slug（仅保留 [a-z0-9]，以 - 连接）。"""
    if lazy_pinyin is not None:
        segs = []
        # 注意：不用 errors="ignore"——它会连数字一起丢弃；默认模式下
        # 非中文字符原样透传，由下面的正则只保留 [a-z0-9]。
        for syl in lazy_pinyin(title):
            seg = re.sub(r"[^a-z0-9]+", "-", syl.lower()).strip("-")
            if seg:
                segs.append(seg)
        slug = "-".join(segs)
    else:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "post"
    if len(slug) > MAX_SLUG_LEN:
        cut = slug[: MAX_SLUG_LEN].rsplit("-", 1)[0]
        if cut:
            slug = cut
    return slug




# ---------------------------------------------------------------- markdown 解析

def normalize_quotes(text):
    """修复反置弯引号：块内 “/” 交替开闭。"""
    out = []
    open_next = True
    for ch in text:
        if ch == "“" or ch == "”":
            out.append("“" if open_next else "”")
            open_next = not open_next
        else:
            out.append(ch)
    return "".join(out)


def auto_link(m):
    url = m.group(0).rstrip(URL_TRAILING_PUNCT)
    return f'<a href="{url}">{url}</a>'


def inline(text):
    """行内处理：引号归一化 → HTML 转义 → 行内代码 → 加粗 → 裸 URL → 硬换行。"""
    text = normalize_quotes(text)
    text = (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
    text = RE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = RE_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = RE_URL.sub(auto_link, text)
    return text.replace(HARD_BREAK, "<br>")


def parse_list(proc, i, kind, level):
    """解析一层列表（ul/ol），支持更深的嵌套子列表。返回 (html, 新下标)。"""
    tag = "ol" if kind == "ol" else "ul"
    items = []
    n = len(proc)
    while i < n:
        content, hard = proc[i]
        if content == "":
            i += 1
            continue
        m = RE_OL.match(content) if kind == "ol" else RE_UL.match(content)
        if not m or indent_level(m.group(1)) != level:
            break
        item_text = m.group(3) + (HARD_BREAK if hard else "")
        i += 1
        subs = []
        while i < n:
            c2, _ = proc[i]
            if c2 == "":
                break
            mu = RE_UL.match(c2)
            mo = RE_OL.match(c2)
            if mu or mo:
                lvl = indent_level(mu.group(1)) if mu else indent_level(mo.group(1))
                if lvl > level:
                    sub_html, i = parse_list(proc, i, "ol" if mo else "ul", lvl)
                    subs.append(sub_html)
                    continue
            break
        items.append((item_text, subs))
    out = [f"<{tag}>"]
    for text, subs in items:
        inner = inline(text)
        for s in subs:
            inner += "\n" + s
        out.append(f"<li>{inner}</li>")
    out.append(f"</{tag}>")
    return "\n".join(out), i


def parse_md(text, src_label):
    """markdown 文本 → [(kind, html)]，kind ∈ p/h2/blockquote/ul/ol/hr。"""
    proc = []
    for raw in text.splitlines():
        hard = raw.endswith("  ")
        line = raw.rstrip()
        proc.append(("", False) if not line.strip() else (line, hard))
    while proc and proc[0][0] == "":
        proc.pop(0)
    while proc and proc[-1][0] == "":
        proc.pop()

    blocks = []
    warnings = []
    n = len(proc)
    i = 0
    while i < n:
        content, hard = proc[i]
        if content == "":
            i += 1
            continue

        m = RE_OBSIDIAN.match(content)
        if m:
            warnings.append(f"[跳过嵌入图片] {src_label}: {m.group(1)}")
            i += 1
            continue

        if RE_HR.match(content):
            blocks.append(("hr", "<hr>"))
            i += 1
            continue

        m = RE_H.match(content)
        if m:
            blocks.append(("h2", f"<h2>{inline(m.group(2))}</h2>"))
            i += 1
            continue

        m = RE_BRACKET_H.match(content)
        if m and len(m.group(1)) <= 40:
            blocks.append(("h2", f"<h2>{inline(m.group(1))}</h2>"))
            i += 1
            continue

        m = RE_QUOTE.match(content)
        if m:
            parts = []
            while i < n:
                qm = RE_QUOTE.match(proc[i][0])
                if not qm:
                    break
                parts.append(qm.group(1))
                i += 1
            body = HARD_BREAK.join(parts)
            blocks.append(("blockquote", f"<blockquote>{inline(body)}</blockquote>"))
            continue

        m = RE_UL.match(content)
        if m:
            html, i = parse_list(proc, i, "ul", indent_level(m.group(1)))
            blocks.append(("ul", html))
            continue

        m = RE_OL.match(content)
        if m:
            if len(m.group(3).strip()) <= PSEUDO_HEADING_MAX_LEN:
                # "1. 市场的本质" 这类短行其实是章节标题
                blocks.append(("h2", f"<h2>{inline(m.group(2) + '. ' + m.group(3))}</h2>"))
                i += 1
                continue
            html, i = parse_list(proc, i, "ol", indent_level(m.group(1)))
            blocks.append(("ol", html))
            continue

        # 普通段落：收集连续非空行，直到空行或下一个块起始符
        parts = []
        while i < n:
            c2, h2flag = proc[i]
            if c2 == "" or is_block_start(c2):
                break
            parts.append(c2 + (HARD_BREAK if h2flag else ""))
            i += 1
        if parts:
            blocks.append(("p", f"<p>{inline(''.join(parts))}</p>"))
        else:
            i += 1
    return blocks, warnings


# ---------------------------------------------------------------- 摘要与阅读时长

def extract_deck(blocks):
    """从正文块提取摘要：跳过标题/分隔线，累计到 DECK_MIN_LEN 字或 DECK_MAX_BLOCKS 块。"""
    parts, count = [], 0
    for kind, html in blocks:
        if kind in ("h2", "hr"):
            continue
        if kind in ("ul", "ol"):
            m = re.search(r"<li>(.*?)</li>", html, re.DOTALL)
            t = plain_text(m.group(1)) if m else ""
        else:
            t = plain_text(html)
        if t:
            parts.append(t)
            count += 1
        if count >= DECK_MAX_BLOCKS or (parts and len(" ".join(parts)) >= DECK_MIN_LEN):
            break
    deck = re.sub(r"\s+", " ", " ".join(parts)).strip()
    # 中文（含全角标点）之间不留空格
    deck = re.sub(r"([^\x00-\xff])\s+([^\x00-\xff])", r"\1\2", deck)
    if len(deck) > DECK_MAX_LEN:
        cut = deck[: DECK_MAX_LEN]
        idx = max((cut.rfind(p) for p in "。！？；!?"), default=-1)
        if idx > 0:
            cut = cut[: idx + 1]
        deck = cut + "……"
    return deck


def reading_minutes(blocks):
    """阅读时长：只数汉字，POSTS_PER_MIN 字/分钟，四舍五入，最少 1。"""
    text = "".join(plain_text(h) for _, h in blocks)
    han = sum(1 for c in text if "一" <= c <= "鿿")
    return max(1, int(han / POSTS_PER_MIN + 0.5))


# ---------------------------------------------------------------- 文章模型与转换

@dataclass
class Post:
    src: Path
    category: str
    date: datetime
    title: str
    slug: str
    filename: str
    deck: str
    minutes: int
    body_html: str


def convert(src, category):
    text = src.read_text(encoding="utf-8-sig")
    date_str, title = parse_filename(src.name)
    if not date_str:
        raise ValueError(f"无法从文件名解析日期：{src.name}")
    d = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=TZ_CN)
    lines = text.splitlines()
    if lines and lines[0].strip() == title.strip():
        lines = lines[1:]  # 正文首行与标题相同，剔除
    blocks, warnings = parse_md("\n".join(lines), src.name)
    for w in warnings:
        print(w)
    deck = extract_deck(blocks) or title
    minutes = reading_minutes(blocks)
    body = "\n\n".join(html for _, html in blocks)
    body = "\n".join((" " * 10 + line) if line else "" for line in body.split("\n"))
    return Post(src=src, category=category, date=d, title=title,
                slug=slugify(title), filename="", deck=deck,
                minutes=minutes, body_html=body)


def assign_filenames(posts):
    """分配「日期-拼音slug.html」文件名：只在本次运行内去重。

    与 posts/ 下同名旧文件不做避让——重复运行会直接覆盖同名文件（幂等）；
    由调用方在运行结束后报告「本次未生成」的孤儿文件。
    """
    assigned = {}
    for p in posts:
        base = f"{p.date.strftime('%Y%m%d')}-{p.slug}"
        name = f"{base}.html"
        n = 2
        while name in assigned:
            name = f"{base}-{n}.html"
            n += 1
        p.filename = name
        assigned[name] = p
    return assigned


# ---------------------------------------------------------------- 模板

POST_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="%%DECK_ATTR%%">
  <title>%%TITLE%% — %%BLOG_NAME_EN%%</title>
  <meta property="og:type" content="article">
  <meta property="og:title" content="%%TITLE%%">
  <meta property="og:description" content="%%DECK_ATTR%%">
  <meta property="article:published_time" content="%%PUBLISHED_ISO%%">
  <!-- canonical / og:url / og:image / twitter:* 需要绝对 URL 且图片真实存在，
       本站部署在根路径 https://hawkoding.github.io/；上传封面图后取消注释:
  <link rel="canonical" href="%%CANONICAL_URL%%">
  <meta property="og:url" content="%%CANONICAL_URL%%">
  <meta property="og:image" content="https://hawkoding.github.io/images/og-cover.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="https://hawkoding.github.io/images/og-cover.png">
  -->
  <link rel="alternate" type="application/rss+xml" title="%%RSS_TITLE%%" href="../feed.xml">
  <link rel="stylesheet" href="../blog.css">
</head>
<body>
  <div class="shell post-shell">
    <header class="site-header">
      <a class="brand" href="../index.html"><strong>%%BLOG_NAME_EN%%</strong><span>%%BLOG_NAME_CN%%</span></a>
      <nav aria-label="博客导航">
        <a href="../../index.html">关于我</a>
        <a href="../index.html" aria-current="page">文章</a>
        <a class="rss" href="../feed.xml">RSS</a>
      </nav>
    </header>

    <main>
      <article>
        <header class="post-header">
          <a class="back-link" href="../index.html">← 返回全部文章</a>
          <h1>%%TITLE%%</h1>
          <p class="post-deck">%%DECK_HTML%%</p>
          <div class="post-meta">
            <time datetime="%%DATE_ATTR%%">%%DATE_DISPLAY%%</time>
            <span>%%CATEGORY%%</span>
            <span>%%READING_MIN%% min read</span>
          </div>
        </header>

        <div class="post-body">
%%BODY%%
        </div>
      </article>
    </main>

    <footer class="site-footer">
      <span>© %%YEAR%% %%AUTHOR_CN%%</span>
      <a href="../index.html">继续阅读 →</a>
    </footer>
  </div>
</body>
</html>
"""

SECTION_TEMPLATE = """        <section aria-labelledby="{heading_id}">
          <div class="section-heading">
            <h2 id="{heading_id}">{category}</h2>
            <span class="section-label">No. {count:03d}</span>
          </div>

{cards}        </section>"""

CARD_TEMPLATE = """          <article class="article-card">
            <a href="posts/{filename}">
              <div class="meta">
                <time datetime="{date_attr}">{date_display}</time>
                <span>{category}</span>
                <span>{minutes} min read</span>
              </div>
              <h3>{title}</h3>
              <p>{deck}</p>
            </a>
          </article>"""

ITEM_TEMPLATE = """    <item>
      <title>{title}</title>
      <link>{url}</link>
      <guid>{url}</guid>
      <pubDate>{pubdate}</pubDate>
      <description>{deck}</description>
    </item>"""


def build_post_html(p):
    date_attr = p.date.strftime("%Y-%m-%d")
    date_display = p.date.strftime("%Y.%m.%d")
    tokens = {
        "%%TITLE%%": escape_html(p.title),
        "%%DECK_ATTR%%": escape_attr(p.deck),
        "%%DECK_HTML%%": escape_html(p.deck),
        "%%BLOG_NAME_EN%%": BLOG_NAME_EN,
        "%%BLOG_NAME_CN%%": BLOG_NAME_CN,
        "%%RSS_TITLE%%": RSS_TITLE,
        "%%PUBLISHED_ISO%%": f"{date_attr}T00:00:00+08:00",
        "%%CANONICAL_URL%%": f"{SITE_URL}/blog/posts/{p.filename}",
        "%%DATE_ATTR%%": date_attr,
        "%%DATE_DISPLAY%%": date_display,
        "%%CATEGORY%%": escape_html(p.category),
        "%%READING_MIN%%": str(p.minutes),
        "%%YEAR%%": str(YEAR),
        "%%AUTHOR_CN%%": AUTHOR_CN,
        "%%BODY%%": p.body_html,
    }
    html = POST_TEMPLATE
    for k, v in tokens.items():
        html = html.replace(k, v)
    return html


def sort_posts(posts):
    """日期倒序，同日期按标题升序（确定性顺序）。"""
    return sorted(posts, key=lambda p: (-p.date.toordinal(), p.title))


def build_index_regions(posts):
    """返回 (POSTS 区域新内容, TOPICS 区域新内容)。"""
    by_cat = {}
    for p in posts:
        by_cat.setdefault(p.category, []).append(p)
    cats = sorted(by_cat, key=lambda c: -max(p.date for p in by_cat[c]).toordinal())

    sections, topics = [], []
    for cat in cats:
        cat_posts = sort_posts(by_cat[cat])
        cards = "\n\n".join(
            CARD_TEMPLATE.format(
                filename=p.filename,
                date_attr=p.date.strftime("%Y-%m-%d"),
                date_display=p.date.strftime("%Y.%m.%d"),
                category=escape_html(p.category),
                minutes=p.minutes,
                title=escape_html(p.title),
                deck=escape_html(p.deck),
            )
            for p in cat_posts
        )
        sections.append(SECTION_TEMPLATE.format(
            heading_id=f"cat-{slugify(cat)}",
            category=escape_html(cat),
            count=len(cat_posts),
            cards=cards,
        ))
        topics.append(f"              <li>{escape_html(cat)}（{len(cat_posts)} 篇）</li>")

    posts_region = "\n" + "\n\n".join(sections) + "\n        "
    topics_region = "\n" + "\n".join(topics) + "\n              "
    return posts_region, topics_region


def build_feed_region(posts):
    """返回 (ITEMS 区域新内容, 新的 lastBuildDate 行文本)。"""
    items = "\n\n".join(
        ITEM_TEMPLATE.format(
            title=escape_html(p.title),
            url=f"{SITE_URL}/blog/posts/{p.filename}",
            pubdate=format_rfc822(p.date.replace(hour=0, minute=0, second=0)),
            deck=escape_html(p.deck),
        )
        for p in sort_posts(posts)
    )
    return "\n" + items + "\n    ", format_rfc822(datetime.now(TZ_CN))


# ---------------------------------------------------------------- 主流程

def collect_md_files():
    result = []
    if not DATABASE_DIR.is_dir():
        sys.exit(f"错误：找不到 database 目录：{DATABASE_DIR}")
    for sub in sorted(DATABASE_DIR.iterdir()):
        if not sub.is_dir():
            continue
        files = sorted(sub.glob("*.md"))
        if not files:
            print(f"注意：空目录 {sub.name}/（无 md 文件，不生成栏目）")
            continue
        for f in files:
            result.append((f, sub.name))
    return result


def existing_generated_filenames():
    if not POSTS_DIR.is_dir():
        return set()
    return {f.name for f in POSTS_DIR.iterdir() if GEN_NAME_RE.match(f.name)}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="database 下 markdown → blog 文章页")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的操作，不写任何文件")
    ap.add_argument("--single", metavar="片段",
                    help="只转换路径含该片段的一篇 md")
    args = ap.parse_args()

    md_files = collect_md_files()
    if args.single:
        md_files = [t for t in md_files if args.single in str(t[0])]
        if not md_files:
            sys.exit(f"没有找到路径含 {args.single!r} 的 md 文件")

    posts = [convert(src, category) for src, category in md_files]
    assign_filenames(posts)

    if not posts:
        print("没有可转换的文章。")
        return

    posts_region, topics_region = build_index_regions(posts)
    items_region, build_date = build_feed_region(posts)

    if args.dry_run:
        print(f"\n[dry-run] 共 {len(posts)} 篇文章：")
        for p in sort_posts(posts):
            print(f"  {p.date.strftime('%Y-%m-%d')}  [{p.category}]  "
                  f"{p.filename}  ←  {p.src.relative_to(REPO_ROOT)}")
        cats = sorted({p.category for p in posts})
        print(f"[dry-run] blog/index.html 将生成 {len(cats)} 个栏目"
              f"（{', '.join(cats)}），侧栏「关注主题」同步 {len(cats)} 条")
        print(f"[dry-run] blog/feed.xml 将写入 {len(posts)} 个 item")
        print("[dry-run] 未写任何文件。")
        return

    # 全部输出已在内存构建完成，开始写盘
    for p in posts:
        atomic_write(POSTS_DIR / p.filename, build_post_html(p))

    index_text = INDEX_PATH.read_bytes().decode("utf-8")
    index_crlf = "\r\n" in index_text
    index_text = index_text.replace("\r\n", "\n")
    index_text = replace_region(index_text, "<!-- POSTS:BEGIN -->",
                                "<!-- POSTS:END -->", posts_region)
    index_text = replace_region(index_text, "<!-- TOPICS:BEGIN -->",
                                "<!-- TOPICS:END -->", topics_region)
    atomic_write(INDEX_PATH, index_text.replace("\n", "\r\n") if index_crlf else index_text)

    feed_text = FEED_PATH.read_bytes().decode("utf-8")
    feed_crlf = "\r\n" in feed_text
    feed_text = feed_text.replace("\r\n", "\n")
    feed_text = replace_region(feed_text, "<!-- ITEMS:BEGIN -->",
                               "<!-- ITEMS:END -->", items_region)
    feed_text = re.sub(r"(<lastBuildDate>)[^<]*(</lastBuildDate>)",
                       lambda m: m.group(1) + build_date + m.group(2),
                       feed_text, count=1)
    atomic_write(FEED_PATH, feed_text.replace("\n", "\r\n") if feed_crlf else feed_text)

    print(f"完成：生成 {len(posts)} 篇文章页（blog/posts/）")
    cats = sorted({p.category for p in posts})
    print(f"完成：重写 blog/index.html（{len(cats)} 个栏目：{', '.join(cats)}）")
    print(f"完成：重写 blog/feed.xml（{len(posts)} 个 item + lastBuildDate）")

    orphans = sorted(existing_generated_filenames() - {p.filename for p in posts})
    if orphans:
        print(f"注意：posts/ 下这些按生成命名规则命名的文件本次未生成（可能已弃用，请手动处理）：")
        for o in orphans:
            print(f"  {o}")


if __name__ == "__main__":
    main()
