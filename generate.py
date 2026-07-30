"""
====================================================================
한국자동차환경협회 뉴스 웹진 - HTML 생성기 (STEP 3, 재구성판)
====================================================================
역할: 하루치 뉴스 데이터(JSON)를 받아 웹진 HTML을 생성한다.

생성물:
  1) news/YYYY-MM-DD.html  → 그날의 웹진 (날짜별 영구 보관본)
  2) index.html            → 메인 웹진 (5개 탭)
  3) archive.json          → 지난 발송 목록

5개 탭 구성:
  ① 데일리 모니터링   - 오늘 뉴스 (6개 카테고리)
  ② 해외뉴스 모니터링 - 해외 뉴스
  ③ 소식지           - newsletter/ 폴더의 분기별 소식지 (자동 감지)
  ④ 이전 뉴스 검색    - 전체 기사 검색 (모든 날짜의 기사)
  ⑤ 이전 웹진 보기    - 날짜별 웹진 목록 + 검색

소식지 자동 감지:
  newsletter/YYYY-QN.html 파일을 훑어 목록을 자동 생성.
  대표님은 소식지 HTML만 규칙대로 올리면 목록에 자동 추가됨. 코드 수정 불필요.

이전 뉴스 검색(방식 B):
  data/*.json 의 모든 기사를 모아 검색 인덱스를 만들어 index.html에 넣는다.
  브라우저 자바스크립트가 그 인덱스에서 검색 → 기사 목록으로 결과 표시.

사용법:
  python generate.py 2026-07-08   (날짜 지정)
  python generate.py              (data 폴더 최신 날짜 자동)
====================================================================
"""
import json
import os
import sys
import glob
import global_convert
import re
import html

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
MAIL_DATA_DIR = os.path.join(ROOT, "mail_data")

# ── 웹진 데이터 소스 스위치 ─────────────────────────────────────────
#   auto   : collect.py 가 모은 자동 수집 결과(data/)로 웹진을 만든다 (기본)
#   stibee : 담당자가 스티비에서 발행한 뉴스레터(mail_data/)로 웹진을 만든다
# 환경변수 WEBZINE_SOURCE 로 전환한다. 코드 수정 없이 되돌릴 수 있다.
# ※ 두 폴더를 따로 두는 이유: 자동 수집을 계속 돌려도 서로 덮어쓰지 않게 하기 위함.
#    '이전 뉴스 검색'은 어느 모드든 두 폴더를 모두 뒤진다.
WEBZINE_SOURCE = os.getenv("WEBZINE_SOURCE", "auto").strip().lower()
ACTIVE_DATA_DIR = MAIL_DATA_DIR if WEBZINE_SOURCE == "stibee" else DATA_DIR
os.makedirs(MAIL_DATA_DIR, exist_ok=True)
NEWS_DIR = os.path.join(ROOT, "news")
NEWSLETTER_DIR = os.path.join(ROOT, "newsletter")
GLOBAL_DIR = os.path.join(ROOT, "global")
ARCHIVE_FILE = os.path.join(ROOT, "archive.json")
INDEX_FILE = os.path.join(ROOT, "index.html")
STATS_FILE = os.path.join(ROOT, "stats.json")
DASHBOARD_FILE = os.path.join(ROOT, "dashboard.html")

os.makedirs(NEWS_DIR, exist_ok=True)
os.makedirs(NEWSLETTER_DIR, exist_ok=True)
os.makedirs(GLOBAL_DIR, exist_ok=True)

DAILY_ORDER = [
    "한국자동차환경협회 뉴스",
    "상위기관 뉴스",
    "자동차 LCA 사업 뉴스",
    "배출 저감사업 뉴스",
    "전기·수소차 뉴스 - 협회 사업 관련 뉴스",
    "전기·수소차 뉴스 - 업계 동향",
    "회원사 뉴스",
    "기타 뉴스",
]
GLOBAL_ORDER = ["유럽 (EU)", "미국 (USA)", "중국 (China)", "글로벌 종합"]

