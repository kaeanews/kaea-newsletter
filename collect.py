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

# 1차 선별용 Claude (선택). 키가 없으면 균등 배분으로 동작한다.
try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

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
DISPLAY = 100          # 네이버 API 1회 최대 (이보다 크게 요청하면 오류)

# 검색어당 몇 페이지까지 받을지. DISPLAY × PAGES 건을 훑는다.
# 네이버는 start 파라미터로 최대 1000번째까지 조회할 수 있다.
# ⚠️ '전기차'처럼 하루 수백 건 쏟아지는 검색어는 100건이 몇 시간 치로 차버려
#    그 앞 기사가 조회 범위 밖으로 밀려난다. 페이지를 늘려 그걸 막는다.
PAGES = 3
# 검색 정렬 방식. 두 가지를 병행해 서로 놓치는 기사를 보완한다.
#   date : 최신순  · sim : 정확도순
# 하나만 쓰려면 목록에서 빼면 된다.
SORT_MODES = ("date", "sim")
REQUEST_DELAY = 0.1
# 카테고리별로 AI 심사(filter.py)에 넘길 후보 기사 수.
# ※ 네이버에서 받아오는 건수(DISPLAY×PAGES)와 다르며, 웹진 게시 건수와도 다르다.
#   여기서 뽑은 후보를 filter.py 가 중요도로 평가해 상위 몇 건만 게시한다.
# 실측: 정원 20일 때 업계동향 후보 299건 중 20건(6%)만 심사해 놓치는 기사가 많았다.
MAX_PER_CATEGORY = 30

# 정원에서 밀려난 후보를 진단용으로 기록한다(제목만).
# 이게 없으면 '검색에 안 나옴'과 '정원에서 밀림'을 구분할 수 없다.
DROPPED_LIMIT = 60          # 카테고리당 기록 상한 (저장소 용량 보호)
DROPPED = []

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


def _search_once(query, sort_mode):
    """네이버 뉴스 검색 → 원본 items 리스트.
    PAGES 만큼 페이지를 넘겨가며 모은다(start=1, 101, 201 ...).
    받아온 건수가 요청보다 적으면 마지막 페이지이므로 거기서 멈춘다."""
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    items = []
    for page in range(PAGES):
        start = page * DISPLAY + 1
        if start > 1000:            # 네이버 start 최대값
            break
        params = {"query": query, "display": DISPLAY, "sort": sort_mode, "start": start}
        try:
            resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
        except requests.RequestException as e:
            print(f"    [요청 실패] '{query}'({sort_mode} p{page+1}): {e}")
            break
        if resp.status_code != 200:
            print(f"    [응답 오류] '{query}'({sort_mode} p{page+1}): HTTP {resp.status_code}")
            break
        got = resp.json().get("items", [])
        items.extend(got)
        if len(got) < DISPLAY:      # 더 이상 결과가 없음
            break
        if page < PAGES - 1:
            time.sleep(REQUEST_DELAY)
    return items


def raw_search(query):
    """네이버 뉴스 검색 → 대상 기간의 정제 기사 리스트 (2차 필터 전).

    ⚠️ 정렬을 두 가지로 병행한다.
       · date(최신순) : 갓 나온 기사를 놓치지 않는다.
                        다만 '전기차'처럼 하루 수백 건 쏟아지는 검색어는
                        최신 100건이 몇 시간 치로 채워져 그 앞 기사가 밀려난다.
       · sim(정확도순): 날짜와 무관하게 관련도 높은 기사를 가져온다.
                        위에서 밀려난 중요 기사를 여기서 건진다.
       같은 기사는 URL·제목으로 한 번만 담는다.
       네이버 검색 API는 무료(일 25,000회)라 호출이 2배가 돼도 한도에 여유가 크다."""
    out = []
    seen_urls, seen_titles = set(), set()

    for sort_mode in SORT_MODES:
        for it in _search_once(query, sort_mode):
            pub = get_pub_date(it.get("pubDate", ""))
            if pub not in TARGET_DATES:         # 대상 기간 발행분만
                continue
            title = clean_text(it.get("title", ""))
            summary = clean_text(it.get("description", ""))
            if not title:
                continue
            url = it.get("originallink") or it.get("link", "")
            if url and url in PUBLISHED_URLS:   # 이미 웹진에 실린 기사는 제외
                continue
            # 두 정렬 결과가 겹치므로 여기서 한 번 더 걸러낸다
            if (url and url in seen_urls) or title in seen_titles:
                continue
            if url:
                seen_urls.add(url)
            seen_titles.add(title)
            out.append({
                "title": title,
                "source": "",
                "date": pub_date_label(pub),
                "summary": summary,
                "url": url,
            })
        time.sleep(REQUEST_DELAY)
    return out


def passes_filter(article, filter_keywords):
    """기사의 제목+요약에 filter_keywords 중 하나라도 있으면 True.
    filter_keywords가 비어있으면 무조건 True(필터 없음)."""
    if not filter_keywords:
        return True
    haystack = article["title"] + " " + article["summary"]
    return any(kw in haystack for kw in filter_keywords)


# ── 1차 선별 (AI 스크리닝) ─────────────────────────────────────────
# 후보가 정원보다 훨씬 많을 때, 제목과 요약만 보고 중요한 것부터 고른다.
# 균등 배분은 '검색어별 앞에서 N건'이라 사실상 무작위였다.
#   실측: 업계 동향 후보 581건 → 30건 선별 시, 담당자가 고른 기사 9건이 모두 탈락
# 본문 크롤링은 하지 않는다. 네이버가 이미 준 요약으로 충분하고 비용·시간이 훨씬 싸다.
SCREEN_MODEL = "claude-haiku-4-5-20251001"
SCREEN_SUMMARY_CHARS = 70        # 후보 1건당 요약 길이 (토큰 절약)
SCREEN_MAX_CANDIDATES = 600      # 한 번에 훑을 최대 후보 수

