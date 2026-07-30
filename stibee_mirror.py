"""
====================================================================
스티비 발행 메일 → 웹진 데이터 변환 (미러 모드)
====================================================================
역할: 협회 담당자가 스티비에서 직접 작성·발송한 뉴스레터를 가져와
      우리 웹진이 쓰는 데이터 형식(JSON)으로 변환한다.
      → 웹진·이전 뉴스 검색·대시보드가 지금 그대로 작동한다.

흐름:
  1) GET /v2/emails            발송 완료(status=3) + 제목 규칙에 맞는 메일 찾기
  2) GET /v2/emails/{id}/content   본문 HTML 가져오기
  3) HTML → 텍스트+링크 추출 (의존 라이브러리 없이 처리)
  4) Claude 1회 호출로 카테고리별 기사 목록으로 구조화
  5) mail_data/YYYY-MM-DD.json 저장

저장 위치를 data/ 와 분리한 이유:
  자동 수집(collect.py)이 data/ 를 계속 쓰고 있어, 같은 파일을 쓰면 서로 덮어쓴다.
  분리해두면 두 방식의 결과가 모두 보존되고, WEBZINE_SOURCE 스위치로 골라 쓸 수 있다.

실행:
  python stibee_mirror.py              최신 발송분 1건 변환
  python stibee_mirror.py --all        아직 변환 안 된 발송분 전부
  python stibee_mirror.py --all --since 2026-06-01
                                       6월 1일 이후 발송분 전부
  python stibee_mirror.py --email 123  특정 이메일 ID 지정

필요한 환경변수:
  STIBEE_API_KEY          스티비 API 키
  ANTHROPIC_API_KEY       Claude API 키
  STIBEE_SUBJECT_FILTER   (선택) 제목 규칙. 없으면 기본값 사용
====================================================================
"""
import os
import re
import sys
import json
import html as html_mod
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIL_DATA_DIR = os.path.join(ROOT, "mail_data")
os.makedirs(MAIL_DATA_DIR, exist_ok=True)

BASE_URL = "https://api.stibee.com/v2"
API_KEY = os.getenv("STIBEE_API_KEY")

_DEFAULT_SUBJECT_FILTER = "한국자동차환경협회 뉴스 모니터링"
SUBJECT_FILTER = os.getenv("STIBEE_SUBJECT_FILTER", "").strip() or _DEFAULT_SUBJECT_FILTER
STATUS_SENT = 3

# 한 번에 변환할 최대 건수.
# ⚠️ 이 계정에는 제목이 일치하는 메일이 673건 있었다. 상한이 없으면
#    수십 달러와 몇 시간이 한 번에 소모될 수 있다.
#    상한에 걸리면 최신 것부터 처리하고, 나머지는 다시 실행해 이어받는다.
MAX_CONVERT = 60

KST = datetime.timezone(datetime.timedelta(hours=9))

# 웹진 카테고리 (순서·이름을 generate.py 의 DAILY_ORDER 와 맞춰야 한다)
CATEGORIES = [
    "한국자동차환경협회 뉴스",
    "상위기관 뉴스",
    "자동차 LCA 사업 뉴스",
    "배출 저감사업 뉴스",
    "전기·수소차 뉴스 - 협회 사업 관련 뉴스",
    "전기·수소차 뉴스 - 업계 동향",
    "회원사 뉴스",
    "기타 뉴스",
]

# AI 에 넘길 본문 길이 상한 (토큰 폭증 방지)
MAX_TEXT_CHARS = 12000

# ── Claude ─────────────────────────────────────────────────────────
try:
    from anthropic import Anthropic
except ImportError:
    print("[오류] anthropic 라이브러리가 없습니다: pip install anthropic python-dotenv")
    sys.exit(1)

MODEL = "claude-haiku-4-5-20251001"
_ak = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=_ak) if _ak else None