CSS = """
  :root {
    --primary: #1F4E79; --primary-dark: #163C5E; --primary-light: #E6F1FB;
    --accent: #2E75B6; --text: #1A1A1A; --text-sub: #555; --text-mute: #888;
    --border: #E5E5E5; --bg: #F8F9FB; --card: #FFFFFF;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "맑은 고딕", sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6; font-size: 14px; }
  .webzine-header { background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    color: white; padding: 28px 32px 22px; text-align: center; }
  .webzine-brand { font-size: 13px; letter-spacing: 2px; opacity: 0.85; margin-bottom: 6px; font-weight: 500; }
  .webzine-title { font-size: 28px; font-weight: 800; margin: 0 0 8px; letter-spacing: -0.5px; }
  .webzine-sub { font-size: 14px; opacity: 0.9; margin: 0; }
  .webzine-date { margin-top: 14px; font-size: 12px; opacity: 0.75; }
  .member-notice { background: var(--primary-light); border-bottom: 1px solid var(--border);
    padding: 10px 32px; font-size: 12px; color: var(--primary); text-align: center; }
  main { max-width: 800px; margin: 0 auto; padding: 28px 24px 40px; }
  .cat-section { margin-bottom: 32px; }
  .cat-header { background: var(--primary); color: white; padding: 14px 22px; font-size: 16px;
    font-weight: 700; border-radius: 6px 6px 0 0; letter-spacing: -0.3px; }
  .subcat-title { background: #EDF1F5; color: var(--primary); padding: 9px 22px; font-size: 13.5px;
    font-weight: 700; border-left: 1px solid var(--border); border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border); }
  .cat-body { background: var(--card); border: 1px solid var(--border); border-top: none;
    border-radius: 0 0 6px 6px; padding: 6px 0; }
  .cat-section .cat-body:not(:last-child) { border-radius: 0; }
  .news-item { padding: 18px 22px; border-bottom: 1px solid #F0F0F0; }
  .news-item:last-child { border-bottom: none; }
  .news-title { font-size: 16px; font-weight: 700; margin: 0 0 8px; line-height: 1.4; }
  .news-title a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
  .news-title a:hover { color: var(--primary); }
  .news-source { font-size: 12.5px; font-weight: 600; color: var(--text); background: #F5F5F5;
    padding: 2px 6px; border-radius: 3px; margin-right: 4px; }
  .news-summary { font-size: 14px; color: var(--text-sub); margin: 0; line-height: 1.65; }
  .footer { background: var(--card); border-top: 1px solid var(--border); padding: 24px 32px;
    text-align: center; font-size: 12px; color: var(--text-mute); line-height: 1.7; margin-top: 40px; }
  .footer strong { color: var(--text-sub); }
"""

INDEX_EXTRA_CSS = """
  .tabs { background: var(--card); border-bottom: 2px solid var(--border); padding: 0;
    display: flex; justify-content: center; gap: 0; flex-wrap: wrap; }
  .tab { padding: 16px 20px; cursor: pointer; font-size: 14.5px; color: var(--text-sub);
    border-bottom: 3px solid transparent; transition: all 0.15s; font-weight: 500; white-space: nowrap; }
  .tab:hover { color: var(--primary); background: var(--primary-light); }
  .tab.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 700; background: var(--primary-light); }
  .page { display: none; }
  .page.active { display: block; }
  .info-box { background: var(--primary-light); border-left: 4px solid var(--primary); padding: 14px 18px;
    border-radius: 4px; font-size: 13px; color: #0C2C4A; margin-bottom: 22px; line-height: 1.6; }
  .info-box strong { color: var(--primary); }
  .search-bar { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 20px; margin-bottom: 18px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  .search-label { font-size: 13px; color: var(--text-sub); font-weight: 500; }
  .search-input { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px;
    font-family: inherit; flex: 1; min-width: 200px; }
  .search-select { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px;
    font-family: inherit; background: var(--card); }
  .search-btn { padding: 8px 16px; background: var(--primary); color: white; border: none;
    border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 500; }
  .search-btn:hover { background: var(--primary-dark); }
  .archive-row { display: grid; grid-template-columns: 110px 1fr 80px; gap: 16px; align-items: center;
    padding: 14px 18px; background: var(--card); border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 8px; font-size: 13.5px; transition: border-color 0.15s; }
  .archive-row:hover { border-color: var(--accent); background: #FAFCFE; }
  .archive-date { font-weight: 700; color: var(--primary); font-size: 13px; }
  .archive-title { font-weight: 500; color: var(--text); }
  .archive-link { color: var(--accent); font-weight: 600; text-align: right; font-size: 13px;
    text-decoration: none; cursor: pointer; background: none; border: none; font-family: inherit; padding: 0; }
  .archive-link:hover { text-decoration: underline; }
  .nl-row { display: grid; grid-template-columns: 1fr 90px; gap: 16px; align-items: center;
    padding: 18px 22px; background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    margin-bottom: 10px; transition: border-color 0.15s; }
  .nl-row:hover { border-color: var(--accent); background: #FAFCFE; }
  .nl-title { font-size: 15.5px; font-weight: 700; color: var(--text); }
  .nl-sub { font-size: 12px; color: var(--text-mute); margin-top: 3px; }
  .nl-btn { padding: 8px 14px; background: var(--primary); color: white; border: none; border-radius: 6px;
    font-size: 13px; cursor: pointer; font-weight: 600; text-align: center; }
  .nl-btn:hover { background: var(--primary-dark); }
  .sr-item { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 18px; margin-bottom: 10px; }
  .sr-item:hover { border-color: var(--accent); }
  .sr-title { font-size: 15px; font-weight: 700; margin: 0 0 6px; line-height: 1.4; }
  .sr-title a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
  .sr-meta { font-size: 12px; color: var(--text-mute); margin-bottom: 6px; }
  .sr-cat { display: inline-block; background: var(--primary-light); color: var(--primary);
    padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-right: 6px; }
  .sr-datelink { color: var(--accent); font-weight: 600; text-decoration: none; cursor: pointer; }
  .sr-datelink:hover { text-decoration: underline; }
  .sr-summary { font-size: 13.5px; color: var(--text-sub); margin: 0; line-height: 1.6; }
  .sr-hint { text-align: center; padding: 40px 20px; color: var(--text-mute); font-size: 13px; }
"""

