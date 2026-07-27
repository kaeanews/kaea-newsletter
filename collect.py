"""
====================================================================
한국자동차환경협회 뉴스 웹진 - 뉴스 수집 스크립트 (STEP 1)
====================================================================
역할: 네이버 검색 API로 뉴스를 가져와서,
      전날+당일 발행분 + 카테고리별 2차 필터를 거쳐 JSON으로 저장한다.
      (이미 발행한 기사는 URL로 제외해 중복 발행을 막는다)

수집 방식 (협회 기준표 반영):
  [일반 카테고리 - 해석 A]
    primary(주요 키워드)로 네이버 검색
    → 그 결과 중 general(일반 키워드)이 제목/요약에 있는 기사만 남김
    → general이 비어있으면 필터 없이 전부 통과 (협회/기타/해외)

  [회원사 카테고리]
    회사명 54개로 각각 검색
    → 결과 중 MEMBER_CONTEXT(자동차·충전·배출 등)가 있는 기사만 남김
    → 무관한 동명이의 뉴스 제거

공통:
  - 전날+당일 발행분만 남김 (KST 기준), 이미 발행한 기사는 제외
  - HTML 태그·특수문자 정제
  - 중복 제거(제목/URL)
  - 부정 뉴스 필터링은 STEP 2(filter.py). 여기선 안 함.

API 키:
  - 로컬: .env 의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
  - 자동: GitHub Secrets 의 동일 이름

라이브러리:
  pip install requests python-dotenv

사용법:
  python collect.py
====================================================================
"""
import os
import re
import json
import glob
import html
import time
import datetime
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from keywords import (
    KEYWORDS, GLOBAL_KEYWORDS,
    MEMBER_COMPANIES, MEMBER_CONTEXT,
)

# --------------------------------------------------------------------
# API 키
# --------------------------------------------------------------------
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("[오류] 네이버 API 키가 없습니다.")
    print("  로컬: .env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET")
    print("  자동: GitHub Secrets 에 동일 이름 등록")
    raise SystemExit(1)

# --------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
DISPLAY = 100
SORT = "date"
REQUEST_DELAY = 0.1
# 카테고리별로 AI 심사(filter.py)에 넘길 후보 기사 수.
# ※ 네이버에서 받아오는 건수(DISPLAY)와 다르며, 웹진 게시 건수와도 다르다.
#   여기서 뽑은 후보를 filter.py 가 중요도로 평가해 상위 몇 건만 게시한다.
MAX_PER_CATEGORY = 20

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY_KST = datetime.datetime.now(KST).date()
YESTERDAY_KST = TODAY_KST - datetime.timedelta(days=1)
# 수집할 날짜 폭(당일 포함). 3이면 당일 + 전날 + 그제.
# 담당자 수동 모니터링이 2~3일치를 훑는 것에 맞춘 값.
# 이미 발행한 기사는 URL로 제외되므로 중복 발행 걱정 없이 넓혀도 안전하다.
COLLECT_DAYS = 3
# 수집 대상 발행일: 당일 포함 최근 COLLECT_DAYS 일
#  · 당일  : 새벽~실행 시각 사이에 나온 기사
#  · 전날~ : 하루치 기사 전체
# ※ 같은 기사가 다음날 실행 때 또 잡히므로,
#   이미 발행한 기사(URL)를 제외해 중복 발행을 막는다. (load_published_urls 참고)
TARGET_DATES = {TODAY_KST - datetime.timedelta(days=i) for i in range(COLLECT_DAYS)}
# 이미 발행한 기사를 찾을 때 확인할 최근 data 파일 수
# 수집 폭보다 넉넉히 잡아야 이전에 실은 기사를 빠짐없이 걸러낸다.
LOOKBACK_FILES = COLLECT_DAYS + 2


def clean_text(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    return text.strip()


def get_pub_date(pubdate_str):
    try:
        dt = datetime.datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).date()
    except Exception:
        return None


def pub_date_label(d):
    return f"{d.month}.{d.day}" if d else ""


