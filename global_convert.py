"""
====================================================================
해외뉴스 모니터링 자동 변환
====================================================================
역할: 담당자가 워드·한글에서 저장한 HTML을 읽어
      웹진과 같은 디자인의 깔끔한 페이지로 다시 만든다.

왜 필요한가
  · 문서 편집기 저장본은 URL이 글자로만 있어 눌러도 이동하지 않는다
  · 한 문장이 여러 줄로 쪼개지고 글자 사이에 공백이 끼어 읽기 어렵다
  · 파일마다 카테고리 표기가 달라 형태가 제각각이다

담당자 작업 방식은 그대로 두고, 보여지는 결과만 통일한다.

원본은 건드리지 않는다.
  global/2026-06-26.html          담당자 원본 (보존)
  global/view/2026-06-26.html     자동 변환본 (웹진에서 열림)

문서 구조 (실측)
  카테고리명
  제목 (여러 줄로 쪼개질 수 있음)
  https://...
  [2026.06.22 / Sustainable Bus]
  요약 (여러 줄로 쪼개질 수 있음)
====================================================================
"""
import os
import re
import html as html_mod

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# [2026.06.22 / Sustainable Bus] 형태. 문서 저장 과정에서 대괄호가 깨질 수 있어
# 앞뒤 괄호는 있어도 없어도 인식하되, 날짜 형식은 반드시 있어야 한다.
META_RE = re.compile(
    r"^[\[\]\(\)]*\s*(\d{4}\s*[.\-]\s*\d{1,2}\s*[.\-]\s*\d{1,2})\s*/\s*([^\]\)]+?)\s*[\]\)]*$")

# 요약 문장이 끝났는지 판정.
# ⚠️ 마침표만 보면 날짜(2026.06.22)나 약어에서 오작동한다.
#    한국어 종결어미로 끝나는지를 함께 본다.
SENT_END_RE = re.compile(r"(다|음|임|함|됨|이다|한다|였다|겠다)\s*[.。]?\s*$")