EDITION_EXTRA_CSS = """
  .back-bar { background: var(--card); border-bottom: 1px solid var(--border); padding: 12px 32px; }
  .back-link { color: var(--accent); font-size: 13px; font-weight: 600; text-decoration: none; cursor: pointer; }
  .back-link:hover { text-decoration: underline; }
  .archive-badge { display: inline-block; background: var(--primary-light); color: var(--primary);
    font-size: 11px; padding: 3px 10px; border-radius: 12px; margin-left: 8px; font-weight: 600; }
"""


def esc(text):
    return html.escape(str(text), quote=True)


def fmt_date_kr(date_str, dow):
    y, m, d = date_str.split("-")
    return f"{y}년 {int(m)}월 {int(d)}일 ({dow})"


def build_news_items(items):
    out = ""
    for i, it in enumerate(items, 1):
        title = esc(it.get("title", ""))
        date = esc(it.get("date", ""))
        summary = esc(it.get("summary", ""))
        url = it.get("url", "").strip()
        src_label = f"[{date}]" if date else ""
        if url:
            link = f'<a href="{esc(url)}" target="_blank" rel="noopener">{i}. {title}</a>'
        else:
            link = f'<a onclick="openArticle(event)">{i}. {title}</a>'
        source_html = f'<span class="news-source">{src_label}</span> ' if src_label else ""
        out += f"""      <div class="news-item">
        <div class="news-title">{link}</div>
        <p class="news-summary">{source_html}{summary}</p>
      </div>
"""
    return out


def build_cat_sections(cats_dict, order):
    """카테고리 섹션 생성.
    카테고리 이름에 ' - '가 있으면 '상위 - 하위'로 해석해,
    같은 상위끼리 하나의 큰 헤더 아래 하위 소제목으로 묶어 표시한다.
    예: '전기·수소차 뉴스 - 협회 사업 관련 뉴스' / '전기·수소차 뉴스 - 업계 동향'
        → 큰 헤더 '전기·수소차 뉴스' 아래 소제목 2개."""
    out = ""
    rendered = set()

    def render_simple(cat_name, items):
        return f"""  <div class="cat-section">
    <div class="cat-header">{esc(cat_name)}</div>
    <div class="cat-body">
{build_news_items(items)}    </div>
  </div>
"""

    def render_grouped(parent, subs):
        # subs: [(하위이름, items), ...]  (items 있는 것만)
        body = ""
        for sub_name, items in subs:
            body += f"""    <div class="subcat-title">{esc(sub_name)}</div>
    <div class="cat-body">
{build_news_items(items)}    </div>
"""
        return f"""  <div class="cat-section">
    <div class="cat-header">{esc(parent)}</div>
{body}  </div>
"""

    # order 순서대로 처리
    for cat_name in order:
        if cat_name in rendered:
            continue
        if " - " in cat_name:
            parent = cat_name.split(" - ", 1)[0]
            # 같은 parent를 가진 order 내 모든 하위를, 있는 것만 모아 그룹 렌더
            subs = []
            for c in order:
                if c.startswith(parent + " - ") and cats_dict.get(c):
                    subs.append((c.split(" - ", 1)[1], cats_dict[c]))
                    rendered.add(c)
            if subs:
                out += render_grouped(parent, subs)
        else:
            items = cats_dict.get(cat_name)
            if items:
                out += render_simple(cat_name, items)
                rendered.add(cat_name)

    # order에 없는 카테고리(혹시 모를 잔여)는 단순 표시
    for cat_name, items in cats_dict.items():
        if cat_name in rendered or not items:
            continue
        out += render_simple(cat_name, items)
        rendered.add(cat_name)
    return out