_screen_key = os.getenv("ANTHROPIC_API_KEY")
screen_client = Anthropic(api_key=_screen_key) if (Anthropic and _screen_key) else None

SCREEN_GUIDE = """당신은 한국자동차환경협회가 회원사에게 보내는 뉴스 웹진의 편집자입니다.
아래 후보 기사 목록에서 회원사에게 중요한 기사를 골라 번호만 알려주세요.

[고를 것]
- 정책·제도의 신설·시행·개정, 지원사업·보조금 공고
- 협회 사업(충전 인프라, 조기폐차, 저감장치, 무공해차 전환)과 관련된 소식
- 업계 판도에 영향을 주는 시장·기술·판매 동향
- 회원사의 사업·기술·투자 소식

[제외할 것]
- 기업 홍보성 보도, 단순 신제품 출시, 인사·수상·행사 참여
- 주제와 스치듯 관련된 기사 (예: 편의점 기사에 '충전소'가 한 번 언급)
- 같은 사안을 다룬 기사가 여러 건이면 가장 내용이 충실한 것 하나만

[답변 형식]
JSON 객체 하나만 출력하세요. 설명 없이 번호 배열만.
{"picked": [3, 7, 12, 25]}"""


def _extract_json(text):
    """응답에서 첫 번째 완전한 JSON 객체만 뽑는다."""
    text = text.strip().replace("```json", "").replace("```", "").strip()
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    return text


def ai_screen(cat_name, articles, limit):
    """후보 중 중요한 limit 건을 AI가 고른다.
    반환: (고른 기사, 탈락 기사)
    실패하거나 키가 없으면 (None, None) → 호출부가 균등 배분으로 넘어간다."""
    if not screen_client or len(articles) <= limit:
        return None, None

    pool = articles[:SCREEN_MAX_CANDIDATES]
    lines = []
    for i, a in enumerate(pool, 1):
        summ = (a.get("summary") or "")[:SCREEN_SUMMARY_CHARS]
        lines.append(f"{i}. {a['title']} | {summ}")
    user = (f"[카테고리] {cat_name}\n"
            f"[고를 개수] {limit}개\n\n"
            f"[후보 {len(pool)}건]\n" + "\n".join(lines))

    raw = ""
    try:
        resp = screen_client.messages.create(
            model=SCREEN_MODEL, max_tokens=1500,
            system=SCREEN_GUIDE,
            messages=[{"role": "user", "content": user},
                      {"role": "assistant", "content": "{"}],
        )
        raw = "{" + resp.content[0].text
        picked = json.loads(_extract_json(raw)).get("picked") or []
    except Exception as e:
        print(f"    [1차 선별 실패 - 균등 배분으로 대체] {cat_name}: {e}")
        if raw:
            print(f"      응답 미리보기: {raw[:120]}")
        return None, None

    idx = []
    for p in picked:
        try:
            v = int(p) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= v < len(pool) and v not in idx:
            idx.append(v)
    if not idx:
        print(f"    [1차 선별 결과 없음 - 균등 배분으로 대체] {cat_name}")
        return None, None

    idx = idx[:limit]
    chosen = [pool[i] for i in idx]
    rest = [a for i, a in enumerate(pool) if i not in set(idx)] + articles[SCREEN_MAX_CANDIDATES:]
    print(f"    1차 선별: 후보 {len(articles)}건 → AI가 {len(chosen)}건 선택")
    return chosen, rest


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

        pool = [a for b in buckets for a in b]
        got = len(pool)

        # 1차 선별: 후보가 많으면 AI가 중요한 것부터 고른다
        chosen, rest = ai_screen(cat_name, pool, MAX_PER_CATEGORY)
        if chosen is None:
            chosen = pick_evenly(buckets, MAX_PER_CATEGORY)
            picked_titles = {a["title"] for a in chosen}
            rest = [a for a in pool if a["title"] not in picked_titles]
            how = "균등 선별"
        else:
            how = "AI 선별"

        result[cat_name] = chosen
        DROPPED.extend({"title": a["title"], "category": cat_name} for a in rest[:DROPPED_LIMIT])
        print(f"  [{cat_name}] {len(chosen)}건 (검색어 {len(primary)}개에서 {got}건 중 {how})")
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

    pool = [a for b in buckets for a in b]
    got = len(pool)
    chosen, rest = ai_screen("회원사 뉴스", pool, MAX_PER_CATEGORY)
    if chosen is None:
        chosen = pick_evenly(buckets, MAX_PER_CATEGORY)
        picked_titles = {a["title"] for a in chosen}
        rest = [a for a in pool if a["title"] not in picked_titles]
        how = "균등 선별"
    else:
        how = "AI 선별"
    DROPPED.extend({"title": a["title"], "category": "회원사 뉴스"} for a in rest[:DROPPED_LIMIT])
    print(f"  [회원사 뉴스] {len(chosen)}건 (회원사 {len(MEMBER_COMPANIES)}곳에서 {got}건 중 {how})")
    return {"회원사 뉴스": chosen}


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
        # 정원에서 밀려난 후보(제목만). 성능 진단 전용이며 웹진에는 쓰이지 않는다.
        "dropped": DROPPED,
    }

    out_path = os.path.join(DATA_DIR, f"{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in daily.values()) + sum(len(v) for v in global_news.values())
    print("\n" + "=" * 50)
    print(f"완료: data/{date_str}.json 저장 (전날+당일 발행 총 {total}건)")


if __name__ == "__main__":
    main()