def load_published_urls():
    """최근 data 파일에서 '이미 웹진에 실린' 기사 URL 집합을 만든다.
    당일 기사를 수집하면 다음날 '전날' 기사로 또 잡히므로, 중복 발행을 막기 위함.

    ⚠️ 오늘 날짜 파일은 제외한다.
       collect.py 는 오늘 날짜(data/오늘.json)에 저장하므로,
       재실행 시 자기가 방금 만든 파일을 읽으면 모든 기사가 '이미 발행'으로
       걸러져 0건이 되는 사고가 난다.

    차단된 기사(blocked)도 포함한다 — 이미 부정 판정된 기사를 다시 수집해
    Claude API 를 또 호출하는 낭비를 막는다."""
    today_name = TODAY_KST.strftime("%Y-%m-%d")
    urls = set()
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)
    checked = 0
    for path in paths:
        name = os.path.basename(path).replace(".json", "")
        if name == today_name:      # 자기 자신 제외 (필수)
            continue
        if checked >= LOOKBACK_FILES:
            break
        checked += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        # 발행된 기사
        for section in ("daily", "global"):
            for _cat, arts in (d.get(section) or {}).items():
                for a in arts or []:
                    u = (a.get("url") or "").strip()
                    if u:
                        urls.add(u)
        # 차단된 기사 (재수집·재판정 방지)
        for b in (d.get("blocked") or []):
            u = (b.get("url") or "").strip()
            if u:
                urls.add(u)
    print(f"  이미 발행된 기사 {len(urls)}건 확인 (최근 {checked}일치, 오늘 파일 제외)")
    return urls


# 실행 중 재사용할 '이미 발행된 URL' 집합
PUBLISHED_URLS = set()


def raw_search(query):
    """네이버 뉴스 API 호출 → '전날 발행' 정제 기사 리스트 (2차 필터 전)."""
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": DISPLAY, "sort": SORT}
    try:
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"    [요청 실패] '{query}': {e}")
        return []
    if resp.status_code != 200:
        print(f"    [응답 오류] '{query}': HTTP {resp.status_code}")
        return []

    out = []
    for it in resp.json().get("items", []):
        pub = get_pub_date(it.get("pubDate", ""))
        if pub not in TARGET_DATES:     # 전날 + 당일 발행분만
            continue
        title = clean_text(it.get("title", ""))
        summary = clean_text(it.get("description", ""))
        if not title:
            continue
        url = it.get("originallink") or it.get("link", "")
        if url and url in PUBLISHED_URLS:   # 이미 웹진에 실린 기사는 제외
            continue
        out.append({
            "title": title,
            "source": "",
            "date": pub_date_label(pub),
            "summary": summary,
            "url": url,
        })
    return out


def passes_filter(article, filter_keywords):
    """기사의 제목+요약에 filter_keywords 중 하나라도 있으면 True.
    filter_keywords가 비어있으면 무조건 True(필터 없음)."""
    if not filter_keywords:
        return True
    haystack = article["title"] + " " + article["summary"]
    return any(kw in haystack for kw in filter_keywords)


def pick_evenly(buckets, limit):
    """검색어별 결과 묶음에서 고르게(라운드로빈) 뽑아 limit 건을 만든다.

    기존 방식은 검색어 순서대로 이어붙인 뒤 앞에서 잘라서,
    첫 검색어가 정원을 다 차지하고 뒤쪽 검색어는 통째로 버려졌다.
    이 함수는 검색어1→2→3…→1→2→3… 순으로 한 건씩 번갈아 뽑아
    모든 검색어가 고르게 반영되게 한다."""
    picked = []
    i = 0
    while len(picked) < limit and any(len(b) > i for b in buckets):
        for b in buckets:
            if len(b) > i:
                picked.append(b[i])
                if len(picked) >= limit:
                    break
        i += 1
    return picked


