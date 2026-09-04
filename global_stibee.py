"""
====================================================================
해외뉴스 모니터링 — 스티비 메일 변환
====================================================================
역할: 협회가 스티비로 발행한 '해외 기관 및 시장 동향 뉴스레터'를
      웹진 해외뉴스 탭에서 볼 수 있는 페이지로 만든다.

왜 이 방식이 좋은가
  담당자가 이미 한국어로 번역·요약을 끝낸 상태라
  AI 호출이 필요 없다. 비용 0원, 처리 즉시.

메일 구조 (실측)
  해외 기관 및 시장 동향 뉴스레터
  Vol.95
  2026.8.21(금)
  [일반(배출가스·온실가스 등)]        ← 카테고리
  뉴스                                ← 하위 구분
  1.제목 [[URL]]
  [8.18/Press TV] 요약 문장
  ...
  보고서                              ← 하위 구분
  1.제목 [[URL]]
  [8.17/icct] 요약 문장

원본은 건드리지 않는다.
  global/view/2026-08-21.html   변환본 (웹진에서 열림)

실행
  python global_stibee.py https://stibee.com/api/v1.0/emails/share/XXXX
  python global_stibee.py URL --date 2026-08-21
====================================================================
"""
import os
import re
import sys
import html as html_mod
import datetime

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
GLOBAL_DIR = os.path.join(ROOT, "global")
VIEW_DIR = os.path.join(GLOBAL_DIR, "view")

KST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 스티비 링크만 허용한다 (임의 주소 요청 방지)
ALLOWED = re.compile(r"^https?://(stib\.ee|(www\.)?stibee\.com)/", re.I)

URL_MARK = re.compile(r"\[\[(https?://[^\]]+)\]\]")
NUM_HEAD = re.compile(r"^(\d+)\s*\.\s*(.+)$")
META_HEAD = re.compile(r"^\[\s*([\d.]+)\s*/\s*([^\]]+?)\s*\]\s*(.*)$")
CAT_LINE = re.compile(r"^\[([^\]\d][^\]]*)\]$")

# 본문이 아닌 줄 (안내·푸터)
SKIP_HINT = ("※", "구독하기", "수신거부", "Unsubscribe", "☎",
             "이메일 내용이 깨져", "회신 주시면", "번역 서비스")


def fetch(url):
    """스티비 공개 링크에서 메일 HTML을 가져온다."""
    url = (url or "").strip()
    if not ALLOWED.match(url):
        print(f"[오류] 스티비 링크가 아닙니다: {url}")
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
    except Exception as e:
        print(f"[연결 실패] {e}")
        return ""
    if r.status_code != 200:
        print(f"[오류] HTTP {r.status_code} · 링크를 열 수 없습니다.")
        return ""
    r.encoding = r.apparent_encoding or "utf-8"
    print(f"  링크에서 HTML {len(r.text):,}자 받음")
    return r.text