def build_edition_page(ed):
    date_kr = fmt_date_kr(ed["date"], ed["dow"])
    daily_sections = build_cat_sections(ed["daily"], DAILY_ORDER)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>데일리 뉴스 모니터링 제{esc(ed['no'])}호 · {esc(ed['date'])}</title>
<style>{CSS}{EDITION_EXTRA_CSS}</style>
</head>
<body>

<header class="webzine-header">
  <div class="webzine-brand">KAEA NEWSLETTER</div>
  <h1 class="webzine-title">데일리 뉴스 모니터링</h1>
  <p class="webzine-sub">한국자동차환경협회 웹진</p>
  <div class="webzine-date">{esc(date_kr)} · 제 {esc(ed['no'])}호</div>
</header>

<div class="back-bar">
  <a class="back-link" href="../index.html">← 오늘의 웹진으로 돌아가기</a>
  <span class="archive-badge">지난 뉴스 · 제{esc(ed['no'])}호</span>
</div>

<div class="member-notice">
  본 자료는 {esc(ed['date'])} 발송된 웹진 원본입니다.
</div>

<main>
{daily_sections}</main>

<footer class="footer">
  <strong>한국자동차환경협회 (KAEA)</strong><br>
  본 뉴스 웹진은 협회 회원사를 위한 일일 모니터링 서비스입니다.<br>
  본 자료의 무단 복제·배포를 금합니다.
</footer>

<script>
  function openArticle(e) {{
    if (e && e.preventDefault) e.preventDefault();
    alert('이 기사는 원문 링크가 아직 연결되지 않았습니다.');
    return false;
  }}
</script>