def to_text_lines(raw_html):
    """문서 저장본 HTML에서 사람이 읽는 줄만 뽑는다."""
    t = raw_html
    t = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    t = re.sub(r"(?i)<(br|/p|/div|/tr|/td|/h[1-6]|/li)\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    t = t.replace("\u200b", " ").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


def tidy(text):
    """워드 저장본에서 생긴 불필요한 공백을 정리한다.
    예) "2026 년 1 분기" → "2026년 1분기"
        "위통 · 솔라리스" → "위통·솔라리스"
        "확인 ." → "확인." """
    s = text
    # 숫자와 한글 단위 사이 공백 제거
    s = re.sub(r"(\d)\s+(년|월|일|분기|위|개|건|억|만|원|％|%|명|대|배|차|시|분)", r"\1\2", s)
    # 문장부호 앞 공백 제거
    s = re.sub(r"\s+([,.…‥·、。%)\]}])", r"\1", s)
    # 여는 괄호·따옴표 뒤 공백 제거
    s = re.sub(r"([(\[{“‘\"])\s+", r"\1", s)
    # 닫는 따옴표 앞 공백 제거
    s = re.sub(r"\s+([”’])", r"\1", s)
    # 가운뎃점 양옆 공백 제거
    s = re.sub(r"\s*·\s*", "·", s)
    # 슬래시 양옆 공백 정리
    s = re.sub(r"\s*/\s*", " / ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


# 카테고리 헤더로 인정할 표현. 실측한 표기를 기준으로 한다.
#   "일반 ( 배출가스 등 )", "수소차", "배출가스 / 일반", "전기차" 등
# ⚠️ 워드 저장본은 문장 중간에도 줄바꿈이 들어가, '짧은 줄 = 카테고리'로 보면
#    제목·요약 조각까지 카테고리로 잘못 잡힌다. 그래서 아래 두 조건을 모두 본다.
#      ① 알려진 주제어로만 이루어져 있을 것
#      ② 문장부호(쉼표·마침표)로 이어지는 문장이 아닐 것
CATEGORY_WORDS = [
    "일반", "배출가스", "전기차", "수소차", "수소", "배터리",
    "충전", "정책", "규제", "시장", "기술", "산업", "기타",
]
_CAT_STRIP = re.compile(r"[()\[\]{}·/,\s등]")


def looks_like_category(line):
    """이 줄이 카테고리 헤더인지 판정."""
    s = line.strip()
    if not s or len(s) > 24:
        return False
    if URL_RE.search(s) or s.endswith((".", "다", "임", "함", "됨")):
        return False
    if re.search(r"\d{4}|%|:", s):          # 연도·비율·콜론이 있으면 제목일 가능성
        return False
    core = _CAT_STRIP.sub("", s)
    if not core:
        return False
    # 남은 글자가 전부 주제어 조합이어야 카테고리로 인정
    rest = core
    for w in sorted(CATEGORY_WORDS, key=len, reverse=True):
        rest = rest.replace(w, "")
    return rest == ""


def parse(lines):
    """줄 목록을 카테고리·기사 구조로 해석한다.

    ⚠️ 워드 저장본은 한 문장이 여러 줄로 쪼개져 줄 단위 판단이 통하지 않는다.
       그래서 'URL 줄'을 기준점으로 삼는다.
         URL 앞  = 제목
         URL 뒤  = [날짜 / 출처] 그리고 요약
       카테고리 헤더는 위 조건을 만족하는 줄만 인정한다.

    반환: [{"name": 카테고리, "items": [{title, url, date, source, summary}]}]
    """
    # 1) URL 위치를 모두 찾는다
    url_at = [i for i, ln in enumerate(lines) if URL_RE.search(ln)]
    if not url_at:
        return []

    sections = []
    cur = None

    def section(name):
        nonlocal cur
        cur = {"name": tidy(name), "items": []}
        sections.append(cur)
        return cur

    prev_end = 0        # 직전 기사의 요약이 끝난 줄 다음
    for k, ui in enumerate(url_at):
        head_lines = lines[prev_end:ui]

        # 제목 앞부분에서 카테고리 헤더를 걷어낸다
        title_parts = []
        for ln in head_lines:
            if looks_like_category(ln):
                section(ln)
                title_parts = []          # 카테고리 뒤부터 제목 시작
                continue
            title_parts.append(ln)

        url_line = lines[ui]
        m = URL_RE.search(url_line)
        head_in_line = url_line[:m.start()].strip()
        if head_in_line:
            title_parts.append(head_in_line)

        art = {"title": tidy(" ".join(title_parts)), "url": m.group(0),
               "date": "", "source": "", "summary": ""}

        # URL 다음 줄부터 다음 URL 전까지가 메타 + 요약
        # ⚠️ 줄 번호로 직접 다뤄야 한다. 메타 줄을 건너뛴 목록의 위치로 계산하면
        #    다음 기사 시작 지점이 어긋나 요약이 제목에 섞인다.
        nxt = url_at[k + 1] if k + 1 < len(url_at) else len(lines)
        summary = []
        end_at = nxt                    # 다음 기사 제목이 시작되는 줄
        for j in range(ui + 1, nxt):
            ln = lines[j]
            mm = META_RE.match(ln)
            if mm and not art["date"]:
                art["date"] = re.sub(r"\s+", "", mm.group(1))
                art["source"] = mm.group(2).strip()
                continue
            summary.append(ln)
            # 종결어미로 끝나면 요약이 끝난 것으로 본다.
            # 그 다음 줄부터는 다음 기사 제목이다.
            if SENT_END_RE.search(ln):
                end_at = j + 1
                break
        art["summary"] = tidy(" ".join(summary))

        if art["title"]:
            (cur or section("해외 뉴스"))["items"].append(art)

        prev_end = end_at

    return [s for s in sections if s["items"]]


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@@TITLE@@</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: #eef1f5;
    color: #1f2937;
    font-family: 'Pretendard','Malgun Gothic','맑은 고딕',-apple-system,BlinkMacSystemFont,sans-serif;
    line-height: 1.75;
    -webkit-text-size-adjust: 100%;
  }
  .wrap { max-width: 820px; margin: 0 auto; background: #fff;
          box-shadow: 0 2px 12px rgba(0,0,0,.07); }

  /* 상단 */
  .top { background: #1e3a5f; color: #fff; padding: 32px 36px 28px; }
  .top .brand { font-size: 11px; letter-spacing: 2.5px; opacity: .7; font-weight: 600; }
  .top h1 { margin: 10px 0 6px; font-size: 25px; font-weight: 700; letter-spacing: -.4px; }
  .top .sub { font-size: 13px; opacity: .82; }

  .back { padding: 13px 36px; background: #f7f9fc; border-bottom: 1px solid #e5eaf0; }
  .back a { color: #1e3a5f; text-decoration: none; font-size: 13px; font-weight: 600; }
  .back a:hover { text-decoration: underline; }

  /* 카테고리 구분 */
  .cat { background: #f0f3f8; color: #1e3a5f; padding: 14px 36px;
         font-size: 15px; font-weight: 700; letter-spacing: -.2px;
         border-top: 1px solid #e5eaf0; border-bottom: 1px solid #e5eaf0; }

  /* 기사 */
  .item { padding: 24px 36px; border-bottom: 1px solid #f1f3f6; }
  .item:last-child { border-bottom: none; }

  .t { font-size: 17px; font-weight: 700; line-height: 1.5;
       margin: 0 0 10px; letter-spacing: -.3px; }
  .t a { color: #16233a; text-decoration: none; }
  .t a:hover { color: #2563eb; text-decoration: underline; }

  .meta { font-size: 12px; color: #7a828e; margin-bottom: 12px; }
  .meta .badge { display: inline-block; background: #eaeff6; color: #3b5378;
                 padding: 3px 9px; border-radius: 4px; margin-right: 8px;
                 font-weight: 700; letter-spacing: .2px; }

  .s { font-size: 14.5px; color: #3d4553; line-height: 1.8;
       padding-left: 13px; border-left: 3px solid #e5eaf0; }

  .link { margin-top: 12px; font-size: 12px; }
  .link a { color: #8892a0; text-decoration: none; word-break: break-all; }
  .link a:hover { color: #2563eb; text-decoration: underline; }

  .foot { padding: 26px 36px; text-align: center; color: #9aa3af;
          font-size: 12px; background: #f7f9fc; border-top: 1px solid #e5eaf0; }

  @media (max-width: 640px) {
    .top, .cat, .item, .back, .foot { padding-left: 18px; padding-right: 18px; }
    .top { padding-top: 24px; padding-bottom: 20px; }
    .top h1 { font-size: 20px; }
    .item { padding-top: 20px; padding-bottom: 20px; }
    .t { font-size: 16px; }
    .s { font-size: 14px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">KAEA NEWSLETTER</div>
    <h1>@@TITLE@@</h1>
    <div class="sub">EU·미국·중국 등 글로벌 자동차 환경 동향</div>
  </div>
  <div class="back"><a href="../../index.html">← 웹진으로 돌아가기</a></div>
@@BODY@@
  <div class="foot">한국자동차환경협회 (KAEA)</div>
</div>
</body>
</html>
"""


def render(raw_html, title):
    """문서 저장본 HTML → 웹진 스타일 페이지 HTML.
    반환: (html, 기사 수)"""
    sections = parse(to_text_lines(raw_html))
    body, n = "", 0
    for sec in sections:
        if not sec["items"]:
            continue
        body += f'  <div class="cat">{_esc(sec["name"])}</div>\n'
        for a in sec["items"]:
            n += 1
            meta = ""
            if a.get("date"):
                meta += f'<span class="badge">{_esc(a["date"])}</span>'
            if a.get("source"):
                meta += _esc(a["source"])
            body += '  <div class="item">\n'
            body += (f'    <div class="t"><a href="{_esc(a["url"])}" '
                     f'target="_blank" rel="noopener">{_esc(a["title"])}</a></div>\n')
            if meta:
                body += f'    <div class="meta">{meta}</div>\n'
            if a.get("summary"):
                body += f'    <div class="s">{_esc(a["summary"])}</div>\n'
            body += (f'    <div class="link"><a href="{_esc(a["url"])}" '
                     f'target="_blank" rel="noopener">{_esc(a["url"])}</a></div>\n')
            body += '  </div>\n'
    if n == 0:
        body = ('  <div class="item"><div class="s">'
                '내용을 불러오지 못했습니다. 원본 파일을 확인해 주세요.</div></div>\n')
    return TEMPLATE.replace("@@TITLE@@", _esc(title)).replace("@@BODY@@", body), n


def render_file(src_path, out_path, title):
    """파일 하나를 변환해 저장. 반환: 기사 수"""
    try:
        with open(src_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception as e:
        print(f"    [해외뉴스 변환 실패] {os.path.basename(src_path)}: {e}")
        return 0
    html, n = render(raw, title)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return n


def convert(raw_html, title, sub=""):
    """generate.py 가 호출하는 진입점.
    문서 저장본 HTML → 웹진 스타일 페이지 HTML.
    변환할 기사가 하나도 없으면 None 을 돌려 원본을 그대로 쓰게 한다."""
    html, n = render(raw_html, title)
    if n == 0:
        return None
    if sub:
        html = html.replace(
            '<div class="sub">EU·미국·중국 등 글로벌 자동차 환경 동향</div>',
            f'<div class="sub">{_esc(sub)}</div>')
    return html