def _headers():
    """스티비 API 인증 헤더.
    ⚠️ GET 요청에는 Content-Type 을 붙이지 않는다.
       본문 없는 GET 에 Content-Type: application/json 을 보내면
       일부 서버가 400 Bad Request 로 거부한다.
       스티비 공식 예시도 AccessToken 헤더만 사용한다."""
    return {"AccessToken": API_KEY}


def _api_get(path, params=None, as_json=True):
    """스티비 GET 호출 공통 처리.
    실패하면 상태코드뿐 아니라 응답 본문까지 찍어 원인을 바로 알 수 있게 한다."""
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=20)
    except Exception as e:
        print(f"  [연결 실패] {path}: {e}")
        return None

    if resp.status_code != 200:
        print(f"  [API 오류] HTTP {resp.status_code} · {path}")
        body = (resp.text or "").strip()
        if body:
            print(f"    스티비 응답: {body[:400]}")
        if resp.status_code in (401, 403):
            print("    → API 키가 잘못됐거나, 요금제에서 이메일 API를 지원하지 않을 수 있습니다.")
            print("       (이메일 API는 프로 요금제 이상에서 사용 가능)")
        elif resp.status_code == 400:
            if "프로 요금제" in (resp.text or ""):
                print("    → 이 워크스페이스는 이메일 API를 쓸 수 없는 요금제입니다.")
                print("       해결 ① 스티비 프로 요금제로 올린다")
                print("       해결 ② '웹에서 보기' 링크 모드를 쓴다 (요금제 무관)")
                print("               python stibee_mirror.py --url https://stib.ee/XXXX")
            else:
                print("    → 요청 형식 문제입니다. 위 응답 내용을 확인하세요.")
        return None

    if not as_json:
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    try:
        return resp.json()
    except Exception as e:
        print(f"  [응답 해석 실패] {path}: {e}")
        return None


# ── 1) 스티비에서 발행 메일 찾기 ────────────────────────────────────
LIST_PAGE = 100          # 목록 조회 1회당 건수
LIST_HARD_CAP = 5000     # 안전 상한 (무한 루프 방지)
# ⚠️ 예전에는 12페이지(1,200건)에서 멈춰, 그 뒤에 있던 최신 메일을 통째로 놓쳤다.
#    이제 API 가 알려주는 total 까지 끝까지 받는다.


# 발송일이 담길 수 있는 필드 이름들.
# ⚠️ 계정·API 버전에 따라 이름이 다를 수 있어 하나만 가정하면 안 된다.
#    실제로 sentTime 만 보다가 673건이 통째로 걸러지는 일이 있었다.
DATE_FIELDS = ("sentTime", "sentAt", "sendTime", "sendAt", "publishTime",
               "publishedAt", "modifiedTime", "updatedTime",
               "createdTime", "createdAt")


def email_date(e):
    """이메일의 발송일을 YYYY-MM-DD 로 돌려준다. 못 찾으면 빈 문자열.
    ISO 형식, 공백 구분 형식, 타임스탬프(초·밀리초)를 모두 인식한다."""
    if not isinstance(e, dict):
        return ""
    for k in DATE_FIELDS:
        v = e.get(k)
        if v in (None, "", 0):
            continue
        s = str(v)
        m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)),
                                     int(m.group(3))).strftime("%Y-%m-%d")
            except ValueError:
                continue
        if s.isdigit():                       # 타임스탬프
            ts = int(s)
            if ts > 10 ** 11:                 # 밀리초
                ts //= 1000
            if 10 ** 9 < ts < 4 * 10 ** 9:
                return datetime.datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")
    return ""


def describe_fields(sample):
    """응답에 실제로 어떤 필드가 있는지 보여준다 (원인 파악용)."""
    if not isinstance(sample, dict):
        return "(형식을 알 수 없음)"
    keys = sorted(sample.keys())
    shown = []
    for k in keys:
        v = sample.get(k)
        if isinstance(v, (str, int, float)) and str(v):
            shown.append(f"{k}={str(v)[:24]}")
        else:
            shown.append(k)
    return ", ".join(shown[:16])


