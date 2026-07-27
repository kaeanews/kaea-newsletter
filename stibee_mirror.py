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

KST = datetime.timezone(datetime.timedelta(hours=9))

# 웹진 카테고리 (순서·이름을 generate.py 의 DAILY_ORDER 와 맞춰야 한다)
CATEGORIES = [
    "한국자동차환경협회 뉴스",
    "상위기관 뉴스",
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
    return {"AccessToken": API_KEY, "Content-Type": "application/json"}


# ── 1) 스티비에서 발행 메일 찾기 ────────────────────────────────────
def get_sent_emails():
    """발송 완료 + 제목 규칙에 맞는 메일 목록. 최신순."""
    try:
        resp = requests.get(f"{BASE_URL}/emails", headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("items") or data.get("list") or []) if isinstance(data, dict) else (data or [])
    except Exception as e:
        print(f"[이메일 목록 조회 실패] {e}")
        return []

    sent_all = [e for e in items if e.get("status") == STATUS_SENT]
    targets = [e for e in sent_all if SUBJECT_FILTER in (e.get("subject") or "")]
    targets.sort(key=lambda e: e.get("sentTime") or "", reverse=True)

    print(f"이메일 목록: 전체 {len(items)}건 → 발송완료 {len(sent_all)}건 → 제목일치 {len(targets)}건")
    print(f"  (제목 규칙: '{SUBJECT_FILTER}' 포함)")

    # 제목이 안 맞아 하나도 못 찾은 경우, 실제 발송된 제목을 보여줘 원인을 바로 알게 한다
    if sent_all and not targets:
        print("\n  ⚠️ 제목 규칙에 맞는 메일이 없습니다. 실제 발송된 메일 제목은 다음과 같습니다:")
        for e in sorted(sent_all, key=lambda x: x.get("sentTime") or "", reverse=True)[:10]:
            print(f"      [{(e.get('sentTime') or '')[:10]}] {e.get('subject') or '(제목 없음)'}")
        print("\n  → 위 제목에 공통으로 들어가는 문구를 Secrets 의 STIBEE_SUBJECT_FILTER 에 등록하세요.")
        print("     (코드 수정 없이 규칙만 바뀝니다)")
    elif not sent_all:
        print("\n  ⚠️ 발송 완료된 메일이 하나도 없습니다.")
        print("     담당자가 스티비에서 발송을 마쳤는지 확인하세요.")
        print("     (작성 중·예약 상태인 메일은 가져올 수 없습니다)")

    return targets


def get_email_content(email_id):
    """메일 본문 HTML 가져오기."""
    try:
        resp = requests.get(
            f"{BASE_URL}/emails/{email_id}/content",
            headers={"AccessToken": API_KEY},
            timeout=20,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        print(f"  [본문 조회 실패] emailId={email_id}: {e}")
        return ""


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
    """웹진 한 줄 요약 (카테고리별 첫 기사 제목에서 간단히 구성)."""
    titles = []
    for cat in CATEGORIES:
        for a in daily.get(cat, [])[:1]:
            t = re.sub(r"^\[[^\]]*\]\s*", "", a["title"])
            titles.append(t[:22])
        if len(titles) >= 3:
            break
    return ", ".join(titles) if titles else "협회 발행 뉴스레터"


def sent_date(email):
    """발송 시각에서 날짜(KST) 추출. 실패하면 오늘."""
    st = email.get("sentTime") or ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", st)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def convert(email):
    """메일 1건을 웹진 데이터로 변환해 저장. 반환: 저장 경로 또는 None"""
    eid = email.get("id")
    subject = email.get("subject", "")
    date_str = sent_date(email)
    print(f"\n[{date_str}] id={eid} · {subject}")

    raw_html = get_email_content(eid)
    if not raw_html:
        return None
    text = html_to_text(raw_html)
    print(f"  본문 텍스트 {len(text):,}자 추출")

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
        "email_id": eid,
        "subject": subject,
    }
    path = os.path.join(MAIL_DATA_DIR, f"{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  저장: mail_data/{date_str}.json (총 {total}건)")
    return path


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
    if not API_KEY:
        print("[오류] STIBEE_API_KEY 가 없습니다. Secrets/.env 를 확인하세요.")
        report_converted(0)
        sys.exit(1)

    args = sys.argv[1:]
    emails = get_sent_emails()
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
    else:
        emails = emails[:1]   # 기본: 최신 1건

    done = 0
    for e in emails:
        if convert(e):
            done += 1
    print(f"\n완료: {done}건 변환 (mail_data/)")
    if done == 0:
        print("  ⚠️ 메일은 찾았지만 기사를 뽑지 못했습니다. 위 로그의 실패 사유를 확인하세요.")
    report_converted(done)


if __name__ == "__main__":
    main()