</body>
</html>
"""


def scan_newsletters():
    """newsletter/ 폴더의 소식지를 훑어 목록 생성 (자동 감지).

    파일명 규칙: YYYY-QN.html 또는 YYYY-QN.pdf
      예) 2026-Q2.html, 2026-Q1.pdf → "2026년 2분기 소식지"
    ⚠️ PDF 는 브라우저가 바로 열어주므로 변환하지 않는다."""
    items = []
    for ext in ("html", "pdf"):
        for path in glob.glob(os.path.join(NEWSLETTER_DIR, f"*.{ext}")):
            fname = os.path.basename(path)
            m = re.match(rf"(\d{{4}})-Q([1-4])\.{ext}$", fname, re.I)
            if not m:
                continue
            y, q = int(m.group(1)), int(m.group(2))
            kind = " · PDF" if ext == "pdf" else ""
            items.append({
                "title": f"{y}년 {q}분기 소식지",
                "sub": f"{y}. {q}분기 · 한국자동차환경협회 뉴스레터{kind}",
                "file": fname,
                "sort_key": y * 10 + q,
            })
    # 같은 분기에 html·pdf 가 모두 있으면 PDF 를 우선한다
    best = {}
    for it in items:
        k = it["sort_key"]
        if k not in best or it["file"].lower().endswith(".pdf"):
            best[k] = it
    out = sorted(best.values(), key=lambda x: x["sort_key"], reverse=True)
    return out


def scan_global():
    """global/ 폴더의 해외뉴스 모니터링 파일을 훑어 목록 생성 (자동 감지).
    담당자가 직접 작성한 파일을 올리면 목록에 나타난다.

    파일명 규칙 (둘 다 인식):
      · YYYY-MM-DD.html  예) 2026-07-27.html → "2026년 7월 27일 해외뉴스 모니터링"
      · YYYY-MM.html     예) 2026-07.html    → "2026년 7월 해외뉴스 모니터링"

    ⚠️ 워드·한글에서 저장한 HTML은 URL이 글자로만 적혀 눌러도 이동하지 않고,
       줄이 임의로 쪼개져 읽기 어렵다. 그래서 정리본을 따로 만들어 그것을 연다.
       원본 파일은 손대지 않는다. (global/view/ 에 정리본 생성)
    """
    items = []
    view_dir = os.path.join(GLOBAL_DIR, "view")
    os.makedirs(view_dir, exist_ok=True)
    converted = 0

    for path in glob.glob(os.path.join(GLOBAL_DIR, "*.html")):
        fname = os.path.basename(path)

        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\.html$", fname)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            title = f"{y}년 {mo}월 {d}일 해외뉴스 모니터링"
            sub = f"{y}.{mo:02d}.{d:02d} · EU·미국·중국 등 글로벌 동향"
            key = y * 10000 + mo * 100 + d
        else:
            m = re.match(r"(\d{4})-(\d{2})\.html$", fname)
            if not m:
                continue
            y, mo = int(m.group(1)), int(m.group(2))
            title = f"{y}년 {mo}월 해외뉴스 모니터링"
            sub = f"{y}.{mo:02d} · EU·미국·중국 등 글로벌 동향"
            key = y * 10000 + mo * 100 + 99

        # 원본을 읽어 정리본을 만든다. 실패하면 원본을 그대로 연다.
        target = fname
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            page = global_convert.convert(raw, title, sub)
            if page:
                with open(os.path.join(view_dir, fname), "w", encoding="utf-8") as f:
                    f.write(page)
                target = f"view/{fname}"
                converted += 1
        except Exception as ex:
            print(f"      [해외뉴스 정리 실패 - 원본 사용] {fname}: {ex}")

        items.append({"title": title, "sub": sub, "file": target, "sort_key": key})

    # PDF 로 올린 경우: 브라우저가 바로 열어주므로 변환하지 않는다
    for path in glob.glob(os.path.join(GLOBAL_DIR, "*.pdf")):
        fname = os.path.basename(path)
        m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?\.pdf$", fname)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) else None
        if d:
            title = f"{y}년 {mo}월 {d}일 해외뉴스 모니터링"
            sub = f"{y}.{mo:02d}.{d:02d} · PDF"
            key = y * 10000 + mo * 100 + d
        else:
            title = f"{y}년 {mo}월 해외뉴스 모니터링"
            sub = f"{y}.{mo:02d} · PDF"
            key = y * 10000 + mo * 100 + 99
        items.append({"title": title, "sub": sub, "file": fname, "sort_key": key})

    if converted:
        print(f"        해외뉴스 {converted}건 정리본 생성 (링크 클릭 가능)")
    items.sort(key=lambda x: x["sort_key"], reverse=True)
    return items


def build_search_index():
    """모든 날짜 JSON에서 기사를 모아 검색 인덱스 생성.
    자동 수집(data/)과 스티비 발행분(mail_data/)을 모두 훑어,
    소스를 전환해도 과거 기사가 검색에서 사라지지 않게 한다.
    같은 URL은 한 번만 넣고, 현재 소스(ACTIVE_DATA_DIR)를 우선한다.
    검색 결과가 최신 뉴스부터 보이도록 최신 날짜 파일부터 읽는다."""
    index = []
    seen_urls = set()

    # 현재 소스를 먼저 읽어 우선권을 준다
    dirs = [ACTIVE_DATA_DIR] + [p for p in (DATA_DIR, MAIL_DATA_DIR) if p != ACTIVE_DATA_DIR]
    for folder in dirs:
        for path in sorted(glob.glob(os.path.join(folder, "*.json")), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:
                continue
            date = doc.get("date", "")
            for section in ["daily", "global"]:
                for cat_name, articles in (doc.get(section) or {}).items():
                    for art in articles or []:
                        url = (art.get("url") or "").strip()
                        if url and url in seen_urls:
                            continue      # 같은 기사가 양쪽 폴더에 있으면 한 번만
                        if url:
                            seen_urls.add(url)
                        index.append({
                            "title": art.get("title", ""),
                            "summary": art.get("summary", ""),
                            "date": date,
                            "pubdate": art.get("date", ""),
                            "category": cat_name,
                            "url": url,
                        })
    index.sort(key=lambda x: x.get("date", ""), reverse=True)
    return index


def build_index(today, archive_list, newsletter_list, search_index, global_list):
    date_kr = fmt_date_kr(today["date"], today["dow"])
    daily_sections = build_cat_sections(today["daily"], DAILY_ORDER)
    # 해외뉴스 모니터링: 담당자가 global/ 폴더에 올린 파일 목록 (소식지와 동일 방식)
    if global_list:
        global_sections = ""
        for g in global_list:
            global_sections += f"""  <div class="nl-row">
    <div>
      <div class="nl-title">{esc(g['title'])}</div>
      <div class="nl-sub">{esc(g['sub'])}</div>
    </div>
    <button class="nl-btn" onclick="openGlobal('{esc(g['file'])}')">열람 →</button>
  </div>
"""
    else:
        global_sections = '  <div class="sr-hint">등록된 해외뉴스 모니터링이 아직 없습니다.</div>\n'

    # 이전 웹진 목록
    archive_rows = ""
    for a in archive_list:
        date_dot = a["date"].replace("-", ".")
        archive_rows += f"""  <div class="archive-row" data-search="{esc((a.get('summary','') + ' ' + date_dot).lower())}">
    <div class="archive-date">{esc(date_dot)}</div>
    <div class="archive-title">제 {esc(a['no'])}호 · {esc(a.get('summary',''))}</div>
    <div><button class="archive-link" onclick="openEdition('{esc(a['date'])}')">열람 →</button></div>
  </div>
