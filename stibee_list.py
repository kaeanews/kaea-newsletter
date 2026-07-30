"""
====================================================================
스티비 발송 메일 목록 확인 (진단 전용)
====================================================================
역할: 어떤 메일이 있는지 제목만 훑어본다. 변환은 하지 않는다.

왜 필요한가
  제목 규칙이 넓으면 데일리 모니터링이 아닌 메일까지 웹진에 실린다.
  실제로 어떤 제목이 있는지 봐야 규칙을 정확히 정할 수 있다.

  실측: 제목 규칙에 673건이 걸렸는데, 6/1~7/29 두 달이면
        평일 기준 40여 회가 정상이다. 규칙이 넓다는 뜻이다.

비용·시간
  본문을 가져오지도, AI를 부르지도 않는다. 목록 조회만 한다.

실행
  python stibee_list.py                      전체 목록
  python stibee_list.py --since 2026-06-01   해당 날짜 이후만
  python stibee_list.py --all                제목 규칙 무시하고 전부
====================================================================
"""
import os
import re
import sys
import datetime
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

BASE_URL = "https://api.stibee.com/v2"
API_KEY = os.getenv("STIBEE_API_KEY")

_DEFAULT_SUBJECT_FILTER = "한국자동차환경협회 뉴스 모니터링"
SUBJECT_FILTER = os.getenv("STIBEE_SUBJECT_FILTER", "").strip() or _DEFAULT_SUBJECT_FILTER
STATUS_SENT = 3
KST = datetime.timezone(datetime.timedelta(hours=9))

LIST_PAGE = 100
LIST_MAX_PAGES = 15

DATE_FIELDS = ("sentTime", "sentAt", "sendTime", "sendAt", "publishTime",
               "publishedAt", "modifiedTime", "updatedTime",
               "createdTime", "createdAt")


def email_date(e):
    """발송일 YYYY-MM-DD. 못 찾으면 빈 문자열."""
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
        if s.isdigit():
            ts = int(s)
            if ts > 10 ** 11:
                ts //= 1000
            if 10 ** 9 < ts < 4 * 10 ** 9:
                return datetime.datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")
    return ""


def fetch_all():
    """발송 목록을 페이지를 넘겨가며 모은다."""
    items = []
    headers = {"AccessToken": API_KEY}
    for page in range(LIST_MAX_PAGES):
        try:
            r = requests.get(f"{BASE_URL}/emails", headers=headers,
                             params={"offset": page * LIST_PAGE, "limit": LIST_PAGE},
                             timeout=20)
        except Exception as e:
            print(f"[연결 실패] {e}")
            break
        if r.status_code != 200:
            print(f"[API 오류] HTTP {r.status_code}")
            print(f"  응답: {(r.text or '')[:300]}")
            break
        data = r.json()
        got = (data.get("items") or data.get("list") or []) if isinstance(data, dict) else (data or [])
        items.extend(got)
        total = (data.get("total") if isinstance(data, dict) else None) or 0
        if len(got) < LIST_PAGE or (total and len(items) >= total):
            break
    return items


def main():
    if not API_KEY:
        print("[오류] STIBEE_API_KEY 가 없습니다.")
        sys.exit(1)

    argv = sys.argv[1:]
    since = ""
    if "--since" in argv:
        i = argv.index("--since")
        since = argv[i + 1] if i + 1 < len(argv) else ""
        if since and not re.match(r"^\d{4}-\d{2}-\d{2}$", since):
            print(f"[오류] 날짜 형식이 올바르지 않습니다: {since} (예: 2026-06-01)")
            sys.exit(1)
    show_all = "--all" in argv

    items = fetch_all()
    if not items:
        print("[중단] 목록을 가져오지 못했습니다.")
        sys.exit(1)

    sent = [e for e in items if e.get("status") == STATUS_SENT]
    pool = sent if show_all else [e for e in sent if SUBJECT_FILTER in (e.get("subject") or "")]
    if since:
        pool = [e for e in pool if (email_date(e) or "9999") >= since]
    pool.sort(key=lambda e: email_date(e) or "", reverse=True)

    print("=" * 74)
    print("스티비 발송 메일 목록")
    print("=" * 74)
    print(f"  전체 {len(items)}건 · 발송완료 {len(sent)}건")
    if not show_all:
        print(f"  제목 규칙 '{SUBJECT_FILTER}' 포함: {len([e for e in sent if SUBJECT_FILTER in (e.get('subject') or '')])}건")
    if since:
        print(f"  {since} 이후: {len(pool)}건")

    # 날짜별 발송 횟수 — 하루 몇 건씩 나가는지 보면 규칙이 넓은지 알 수 있다
    by_date = Counter(email_date(e) or "날짜미상" for e in pool)
    multi = {d: c for d, c in by_date.items() if c > 1 and d != "날짜미상"}
    print(f"\n  발송된 날짜 {len(by_date)}일")
    if multi:
        print(f"  ⚠️ 하루에 2건 이상 발송된 날 {len(multi)}일")
        for d, c in sorted(multi.items(), reverse=True)[:8]:
            print(f"       {d}: {c}건")
        print("     → 데일리 외 다른 메일이 섞였을 수 있습니다.")

    print("\n" + "=" * 74)
    print(f"제목 목록 (최신순 {min(len(pool), 80)}건)")
    print("=" * 74)
    for e in pool[:80]:
        d = email_date(e) or "날짜미상"
        print(f"  [{d}] {e.get('subject') or '(제목 없음)'}")

    # 제목에서 공통 앞머리를 찾아 규칙 후보를 제안한다
    print("\n" + "=" * 74)
    print("제목 앞부분 패턴 (규칙 정하기용)")
    print("=" * 74)
    heads = Counter()
    for e in pool:
        s = (e.get("subject") or "").strip()
        s = re.sub(r"[\d]+", "#", s)          # 숫자는 # 로 뭉갠다
        heads[s[:26]] += 1
    for h, c in heads.most_common(12):
        print(f"  {c:>4}건  {h}")

    print("\n" + "=" * 74)
    print("참고")
    print("=" * 74)
    print("  · 데일리 모니터링만 남기려면, 위 목록에서 데일리에만 있는 문구를 골라")
    print("    Secrets 의 STIBEE_SUBJECT_FILTER 에 등록하세요.")
    print("  · 이 도구는 목록만 봅니다. 변환·발송은 하지 않습니다.")


if __name__ == "__main__":
    main()