def get_sent_emails(since=""):
    """발송 완료 + 제목 규칙에 맞는 메일 목록. 최신순.

    since : "YYYY-MM-DD" 이후 발송분만 (비우면 전부)

    ⚠️ 과거 발송분을 일괄로 가져오려면 100건으로는 부족하다.
       offset 을 넘겨가며 여러 페이지를 모은다.
       받아온 건수가 요청보다 적으면 마지막 페이지이므로 멈춘다."""
    items, total, offset = [], 0, 0
    while True:
        data = _api_get("/emails", params={"offset": offset, "limit": LIST_PAGE})
        if data is None:
            if offset == 0:
                print("[중단] 이메일 목록을 가져오지 못했습니다.")
                return []
            break
        got = (data.get("items") or data.get("list") or []) if isinstance(data, dict) else (data or [])
        if not total:
            total = (data.get("total") if isinstance(data, dict) else None) or 0
        items.extend(got)
        offset += LIST_PAGE
        if not got or len(got) < LIST_PAGE:
            break
        if total and len(items) >= total:
            break
        if offset >= LIST_HARD_CAP:
            print(f"  ⚠️ 안전 상한({LIST_HARD_CAP}건)에 걸려 일부만 받았습니다.")
            break
    if len(items) > LIST_PAGE:
        print(f"  목록 {len(items)}건 조회 (API 총계 {total or '?'}건)")

    sent_all = [e for e in items if e.get("status") == STATUS_SENT]
    targets = [e for e in sent_all if SUBJECT_FILTER in (e.get("subject") or "")]

    # 날짜 범위 적용
    before_since = len(targets)
    unknown_date = 0
    if since:
        kept = []
        for e in targets:
            d = email_date(e)
            if not d:
                unknown_date += 1
                kept.append(e)        # 날짜를 못 읽으면 버리지 않는다(누락 방지)
            elif d >= since:
                kept.append(e)
        targets = kept

    targets.sort(key=lambda e: email_date(e) or "", reverse=True)

    dates = [d for d in (email_date(e) for e in targets or sent_all) if d]
    if dates:
        print(f"  발송일 범위: {min(dates)} ~ {max(dates)}")
    line = f"이메일 목록: 전체 {len(items)}건 → 발송완료 {len(sent_all)}건 → 제목일치 {before_since}건"
    if since:
        line += f" → {since} 이후 {len(targets)}건"
    print(line)
    print(f"  (제목 규칙: '{SUBJECT_FILTER}' 포함)")

    # 제목이 안 맞아 하나도 못 찾은 경우, 실제 발송된 제목을 보여줘 원인을 바로 알게 한다
    if sent_all and not before_since:
        print("\n  ⚠️ 제목 규칙에 맞는 메일이 없습니다. 실제 발송된 메일 제목은 다음과 같습니다:")
        for e in sorted(sent_all, key=lambda x: email_date(x) or "", reverse=True)[:15]:
            print(f"      [{email_date(e) or '날짜미상'}] {e.get('subject') or '(제목 없음)'}")
        print("\n  → 위 제목에 공통으로 들어가는 문구를 Secrets 의 STIBEE_SUBJECT_FILTER 에 등록하세요.")
        print("     (코드 수정 없이 규칙만 바뀝니다)")
    elif before_since and not targets:
        print(f"\n  ⚠️ 제목은 맞지만 {since} 이후 발송분이 없습니다.")
        sample = next((e for e in sent_all if SUBJECT_FILTER in (e.get("subject") or "")), None)
        if sample is not None:
            print(f"     응답에 담긴 항목: {describe_fields(sample)}")
            print(f"     읽어낸 발송일: '{email_date(sample) or '(없음)'}'")
        print("     발송일을 못 읽었다면 시작 날짜를 비우고 다시 실행하세요.")
    elif not sent_all:
        print("\n  ⚠️ 발송 완료된 메일이 하나도 없습니다.")
        print("     담당자가 스티비에서 발송을 마쳤는지 확인하세요.")
        print("     (작성 중·예약 상태인 메일은 가져올 수 없습니다)")

    return targets