def collect_standard(cat_dict):
    """일반 카테고리 수집 (해석 A: primary 검색 → general 필터).
    검색어별로 따로 담았다가 균등 배분해 정원을 채운다."""
    result = {}
    for cat_name, conf in cat_dict.items():
        primary = conf.get("primary", [])
        general = conf.get("general", [])
        seen_titles, seen_urls = set(), set()
        buckets = []                            # 검색어별 결과 묶음

        for kw in primary:                      # 주요 키워드로 검색
            bucket = []
            for art in raw_search(kw):
                # 2차 필터: 일반 키워드가 든 기사만 (general 비면 전부 통과)
                if not passes_filter(art, general):
                    continue
                if art["title"] in seen_titles or (art["url"] and art["url"] in seen_urls):
                    continue
                seen_titles.add(art["title"])
                if art["url"]:
                    seen_urls.add(art["url"])
                bucket.append(art)
            buckets.append(bucket)
            time.sleep(REQUEST_DELAY)

        result[cat_name] = pick_evenly(buckets, MAX_PER_CATEGORY)
        got = sum(len(b) for b in buckets)
        print(f"  [{cat_name}] {len(result[cat_name])}건 (검색어 {len(primary)}개에서 {got}건 중 균등 선별)")
    return result


def collect_members():
    """회원사 수집 (회사명 검색 → 맥락 키워드 필터).
    회사별로 따로 담았다가 균등 배분해, 앞쪽 회사가 정원을 독식하지 않게 한다."""
    seen_titles, seen_urls = set(), set()
    buckets = []

    for company in MEMBER_COMPANIES:            # 회사명으로 검색
        bucket = []
        for art in raw_search(company):
            # 맥락 키워드(자동차·충전·배출 등)가 있어야 관련 기사로 인정
            if not passes_filter(art, MEMBER_CONTEXT):
                continue
            if art["title"] in seen_titles or (art["url"] and art["url"] in seen_urls):
                continue
            seen_titles.add(art["title"])
            if art["url"]:
                seen_urls.add(art["url"])
            bucket.append(art)
        buckets.append(bucket)
        time.sleep(REQUEST_DELAY)

    articles = pick_evenly(buckets, MAX_PER_CATEGORY)
    got = sum(len(b) for b in buckets)
    print(f"  [회원사 뉴스] {len(articles)}건 (회원사 {len(MEMBER_COMPANIES)}곳에서 {got}건 중 균등 선별)")
    return {"회원사 뉴스": articles}


def main():
    global PUBLISHED_URLS

    print("뉴스 수집 시작")
    print(f"  실행일(KST): {TODAY_KST}")
    _dates = sorted(TARGET_DATES)
    print(f"  수집 대상 발행일: {_dates[0]} ~ {_dates[-1]} (최근 {COLLECT_DAYS}일)")
    PUBLISHED_URLS = load_published_urls()
    print("=" * 50)

    print("[국내 뉴스 - 일반 카테고리]")
    daily = collect_standard(KEYWORDS)

    print("[회원사 뉴스]")
    member = collect_members()
    daily.update(member)     # 회원사 뉴스를 daily에 합침

    # ── 해외뉴스 자동 수집 중단 ──
    # 해외뉴스 모니터링은 담당자가 직접 작성한 파일을 global/ 폴더에 올리는 방식으로
    # 변경되었다(소식지와 동일). 웹진에 표시되지 않는 데이터를 매일 수집하면
    # 네이버 호출과 Claude API 비용만 발생하므로 수집하지 않는다.
    # ※ 과거에 수집된 해외뉴스는 data/*.json 에 남아 있어 '이전 뉴스 검색'에서 계속 조회된다.
    global_news = {}

    date_str = TODAY_KST.strftime("%Y-%m-%d")
    dow_kr = ["월", "화", "수", "목", "금", "토", "일"][TODAY_KST.weekday()]

    data = {
        "no": TODAY_KST.strftime("%m%d"),
        "date": date_str,
        "dow": dow_kr,
        "summary": "",
        "daily": daily,
        "global": global_news,
    }

    out_path = os.path.join(DATA_DIR, f"{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in daily.values()) + sum(len(v) for v in global_news.values())
    print("\n" + "=" * 50)
    print(f"완료: data/{date_str}.json 저장 (전날+당일 발행 총 {total}건)")


if __name__ == "__main__":
    main()