"""

    # 소식지 목록 (자동 감지)
    if newsletter_list:
        newsletter_rows = ""
        for nl in newsletter_list:
            newsletter_rows += f"""  <div class="nl-row">
    <div>
      <div class="nl-title">{esc(nl['title'])}</div>
      <div class="nl-sub">{esc(nl['sub'])}</div>
    </div>
    <button class="nl-btn" onclick="openNewsletter('{esc(nl['file'])}')">열람 →</button>
  </div>
"""
    else:
        newsletter_rows = '  <div class="sr-hint">등록된 소식지가 아직 없습니다.</div>\n'

    # 검색 인덱스 JSON (script 안전 처리: </ 이스케이프)
    search_json = json.dumps(search_index, ensure_ascii=False).replace("</", "<\\/")

    # 카테고리 옵션 (검색 필터용)
    cat_options = "".join(f"<option>{esc(c)}</option>" for c in DAILY_ORDER + GLOBAL_ORDER)

    tmpl = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>한국자동차환경협회 웹진</title>
<style>@@CSS@@@@INDEX_CSS@@</style>
</head>
<body>

<header class="webzine-header">
  <div class="webzine-brand">KAEA NEWSLETTER</div>
  <h1 class="webzine-title">데일리 뉴스 모니터링</h1>
  <p class="webzine-sub">한국자동차환경협회 웹진</p>
  <div class="webzine-date">@@DATE_KR@@ · 제 @@NO@@호</div>
</header>

<div class="member-notice">
  본 웹진은 한국자동차환경협회 회원사를 위한 뉴스 모니터링 서비스입니다.
</div>

<nav class="tabs">
  <div class="tab active" onclick="go('daily')">데일리 모니터링</div>
  <div class="tab" onclick="go('global')">해외뉴스 모니터링</div>
  <div class="tab" onclick="go('newsletter')">소식지</div>
  <div class="tab" onclick="go('search')">이전 뉴스 검색</div>
  <div class="tab" onclick="go('archive')">이전 웹진 보기</div>
</nav>

<main>

<section class="page active" id="daily">
@@DAILY@@</section>

<section class="page" id="global">
  <div class="info-box">
    <strong>해외뉴스 모니터링</strong> · EU·미국·중국 등 주요국의 자동차 환경 정책과 글로벌 시장 동향입니다. 열람 버튼을 누르면 해당 회차를 보실 수 있습니다.
  </div>
@@GLOBAL@@</section>

<section class="page" id="newsletter">
  <div class="info-box">
    <strong>소식지</strong> · 한국자동차환경협회가 분기별로 발행하는 소식지입니다. 열람 버튼을 누르면 해당 분기 소식지를 보실 수 있습니다.
  </div>
@@NEWSLETTER@@</section>

<section class="page" id="search">
  <div class="info-box">
    <strong>이전 뉴스 검색</strong> · 지금까지 수집된 모든 기사를 키워드로 검색합니다. 기사 제목을 누르면 원문으로, 날짜를 누르면 해당 날짜의 웹진으로 이동합니다.
  </div>
  <div class="search-bar">
    <span class="search-label">검색</span>
    <input type="text" class="search-input" id="newsSearchInput" placeholder="키워드 입력 (예: 전기차 충전)" onkeydown="if(event.key==='Enter')runNewsSearch()">
    <select class="search-select" id="newsSearchCat">
      <option>전체 카테고리</option>
      @@CAT_OPTIONS@@
    </select>
    <button class="search-btn" onclick="runNewsSearch()">검색</button>
  </div>
  <div id="searchResults"><div class="sr-hint">검색어를 입력하고 검색 버튼을 눌러주세요.</div></div>
</section>

<section class="page" id="archive">
  <div class="info-box">
    <strong>이전 웹진 보기</strong> · 과거 발송된 날짜별 웹진을 조회합니다. 아래 검색창으로 날짜·주제를 걸러볼 수 있습니다.
  </div>
  <div class="search-bar">
    <span class="search-label">검색</span>
    <input type="text" class="search-input" id="archiveSearchInput" placeholder="주제·날짜로 검색 (예: 전기차, 07.08)" oninput="filterArchive()">
  </div>
  <div id="archiveList">
@@ARCHIVE@@  </div>
  <div id="archiveEmpty" class="sr-hint" style="display:none;">검색 결과가 없습니다.</div>
</section>

</main>

<footer class="footer">
  <strong>한국자동차환경협회 (KAEA)</strong><br>
  본 뉴스 웹진은 협회 회원사를 위한 일일 모니터링 서비스로, 매일 오전 자동 갱신됩니다.<br>
  본 자료의 무단 복제·배포를 금합니다.
</footer>

<script>
  var SEARCH_INDEX = @@SEARCH_JSON@@;

  function go(id) {
    document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
    document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
    var tabs = document.querySelectorAll('.tab');
    var ids = ['daily', 'global', 'newsletter', 'search', 'archive'];
    var idx = ids.indexOf(id);
    if (idx >= 0) tabs[idx].classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function openEdition(date) {
    window.location.href = 'news/' + date + '.html';
  }

  function openNewsletter(file) {
    window.location.href = 'newsletter/' + file;
  }

  function openGlobal(file) {
    window.location.href = 'global/' + file;
  }

  function openArticle(e) {
    if (e && e.preventDefault) e.preventDefault();
    alert('이 기사는 원문 링크가 아직 연결되지 않았습니다.');
    return false;
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function runNewsSearch() {
    var q = document.getElementById('newsSearchInput').value.trim().toLowerCase();
    var cat = document.getElementById('newsSearchCat').value;
    var box = document.getElementById('searchResults');
    if (!q) { box.innerHTML = '<div class="sr-hint">검색어를 입력해주세요.</div>'; return; }
    var results = SEARCH_INDEX.filter(function(item) {
      var hay = (item.title + ' ' + item.summary).toLowerCase();
      var matchQ = hay.indexOf(q) !== -1;
      var matchC = (cat === '전체 카테고리' || item.category === cat);
      return matchQ && matchC;
    });
    if (!results.length) { box.innerHTML = '<div class="sr-hint">"' + esc(q) + '" 검색 결과가 없습니다.</div>'; return; }
    var html = '<div style="font-size:12px;color:#888;margin-bottom:12px;font-weight:600;">총 ' + results.length + '건</div>';
    results.forEach(function(r) {
      var titleHtml = r.url
        ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.title) + '</a>'
        : esc(r.title);
      html += '<div class="sr-item">'
        + '<div class="sr-title">' + titleHtml + '</div>'
        + '<div class="sr-meta"><span class="sr-cat">' + esc(r.category) + '</span>'
        + '<a class="sr-datelink" onclick="openEdition(\\'' + r.date + '\\')">📅 ' + esc(r.date) + ' 웹진</a></div>'
        + '<p class="sr-summary">' + esc(r.summary) + '</p>'
        + '</div>';
    });
    box.innerHTML = html;
  }

  function filterArchive() {
    var q = document.getElementById('archiveSearchInput').value.trim().toLowerCase();
    var rows = document.querySelectorAll('#archiveList .archive-row');
    var shown = 0;
    rows.forEach(function(row) {
      var key = row.getAttribute('data-search') || '';
      if (!q || key.indexOf(q) !== -1) { row.style.display = ''; shown++; }
      else { row.style.display = 'none'; }
    });
    document.getElementById('archiveEmpty').style.display = shown ? 'none' : 'block';
  }
</script>

</body>
</html>
"""

    result = (tmpl
              .replace("@@CSS@@", CSS)
              .replace("@@INDEX_CSS@@", INDEX_EXTRA_CSS)
              .replace("@@DATE_KR@@", esc(date_kr))
              .replace("@@NO@@", esc(today["no"]))
              .replace("@@DAILY@@", daily_sections)
              .replace("@@GLOBAL@@", global_sections)
              .replace("@@NEWSLETTER@@", newsletter_rows)
              .replace("@@CAT_OPTIONS@@", cat_options)
              .replace("@@ARCHIVE@@", archive_rows)
              .replace("@@SEARCH_JSON@@", search_json))
    return result