def get_email_content(email_id):
    """메일 본문 HTML 가져오기."""
    text = _api_get(f"/emails/{email_id}/content", as_json=False)
    return text or ""


# ── 2) HTML → 텍스트+링크 ──────────────────────────────────────────
def html_to_text(raw_html):
    """이메일 HTML에서 사람이 읽는 텍스트와 링크를 뽑는다.
    라이브러리 없이 처리해 GitHub Actions 의존성을 늘리지 않는다.
    링크는 '제목 [[URL]]' 형태로 남긴다.
    ※ 마커에 < > 를 쓰면 뒤이은 태그 제거 정규식이 통째로 지워버리므로 대괄호를 쓴다."""
    if not raw_html:
        return ""
    t = raw_html
    # 화면에 안 보이는 요소 제거
    t = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?is)<!--.*?-->", " ", t)
    # 링크는 URL을 살려서 표시
    t = re.sub(
        r'(?is)<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f" {re.sub(r'(?s)<[^>]+>', '', m.group(2)).strip()} [[{m.group(1).strip()}]] ",
        t,
    )
    # 줄바꿈이 되는 태그
    t = re.sub(r"(?i)<(br|/p|/div|/tr|/td|/h[1-6]|/li)\s*/?>", "\n", t)
    # 나머지 태그 제거
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    # 공백 정리
    t = t.replace("\u200b", " ").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# ── 3) AI 로 기사 구조화 ───────────────────────────────────────────
EXTRACT_GUIDE = """당신은 뉴스레터에서 기사 목록을 추출하는 도구입니다.
협회 담당자가 발행한 뉴스레터 본문을 읽고, 실린 기사들을 카테고리별로 정리하세요.

[카테고리 목록] 반드시 아래 이름 중 하나로만 분류하세요.
""" + "\n".join(f"  - {c}" for c in CATEGORIES) + """

[분류 요령]
- 본문에는 카테고리 제목이 그대로 적혀 있습니다. 그 아래 기사들을 해당 카테고리에 넣으세요.
- '전기/수소차 뉴스' 아래에 '협회 사업 관련 뉴스'와 '업계 동향' 소제목이 있으면
  각각 '전기·수소차 뉴스 - 협회 사업 관련 뉴스', '전기·수소차 뉴스 - 업계 동향' 으로 넣으세요.
- 본문의 카테고리 이름이 위 목록과 조금 달라도(예: '배출가스 저감 사업 뉴스')
  가장 가까운 이름으로 맞추세요.
- 어느 카테고리인지 불분명하면 '기타 뉴스'로 넣으세요.

[기사 판별]
- 뉴스 기사만 뽑습니다.
- 아래는 기사가 아니므로 제외하세요.
  구독 취소·수신거부, 소셜미디어(인스타그램·유튜브·블로그·페이스북) 링크,
  협회 홈페이지·문의 이메일, 웹에서 보기, 구독하기 버튼, 이미지 파일 링크

[각 기사에서 뽑을 것]
- title   : 기사 제목 (앞의 번호 '1.' 은 빼세요)
- url     : 기사 링크. 본문에서 [[URL]] 형태로 표시돼 있습니다.
- date    : [7.27/에너지데일리] 같은 표기에서 날짜 부분만 (예: "7.27"). 없으면 ""
- source  : 위 표기에서 언론사 부분만 (예: "에너지데일리"). 없으면 ""
- summary : 제목 아래 본문 요약을 그대로 옮기세요. 새로 지어내지 마세요.

[답변 형식]
JSON 객체 하나만 출력하세요. 설명·코드블록 없이 JSON만.
{"daily": {"카테고리이름": [{"title": "", "url": "", "date": "", "source": "", "summary": ""}]}}
기사가 없는 카테고리는 넣지 마세요."""