def to_lines(raw):
    """메일 HTML에서 읽는 줄만 뽑는다. 링크는 [[URL]] 로 남긴다."""
    t = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>", " ", raw)
    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    t = re.sub(
        r'(?is)<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f" {re.sub(r'(?s)<[^>]+>', '', m.group(2)).strip()} [[{m.group(1).strip()}]] ",
        t)
    t = re.sub(r"(?i)<(br|/p|/div|/tr|/td|/h[1-6]|/li)\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html_mod.unescape(t).replace("\u200b", " ").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


def parse(lines):
    """줄 목록 → (제목정보, 섹션 목록)
    섹션: {"cat": 카테고리, "groups": [{"name": 뉴스/보고서, "items": [...]}]}"""
    info = {"vol": "", "date_txt": "", "lead": ""}
    sections = []
    cur_sec = cur_grp = cur_item = None

    for ln in lines:
        if any(h in ln for h in SKIP_HINT):
            cur_item = None
            continue

        m = re.match(r"^Vol\.\s*(\d+)", ln, re.I)
        if m:
            info["vol"] = m.group(1)
            continue
        m = re.match(r"^(20\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", ln)
        if m and not info["date_txt"]:
            info["date_txt"] = ln
            continue
        if "동향을 전달드립니다" in ln:
            info["lead"] = ln
            continue

        m = CAT_LINE.match(ln)
        if m:
            cur_sec = {"cat": m.group(1).strip(), "groups": []}
            sections.append(cur_sec)
            cur_grp = cur_item = None
            continue

        if ln in ("뉴스", "보고서") and cur_sec is not None:
            cur_grp = {"name": ln, "items": []}
            cur_sec["groups"].append(cur_grp)
            cur_item = None
            continue

        m = NUM_HEAD.match(ln)
        if m and URL_MARK.search(ln):
            title = m.group(2)
            u = URL_MARK.search(title)
            url = u.group(1)
            title = URL_MARK.sub("", title).strip(" ·-")
            if cur_sec is None:
                cur_sec = {"cat": "해외 뉴스", "groups": []}
                sections.append(cur_sec)
            if cur_grp is None:
                cur_grp = {"name": "뉴스", "items": []}
                cur_sec["groups"].append(cur_grp)
            cur_item = {"no": m.group(1), "title": title, "url": url,
                        "date": "", "src": "", "summary": ""}
            cur_grp["items"].append(cur_item)
            continue

        m = META_HEAD.match(ln)
        if m and cur_item is not None:
            cur_item["date"] = m.group(1).strip(".")
            cur_item["src"] = m.group(2).strip()
            cur_item["summary"] = m.group(3).strip()
            continue

        # 요약이 다음 줄로 이어지는 경우
        if cur_item is not None and cur_item["summary"]:
            cur_item["summary"] = (cur_item["summary"] + " " + ln).strip()

    # 기사가 없는 그룹·섹션 정리
    for s in sections:
        s["groups"] = [g for g in s["groups"] if g["items"]]
    return info, [s for s in sections if s["groups"]]


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
    margin: 0; background: #eef1f5; color: #1f2937;
    font-family: 'Pretendard','Malgun Gothic','맑은 고딕',-apple-system,BlinkMacSystemFont,sans-serif;
    line-height: 1.75; -webkit-text-size-adjust: 100%;
  }
  .wrap { max-width: 820px; margin: 0 auto; background: #fff;
          box-shadow: 0 2px 12px rgba(0,0,0,.07); }
  .top { background: #1e3a5f; color: #fff; padding: 32px 36px 28px; }
  .top .brand { font-size: 11px; letter-spacing: 2.5px; opacity: .7; font-weight: 600; }
  .top h1 { margin: 10px 0 6px; font-size: 25px; font-weight: 700; letter-spacing: -.4px; }
  .top .sub { font-size: 13px; opacity: .82; }
  .back { padding: 13px 36px; background: #f7f9fc; border-bottom: 1px solid #e5eaf0; }
  .back a { color: #1e3a5f; text-decoration: none; font-size: 13px; font-weight: 600; }
  .back a:hover { text-decoration: underline; }
  .cat { background: #f0f3f8; color: #1e3a5f; padding: 14px 36px;
         font-size: 15px; font-weight: 700; letter-spacing: -.2px;
         border-top: 1px solid #e5eaf0; border-bottom: 1px solid #e5eaf0; }
  .grp { padding: 16px 36px 4px; font-size: 13px; font-weight: 700; color: #3b5378; }
  .grp span { display: inline-block; background: #eaeff6; padding: 3px 11px; border-radius: 4px; }
  .item { padding: 18px 36px; border-bottom: 1px solid #f1f3f6; }
  .item:last-child { border-bottom: none; }
  .t { font-size: 16.5px; font-weight: 700; line-height: 1.5; margin: 0 0 9px; letter-spacing: -.3px; }
  .t .n { color: #8892a0; margin-right: 4px; }
  .t a { color: #16233a; text-decoration: none; }
  .t a:hover { color: #2563eb; text-decoration: underline; }
  .meta { font-size: 12px; color: #7a828e; margin-bottom: 10px; }
  .meta .badge { display: inline-block; background: #eaeff6; color: #3b5378;
                 padding: 3px 9px; border-radius: 4px; margin-right: 8px;
                 font-weight: 700; letter-spacing: .2px; }
  .s { font-size: 14.5px; color: #3d4553; line-height: 1.8;
       padding-left: 13px; border-left: 3px solid #e5eaf0; }
  .link { margin-top: 11px; font-size: 12px; }
  .link a { color: #8892a0; text-decoration: none; word-break: break-all; }
  .link a:hover { color: #2563eb; text-decoration: underline; }
  .foot { padding: 26px 36px; text-align: center; color: #9aa3af;
          font-size: 12px; background: #f7f9fc; border-top: 1px solid #e5eaf0; }
  @media (max-width: 640px) {
    .top, .cat, .item, .back, .foot, .grp { padding-left: 18px; padding-right: 18px; }
    .top { padding-top: 24px; padding-bottom: 20px; }
    .top h1 { font-size: 20px; }
    .item { padding-top: 16px; padding-bottom: 16px; }
    .t { font-size: 15.5px; }
    .s { font-size: 14px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">KAEA NEWSLETTER</div>
    <h1>@@TITLE@@</h1>
    <div class="sub">@@SUB@@</div>
  </div>
  <div class="back"><a href="../../index.html">← 웹진으로 돌아가기</a></div>
@@BODY@@
  <div class="foot">한국자동차환경협회 (KAEA)</div>
</div>
</body>
</html>
"""


def render(info, sections, title, sub):
    body, n = "", 0
    for sec in sections:
        body += f'  <div class="cat">{_esc(sec["cat"])}</div>\n'
        for grp in sec["groups"]:
            body += f'  <div class="grp"><span>{_esc(grp["name"])}</span></div>\n'
            for a in grp["items"]:
                n += 1
                meta = ""
                if a.get("date"):
                    meta += f'<span class="badge">{_esc(a["date"])}</span>'
                if a.get("src"):
                    meta += _esc(a["src"])
                body += '  <div class="item">\n'
                body += (f'    <div class="t"><span class="n">{_esc(a["no"])}.</span>'
                         f'<a href="{_esc(a["url"])}" target="_blank" rel="noopener">'
                         f'{_esc(a["title"])}</a></div>\n')
                if meta:
                    body += f'    <div class="meta">{meta}</div>\n'
                if a.get("summary"):
                    body += f'    <div class="s">{_esc(a["summary"])}</div>\n'
                body += (f'    <div class="link"><a href="{_esc(a["url"])}" '
                         f'target="_blank" rel="noopener">{_esc(a["url"])}</a></div>\n')
                body += '  </div>\n'
    if n == 0:
        return "", 0
    html = (TEMPLATE.replace("@@TITLE@@", _esc(title))
                    .replace("@@SUB@@", _esc(sub))
                    .replace("@@BODY@@", body))
    return html, n


def guess_date(info):
    """메일에 적힌 발행일(2026.8.21(금))에서 날짜를 뽑는다."""
    m = re.match(r"^(20\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", info.get("date_txt", ""))
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)),
                                 int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python global_stibee.py <스티비 웹에서보기 링크> [--date YYYY-MM-DD]")
        sys.exit(1)
    url = args[0]

    date_arg = ""
    if "--date" in sys.argv:
        i = sys.argv.index("--date")
        date_arg = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        if date_arg and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_arg):
            print(f"[오류] 날짜 형식이 올바르지 않습니다: {date_arg} (예: 2026-08-21)")
            sys.exit(1)

    raw = fetch(url)
    if not raw:
        sys.exit(1)

    info, sections = parse(to_lines(raw))
    date_str = date_arg or guess_date(info)
    vol = info.get("vol", "")

    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    title = f"{d.year}년 {d.month}월 {d.day}일 해외뉴스 모니터링"
    sub = f"{date_str.replace('-', '.')}"
    if vol:
        sub += f" · Vol.{vol}"
    sub += " · 해외 기관 및 시장 동향"

    html, n = render(info, sections, title, sub)
    if n == 0:
        print("[중단] 기사를 찾지 못했습니다. 링크나 메일 형식을 확인하세요.")
        sys.exit(1)

    print(f"  발행일: {date_str}" + (f" (Vol.{vol})" if vol else ""))
    for sec in sections:
        cnt = sum(len(g["items"]) for g in sec["groups"])
        detail = " / ".join(f"{g['name']} {len(g['items'])}건" for g in sec["groups"])
        print(f"    [{sec['cat']}] {cnt}건  ({detail})")

    os.makedirs(VIEW_DIR, exist_ok=True)
    out = os.path.join(VIEW_DIR, f"{date_str}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  저장: global/view/{date_str}.html (총 {n}건)")

    # 목록에 뜨도록 원본 자리에 표시 파일을 남긴다
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    marker = os.path.join(GLOBAL_DIR, f"{date_str}.html")
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  저장: global/{date_str}.html (목록 감지용)")

    gh = os.getenv("GITHUB_OUTPUT")
    if gh:
        try:
            with open(gh, "a", encoding="utf-8") as f:
                f.write(f"converted={n}\ndate={date_str}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
