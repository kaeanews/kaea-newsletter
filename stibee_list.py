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


LIST_HARD_CAP = 5000        # 안전 상한 (무한 루프 방지)


def _get(offset, limit):
    """목록 1페이지 조회. 반환: (items, total, 오류메시지)"""
    try:
        r = requests.get(f"{BASE_URL}/emails", headers={"AccessToken": API_KEY},
                         params={"offset": offset, "limit": limit}, timeout=20)
    except Exception as e:
        return [], 0, f"연결 실패: {e}"
    if r.status_code != 200:
        return [], 0, f"HTTP {r.status_code} · {(r.text or '')[:200]}"
    data = r.json()
    items = (data.get("items") or data.get("list") or []) if isinstance(data, dict) else (data or [])
    total = (data.get("total") if isinstance(data, dict) else None) or 0
    return items, total, ""


def fetch_all():
    """발송 목록 전체를 모은다.

    ⚠️ 예전에는 15페이지(1,500건)에서 멈춰, 그 뒤에 있던 최신 메일을
       통째로 놓쳤다. 이제 API 가 알려주는 total 까지 끝까지 받는다.
    반환: (items, total, capped)
    """
    first, total, err = _get(0, LIST_PAGE)
    if err:
        print(f"[API 오류] {err}")
        return [], 0, False
    items = list(first)
    if not total:
        total = len(first)

    offset = LIST_PAGE
    capped = False
    while offset < total:
        if offset >= LIST_HARD_CAP:
            capped = True
            break
        got, _, err = _get(offset, LIST_PAGE)
        if err:
            print(f"  [일부 조회 실패] offset={offset}: {err}")
            break
        if not got:
            break
        items.extend(got)
        offset += LIST_PAGE
    return items, total, capped


def date_range(pool):
    """목록의 발송일 범위를 돌려준다."""
    ds = [d for d in (email_date(e) for e in pool) if d]
    return (min(ds), max(ds), len(pool) - len(ds)) if ds else ("", "", len(pool))


def show_samples(pool, n=3):
    """원본 응답에 어떤 날짜 관련 값이 들어 있는지 그대로 보여준다."""
    print("\n" + "=" * 74)
    print("응답 원본 샘플 (날짜 필드 확인용)")
    print("=" * 74)
    for e in pool[:n]:
        print(f"\n  · {(e.get('subject') or '(제목 없음)')[:52]}")
        found = False
        for k in sorted(e.keys()):
            if any(w in k.lower() for w in ("time", "date", "at", "created", "sent", "publish")):
                print(f"      {k} = {e.get(k)}")
                found = True
        if not found:
            print(f"      (날짜로 보이는 항목 없음) 전체 항목: {', '.join(sorted(e.keys())[:14])}")
        print(f"      → 읽어낸 발송일: '{email_date(e) or '(없음)'}'")


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

    items, total, capped = fetch_all()
    if not items:
        print("[중단] 목록을 가져오지 못했습니다.")
        sys.exit(1)

    sent = [e for e in items if e.get("status") == STATUS_SENT]
    matched = sent if show_all else [e for e in sent if SUBJECT_FILTER in (e.get("subject") or "")]
    pool = matched
    if since:
        pool = [e for e in matched if (email_date(e) or "9999") >= since]
    pool.sort(key=lambda e: email_date(e) or "", reverse=True)

    print("=" * 74)
    print("스티비 발송 메일 목록")
    print("=" * 74)
    print(f"  API 총계 {total}건 · 받아온 것 {len(items)}건 · 발송완료 {len(sent)}건")
    if capped:
        print(f"  ⚠️ 안전 상한({LIST_HARD_CAP}건)에 걸려 일부만 받았습니다.")
    elif total and len(items) < total:
        print(f"  ⚠️ 총 {total}건 중 {len(items)}건만 받았습니다. 조회가 중단됐을 수 있습니다.")
    if not show_all:
        print(f"  제목 규칙 '{SUBJECT_FILTER}' 포함: {len(matched)}건")

    # 발송일 범위 — 최신 메일이 조회에 들어왔는지 판단하는 핵심 정보
    lo, hi, unknown = date_range(matched)
    print(f"\n  제목 일치분의 발송일 범위: {lo or '?'} ~ {hi or '?'}"
          + (f" (날짜 미상 {unknown}건)" if unknown else ""))
    if since:
        print(f"  {since} 이후: {len(pool)}건")

    if since and not pool:
        print("\n  " + "-" * 70)
        print("  ⚠️ 해당 기간 메일이 하나도 없습니다. 아래를 확인하세요.")
        if hi and hi < since:
            print(f"     · 가장 최근 발송일이 {hi} 입니다. 최신 메일이 조회에 안 들어왔거나,")
            print(f"       읽어낸 날짜가 실제 발송일이 아닐 수 있습니다.")
        if unknown:
            print(f"     · 날짜를 못 읽은 메일이 {unknown}건 있습니다.")
        print("  " + "-" * 70)
        show_samples(matched)
        print("\n  → 시작 날짜를 비우고 다시 실행하면 전체 목록을 볼 수 있습니다.")
        return

    # 날짜별 발송 횟수 — 하루 몇 건씩 나가는지 보면 규칙이 넓은지 알 수 있다
    by_date = Counter(email_date(e) or "날짜미상" for e in pool)
    if not pool:
        show_samples(matched)
        return
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