def extract_articles(text):
    """뉴스레터 텍스트에서 카테고리별 기사 목록을 뽑는다."""
    if not client:
        print("[오류] ANTHROPIC_API_KEY 가 없습니다.")
        return {}
    if len(text) > MAX_TEXT_CHARS:
        print(f"  본문이 길어 {MAX_TEXT_CHARS:,}자로 자름 (원본 {len(text):,}자)")
        text = text[:MAX_TEXT_CHARS]

    raw = ""
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=8000,
            system=[{"type": "text", "text": EXTRACT_GUIDE,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[
                {"role": "user", "content": f"[뉴스레터 본문]\n{text}"},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw = "{" + resp.content[0].text
        result = json.loads(_extract_json(raw))
        return result.get("daily", {}) or {}
    except Exception as e:
        print(f"  [기사 추출 실패] {e}")
        if raw:
            print(f"    응답 미리보기: {raw[:150]}")
        return {}


def _extract_json(text):
    """응답에서 첫 번째 완전한 JSON 객체만 뽑는다 (뒤에 설명이 붙어도 안전)."""
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


# 담당자 메일에서 쓰는 표기 ↔ 웹진 표준 카테고리 이름
# (공백·중점·슬래시를 뺀 형태를 키로 둔다)
CATEGORY_ALIASES = {
    "협회뉴스": "한국자동차환경협회 뉴스",
    "자동차환경협회뉴스": "한국자동차환경협회 뉴스",
    "자동차LCA사업뉴스": "자동차 LCA 사업 뉴스",
    "LCA사업뉴스": "자동차 LCA 사업 뉴스",
    "LCA뉴스": "자동차 LCA 사업 뉴스",
    "전과정평가뉴스": "자동차 LCA 사업 뉴스",
    "배출가스저감사업뉴스": "배출 저감사업 뉴스",
    "배출가스저감뉴스": "배출 저감사업 뉴스",
    "저감사업뉴스": "배출 저감사업 뉴스",
    "조기폐차뉴스": "배출 저감사업 뉴스",
    # 소제목만 넘어온 경우 (상위 '전기/수소차 뉴스' 아래 하위 항목)
    "협회사업관련뉴스": "전기·수소차 뉴스 - 협회 사업 관련 뉴스",
    "협회사업뉴스": "전기·수소차 뉴스 - 협회 사업 관련 뉴스",
    "업계동향": "전기·수소차 뉴스 - 업계 동향",
    "업계동향뉴스": "전기·수소차 뉴스 - 업계 동향",
    "회원사소식": "회원사 뉴스",
}


def _simplify(name):
    return re.sub(r"[\s·/\-]", "", name or "")


def match_category(name):
    """메일에 적힌 카테고리 이름을 웹진 표준 이름으로 맞춘다.
    ① 정확히 일치 ② 별칭표 ③ 부분 포함 ④ 글자 유사도 순으로 찾는다."""
    name = (name or "").strip()
    if name in CATEGORIES:
        return name
    simple = _simplify(name)
    if not simple:
        return "기타 뉴스"
    if simple in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[simple]
    std = {_simplify(c): c for c in CATEGORIES}
    if simple in std:
        return std[simple]
    for s, c in std.items():
        if simple in s or s in simple:
            return c
    import difflib
    hit = difflib.get_close_matches(simple, list(std.keys()), n=1, cutoff=0.6)
    return std[hit[0]] if hit else "기타 뉴스"


def normalize(daily_raw):
    """카테고리 이름을 웹진 표준 이름으로 맞추고, 빈 항목을 정리한다."""
    out = {}
    for cat, arts in (daily_raw or {}).items():
        if not isinstance(arts, list):
            continue
        name = match_category(cat)
        cleaned = []
        for a in arts:
            if not isinstance(a, dict):
                continue
            title = (a.get("title") or "").strip()
            if not title:
                continue
            cleaned.append({
                "title": title,
                "source": (a.get("source") or "").strip(),
                "date": (a.get("date") or "").strip(),
                "summary": (a.get("summary") or "").strip(),
                "url": (a.get("url") or "").strip(),
            })
        if cleaned:
            out.setdefault(name, []).extend(cleaned)
    # 표준 순서대로 정렬
    return {c: out[c] for c in CATEGORIES if c in out}


def make_summary(daily):
    """웹진 한 줄 요약.
    ⚠️ 이전에는 기사 제목을 22자에서 잘라 이어붙였는데,
       "서산시, 2026년 하반기 노후차량 조기, 배터리 덜어내자 전기차 가격 40%↓…현"
       처럼 문장이 중간에 끊겨 읽기 어려웠다.
       자동 수집 방식(filter.py)과 똑같이 AI가 주제 키워드를 뽑도록 맞춘다.
       AI를 못 쓰면 제목에서 키워드만 간단히 뽑아 되도록 자연스럽게 만든다."""
    titles = [arts[0].get("title", "") for arts in daily.values() if arts]
    if not titles:
        return ""

    if client:
        joined = "\n".join(f"- {t}" for t in titles[:6])
        prompt = f"""아래는 오늘 자동차·환경 뉴스 웹진의 주요 기사 제목들입니다.
이 내용을 대표하는 한 줄 요약을 만들어 주세요.

{joined}

조건:
- 쉼표로 구분된 핵심 키워드 3개 정도 (예: "전기차 보조금 개편, 수소충전소 확대, EU 규제")
- 15자~40자 이내
- 다른 말 없이 요약 문구만 출력"""
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip().strip('"').strip()
            if text:
                return text
        except Exception as e:
            print(f"  [웹진 요약 생성 실패 - 간이 요약 사용] ({e})")

    # 대체 수단: AI를 못 쓸 때. 제목을 자르지 않고 '단어 경계'까지만 사용해
    # 문장이 중간에 끊기지 않게 한다.
    def clip(text, limit=16):
        text = re.sub(r"^\[[^\]]*\]\s*", "", text)      # 머리말 대괄호 제거
        text = re.split(r"[…·\-–—\|]", text)[0]           # 부제 앞부분만
        text = re.sub(r"^[^,]{1,10},\s*", "", text)       # 앞머리 기관·지역명 제거
        text = re.sub(r"[\"'\u201c\u201d\u2018\u2019]", "", text).strip()
        if len(text) <= limit:
            return text
        # 한도 안에서 마지막 띄어쓰기까지만 (단어가 잘리지 않게)
        cut = text[:limit]
        sp = cut.rfind(" ")
        return (cut[:sp] if sp >= limit // 2 else cut).strip(" ,·")

    parts = [c for c in (clip(t) for t in titles[:3]) if c]
    return ", ".join(parts) if parts else "협회 발행 뉴스레터"


def sent_date(email):
    """발송일(YYYY-MM-DD). 못 읽으면 오늘.
    ⚠️ 발송일을 못 읽으면 여러 건이 같은 파일명으로 저장돼 서로 덮어쓴다.
       그래서 변환 단계에서 한 번 더 경고한다."""
    return email_date(email) or datetime.datetime.now(KST).strftime("%Y-%m-%d")


def build_and_save(raw_html, date_str, subject="", email_id=None, origin="api"):
    """메일 HTML → 기사 추출 → mail_data/날짜.json 저장. 반환: 경로 또는 None"""
    text = html_to_text(raw_html)
    print(f"  본문 텍스트 {len(text):,}자 추출")
    if len(text) < 50:
        print("  [중단] 본문이 너무 짧습니다. 링크나 메일 내용을 확인하세요.")
        return None

    daily = normalize(extract_articles(text))
    total = sum(len(v) for v in daily.values())
    if total == 0:
        print("  [중단] 추출된 기사가 없습니다.")
        return None
    for c, arts in daily.items():
        print(f"    {c}: {len(arts)}건")

    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    data = {
        "no": d.strftime("%m%d"),
        "date": date_str,
        "dow": ["월", "화", "수", "목", "금", "토", "일"][d.weekday()],
        "summary": make_summary(daily),
        "daily": daily,
        "global": {},
        "source": "stibee",
        "origin": origin,          # api = 이메일 API / link = 웹에서 보기 링크
        "email_id": email_id,
        "subject": subject,
    }
    path = os.path.join(MAIL_DATA_DIR, f"{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  저장: mail_data/{date_str}.json (총 {total}건)")
    return path


def convert(email):
    """[API 모드] 이메일 API로 본문을 받아 변환한다. (프로 요금제 필요)"""
    eid = email.get("id")
    subject = email.get("subject", "")
    date_str = sent_date(email)
    known = email_date(email)
    print(f"\n[{date_str}] id={eid} · {subject}")
    if not known:
        print("  ⚠️ 발송일을 읽지 못해 오늘 날짜로 저장합니다. 필요하면 나중에 정리하세요.")

    raw_html = get_email_content(eid)
    if not raw_html:
        return None
    return build_and_save(raw_html, date_str, subject, eid, origin="api")


# ── [링크 모드] 프로 요금제 없이 쓰는 우회 경로 ──────────────────
# 스티비 메일의 '웹에서 보기' 링크(https://stib.ee/XXXX)는 로그인 없이 열리는
# 공개 페이지다. 이메일 API를 못 쓰는 요금제에서도 이 경로로 본문을 가져올 수 있다.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_ALLOWED_HOST = re.compile(r"^https?://(stib\.ee|(www\.)?stibee\.com)/", re.I)


def fetch_shared_html(url):
    """'웹에서 보기' 공개 링크에서 메일 HTML을 가져온다."""
    url = (url or "").strip()
    if not _ALLOWED_HOST.match(url):
        print(f"  [오류] 스티비 링크가 아닙니다: {url}")
        print("     https://stib.ee/... 또는 https://stibee.com/... 형태여야 합니다.")
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=20, allow_redirects=True)
    except Exception as e:
        print(f"  [연결 실패] {e}")
        return ""
    if r.status_code != 200:
        print(f"  [오류] HTTP {r.status_code} · 링크를 열 수 없습니다.")
        print("     링크가 만료됐거나 잘못된 주소일 수 있습니다.")
        return ""
    r.encoding = r.apparent_encoding or "utf-8"
    print(f"  링크에서 HTML {len(r.text):,}자 받음")
    return r.text


def guess_date(raw_html):
    """메일 내용에서 발행일을 추론한다. 못 찾으면 오늘(KST)."""
    text = html_to_text(raw_html)[:3000]
    m = re.search(r"(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def convert_from_url(url, date_override=""):
    """[링크 모드] 공개 링크에서 메일을 가져와 변환한다."""
    print(f"\n[링크 모드] {url}")
    raw_html = fetch_shared_html(url)
    if not raw_html:
        return None

    date_str = (date_override or "").strip()
    if date_str:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            print(f"  [오류] 날짜 형식이 올바르지 않습니다: {date_str} (예: 2026-07-27)")
            return None
        print(f"  발행일: {date_str} (직접 지정)")
    else:
        date_str = guess_date(raw_html)
        print(f"  발행일: {date_str} (메일 내용에서 추론)")

    return build_and_save(raw_html, date_str, subject="(웹에서 보기 링크)",
                          email_id=None, origin="link")


def report_converted(count):
    """변환 건수를 GitHub Actions 다음 단계로 넘긴다.
    0건이면 웹진 재생성을 건너뛰게 해서, 엉뚱한 단계에서 실패하지 않도록 한다."""
    out = os.getenv("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"converted={count}\n")
    except Exception:
        pass


def main():
    args = sys.argv[1:]

    # ── 링크 모드: 프로 요금제·API 키 없이도 동작한다 ──
    if "--url" in args:
        try:
            url = args[args.index("--url") + 1]
        except IndexError:
            print("[오류] --url 뒤에 '웹에서 보기' 링크를 넣으세요.")
            report_converted(0)
            sys.exit(1)
        date_override = ""
        if "--date" in args:
            try:
                date_override = args[args.index("--date") + 1]
            except IndexError:
                print("[오류] --date 뒤에 날짜(YYYY-MM-DD)를 넣으세요.")
                report_converted(0)
                sys.exit(1)
        ok = convert_from_url(url, date_override)
        print(f"\n완료: {1 if ok else 0}건 변환 (mail_data/)")
        report_converted(1 if ok else 0)
        if not ok:
            sys.exit(1)
        return

    # ── API 모드: 이메일 API 사용 (프로 요금제 필요) ──
    if not API_KEY:
        print("[오류] STIBEE_API_KEY 가 없습니다. Secrets/.env 를 확인하세요.")
        report_converted(0)
        sys.exit(1)

    # 시작 날짜(--since). 값을 읽은 뒤에 목록에서 빼야 다른 인자로 오해되지 않는다.
    since = ""
    if "--since" in args:
        i = args.index("--since")
        since = args[i + 1] if i + 1 < len(args) else ""
        if since and not re.match(r"^\d{4}-\d{2}-\d{2}$", since):
            print(f"[오류] --since 날짜 형식이 올바르지 않습니다: {since} (예: 2026-06-01)")
            report_converted(0)
            sys.exit(1)
        args = args[:i] + args[i + 2:]

    emails = get_sent_emails(since)
    if not emails:
        print("\n[안내] 변환할 발송 메일이 없습니다. 웹진은 그대로 둡니다.")
        report_converted(0)
        return

    if "--email" in args:
        try:
            want = int(args[args.index("--email") + 1])
        except (IndexError, ValueError):
            print("[오류] --email 뒤에 이메일 ID를 넣으세요.")
            report_converted(0)
            sys.exit(1)
        emails = [e for e in emails if e.get("id") == want]
        if not emails:
            print(f"[오류] id={want} 인 발송 메일을 찾지 못했습니다.")
            report_converted(0)
            sys.exit(1)
    elif "--all" in args:
        # 이미 변환한 날짜는 건너뛴다
        emails = [e for e in emails
                  if not os.path.exists(os.path.join(MAIL_DATA_DIR, f"{sent_date(e)}.json"))]
        if not emails:
            print("\n[안내] 새로 변환할 메일이 없습니다. (모두 변환됨)")
            report_converted(0)
            return
        if len(emails) > MAX_CONVERT:
            print(f"\n  ⚠️ 변환 대상이 {len(emails)}건입니다. 한 번에 {MAX_CONVERT}건까지만 처리합니다.")
            print(f"     (비용·실행시간 보호) 최신 것부터 처리하며, 다시 실행하면 이어서 진행됩니다.")
            emails = emails[:MAX_CONVERT]
    else:
        emails = emails[:1]   # 기본: 최신 1건

    done = 0
    used_dates = set()
    for e in emails:
        d = sent_date(e)
        if d in used_dates:
            print(f"\n  [건너뜀] {d} 는 이번 실행에서 이미 처리했습니다."
                  f" (id={e.get('id')} · 발송일을 못 읽었을 수 있음)")
            continue
        used_dates.add(d)
        if convert(e):
            done += 1
    print(f"\n완료: {done}건 변환 (mail_data/)")
    if done == 0:
        print("  ⚠️ 메일은 찾았지만 기사를 뽑지 못했습니다. 위 로그의 실패 사유를 확인하세요.")
    report_converted(done)


if __name__ == "__main__":
    main()