def backfill_edition_pages(skip_date=""):
    """데이터 파일은 있는데 날짜별 웹진 페이지가 없는 회차를 채운다.

    ⚠️ generate.py 는 실행할 때 지정한 날짜 하나만 news/날짜.html 을 만든다.
       과거 발행분을 한꺼번에 가져오면 데이터만 쌓이고 페이지가 없어,
       '이전 웹진 보기'에서 열람 시 404 가 난다.
       그래서 매번 빠진 회차를 찾아 채운다.
    반환: 새로 만든 개수"""
    made = 0
    for path in sorted(glob.glob(os.path.join(ACTIVE_DATA_DIR, "*.json"))):
        name = os.path.basename(path).replace(".json", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", name) or name == skip_date:
            continue
        out = os.path.join(NEWS_DIR, f"{name}.html")
        if os.path.exists(out):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if not (doc.get("daily") or doc.get("global")):
                continue
            with open(out, "w", encoding="utf-8") as f:
                f.write(build_edition_page(doc))
            made += 1
        except Exception as e:
            print(f"        [회차 페이지 생성 실패] {name}: {e}")
    return made


def update_archive(ed):
    """이전 웹진 목록(archive.json) 갱신.

    ⚠️ 예전에는 '오늘 날짜 항목'만 갱신해서, 요약 생성 방식을 고쳐도
       이미 등록된 과거 항목은 잘못된 요약이 그대로 남았다.
       이제 데이터 파일을 훑어 요약을 다시 채운다(자체 복구).

    안전장치
      · 요약이 비어 있는 파일은 건너뛴다 (수집 직후처럼 아직 요약 전인 경우)
      · 데이터 파일이 사라진 과거 항목은 기존 값을 그대로 둔다
    """
    archive = []
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                archive = json.load(f)
        except Exception:
            archive = []
    by_date = {a.get("date"): a for a in archive if a.get("date")}

    # 데이터 파일에 담긴 요약으로 과거 항목까지 다시 채운다
    refreshed = 0
    for path in sorted(glob.glob(os.path.join(ACTIVE_DATA_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
        d = doc.get("date")
        summary = (doc.get("summary") or "").strip()
        if not d or not summary:
            continue                      # 요약이 없으면 기존 값을 지키기 위해 건너뜀
        prev = by_date.get(d, {}).get("summary", "")
        by_date[d] = {"no": doc.get("no", ""), "date": d, "summary": summary}
        if prev and prev != summary:
            refreshed += 1

    # 이번에 만든 회차는 항상 반영
    by_date[ed["date"]] = {"no": ed["no"], "date": ed["date"],
                           "summary": ed.get("summary", "")}

    archive = sorted(by_date.values(), key=lambda a: a["date"], reverse=True)
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    if refreshed:
        print(f"        과거 회차 요약 {refreshed}건 갱신")
    return archive


def main():
    src_label = "스티비 발행분(mail_data)" if WEBZINE_SOURCE == "stibee" else "자동 수집(data)"
    print(f"[소스] {src_label}")

    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        files = sorted(glob.glob(os.path.join(ACTIVE_DATA_DIR, "*.json")))
        if not files:
            print(f"[오류] {os.path.basename(ACTIVE_DATA_DIR)} 폴더에 JSON 파일이 없습니다.")
            if WEBZINE_SOURCE == "stibee":
                print("  스티비 미러 모드입니다. 먼저 stibee_mirror.py 를 실행해 메일을 가져오세요.")
            sys.exit(1)
        date_str = os.path.basename(files[-1]).replace(".json", "")

    data_path = os.path.join(ACTIVE_DATA_DIR, f"{date_str}.json")
    if not os.path.exists(data_path):
        print(f"[오류] 데이터 파일이 없습니다: {data_path}")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        ed = json.load(f)

    print(f"[1/5] 데이터 로드: {date_str} 제{ed['no']}호")

    edition_html = build_edition_page(ed)
    with open(os.path.join(NEWS_DIR, f"{date_str}.html"), "w", encoding="utf-8") as f:
        f.write(edition_html)
    print(f"[2/5] 날짜별 웹진 생성: news/{date_str}.html")

    # 과거 회차 중 페이지가 없는 것을 채운다 (열람 시 404 방지)
    filled = backfill_edition_pages(skip_date=date_str)
    if filled:
        print(f"        빠진 회차 페이지 {filled}건 생성")

    archive = update_archive(ed)
    print(f"[3/5] 이전 웹진 목록 갱신: 총 {len(archive)}건")

    newsletter_list = scan_newsletters()
    global_list = scan_global()
    search_index = build_search_index()
    print(f"[4/5] 소식지 {len(newsletter_list)}건 / 해외뉴스 {len(global_list)}건 감지 / 검색 인덱스 {len(search_index)}건")

    index_html = build_index(ed, archive, newsletter_list, search_index, global_list)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(index_html)
    print(f"[5/6] index.html 재생성 완료 (5개 탭)")

    # ── 대시보드 생성 (협회 담당자용) ──
    # dashboard.py 와 keywords.py 가 같은 폴더에 있어야 함.
    # 셋 중 하나라도 없으면 대시보드는 건너뛴다(웹진 생성은 정상 완료).
    try:
        from dashboard import build_dashboard
        from keywords import KEYWORDS, MEMBER_COMPANIES

        stats = []
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                stats = []

        dash_html = build_dashboard(ed, stats, (KEYWORDS, MEMBER_COMPANIES))
        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            f.write(dash_html)
        print(f"[6/6] dashboard.html 생성 완료 (협회 운영용)")
    except Exception as e:
        print(f"[6/6] 대시보드 생성 건너뜀: {e}")

    print(f"\n완료: {date_str} 웹진 + 대시보드 생성")


if __name__ == "__main__":
    main()
