"""
====================================================================
수집 성능 비교 리포트
====================================================================
역할: 담당자가 실제로 발행한 뉴스레터(정답지)와
      우리 자동 수집 결과를 비교해 성능을 숫자로 보여준다.

      키워드나 프롬프트를 고칠 때마다 이 리포트를 돌려
      재현율이 올랐는지 확인하는 용도.

비교 대상:
  mail_data/*.json   담당자 발행분  → 정답지
  data/*.json        자동 수집분    → 테스트 결과

⚠️ 날짜 정렬
  파일 날짜로 비교하면 어긋난다.
    · 담당자 7/28자 파일 = 7.27~7.28 기사
    · 우리   7/27자 파일 = 7.25~7.27 기사
  그래서 파일이 아니라 '기사 발행일(pubdate)' 기준으로 맞춘다.

⚠️ 매칭 방법
  담당자 메일의 URL과 우리 네이버 수집 URL이 달라질 수 있어
  (원문 링크 vs 네이버 링크) URL만으로는 부족하다.
  URL이 같으면 바로 인정하고, 아니면 제목 유사도로 판정한다.

실행:
  python compare.py                하루 전 기사일 기준
  python compare.py 2026-07-27     특정 발행일
  python compare.py --days 7       최근 7일치 종합
====================================================================
"""
import os
import re
import sys
import json
import glob
import difflib
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIL_DIR = os.path.join(ROOT, "mail_data")   # 정답지
DATA_DIR = os.path.join(ROOT, "data")        # 테스트 결과
REPORT_DIR = os.path.join(ROOT, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

KST = datetime.timezone(datetime.timedelta(hours=9))

# 제목 유사도 임계값. 이 값 이상이면 같은 기사로 본다.
# 0.65 = "서산시, ..." 와 "충남 서산시, ..." 를 같은 기사로 인정하는 수준
SIMILARITY = 0.65


# ── 제목 정규화 ────────────────────────────────────────────────────
_BRACKET = re.compile(r"[\[\(【][^\]\)】]*[\]\)】]")
_NONWORD = re.compile(r"[^가-힣a-zA-Z0-9]")


def norm_title(t):
    """비교용 제목 정규화: 머리말 대괄호·기호·공백 제거"""
    t = _BRACKET.sub("", t or "")
    return _NONWORD.sub("", t).lower()


def same_article(a, b):
    """두 기사가 같은 기사인지 판정."""
    ua, ub = (a.get("url") or "").strip(), (b.get("url") or "").strip()
    if ua and ub and ua == ub:
        return True
    na, nb = norm_title(a.get("title", "")), norm_title(b.get("title", ""))
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= SIMILARITY


# ── 발행일 계산 ────────────────────────────────────────────────────
def pubdate_of(article, file_date):
    """기사의 발행일을 YYYY-MM-DD 로 만든다.
    기사에는 '7.27' 처럼 연도가 없으므로 파일 날짜에서 연도를 빌린다.
    파일보다 월이 큰 경우(연말연시)는 전년으로 본다."""
    raw = (article.get("date") or "").strip()
    m = re.match(r"(\d{1,2})[.\-/](\d{1,2})", raw)
    if not m:
        return file_date
    mo, d = int(m.group(1)), int(m.group(2))
    y, fmo = int(file_date[:4]), int(file_date[5:7])
    if mo > fmo + 1:        # 12월 기사가 1월 파일에 들어온 경우
        y -= 1
    try:
        return datetime.date(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return file_date


def load_pool(folder):
    """폴더의 모든 JSON에서 기사를 모아 발행일별로 묶는다.
    반환: {발행일: [기사, ...]}  (기사에 cat, file 정보 추가)"""
    pool = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        fname = os.path.basename(path).replace(".json", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", fname):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
        fdate = doc.get("date", fname)
        for cat, arts in (doc.get("daily") or {}).items():
            for a in arts or []:
                pd = pubdate_of(a, fdate)
                pool.setdefault(pd, []).append({**a, "cat": cat, "file": fname})
    return pool


# ── 비교 ───────────────────────────────────────────────────────────
def dedupe_within(articles):
    """같은 기사가 여러 카테고리에 중복 게재된 것을 찾는다.
    반환: (고유 기사 리스트, 중복 묶음 리스트)"""
    uniq, dups = [], []
    for a in articles:
        hit = next((u for u in uniq if same_article(u, a)), None)
        if hit:
            hit.setdefault("_also", []).append(a["cat"])
            dups.append((hit, a))
        else:
            uniq.append(dict(a))
    return uniq, dups


def compare_one(pubdate, truth, mine):
    """한 발행일에 대해 비교. 반환: 리포트 dict"""
    mine_uniq, dups = dedupe_within(mine)

    matched, missed = [], []
    used = set()
    for t in truth:
        hit = None
        for i, m in enumerate(mine_uniq):
            if i in used:
                continue
            if same_article(t, m):
                hit = (i, m)
                break
        if hit:
            used.add(hit[0])
            matched.append((t, hit[1]))
        else:
            missed.append(t)
    extra = [m for i, m in enumerate(mine_uniq) if i not in used]

    return {
        "date": pubdate,
        "truth_n": len(truth),
        "mine_n": len(mine),
        "mine_uniq_n": len(mine_uniq),
        "dup_n": len(dups),
        "dups": dups,
        "matched": matched,
        "missed": missed,
        "extra": extra,
        "src_missing": sum(1 for m in mine_uniq if not (m.get("source") or "").strip()),
    }


def render(reports):
    """리포트를 사람이 읽는 텍스트로."""
    L = []
    add = L.append
    tot_truth = sum(r["truth_n"] for r in reports)
    tot_match = sum(len(r["matched"]) for r in reports)
    tot_uniq = sum(r["mine_uniq_n"] for r in reports)
    tot_dup = sum(r["dup_n"] for r in reports)
    tot_src = sum(r["src_missing"] for r in reports)

    recall = tot_match * 100 // tot_truth if tot_truth else 0
    prec = tot_match * 100 // tot_uniq if tot_uniq else 0

    add("=" * 62)
    add("수집 성능 리포트")
    add("=" * 62)
    add(f"대상 발행일: {', '.join(r['date'] for r in reports)}")
    add("")
    add(f"  재현율  담당자 {tot_truth}건 중 {tot_match}건 포착      {recall}%")
    add(f"          ↑ 담당자가 실은 기사를 우리가 얼마나 잡았나 (높을수록 좋음)")
    add("")
    add(f"  정확도  우리 {tot_uniq}건 중 {tot_match}건 채택        {prec}%")
    add(f"          ↑ 우리가 실은 기사 중 담당자도 실은 비율")
    add("")
    if tot_dup:
        add(f"  ⚠️ 카테고리 중복 게재  {tot_dup}건")
    if tot_src:
        add(f"  ⚠️ 언론사명 누락       {tot_src}건 / {tot_uniq}건")

    for r in reports:
        add("")
        add("-" * 62)
        add(f"[{r['date']}]  담당자 {r['truth_n']}건 / 우리 {r['mine_uniq_n']}건"
            + (f" (표시 {r['mine_n']}건, 중복 {r['dup_n']})" if r["dup_n"] else ""))
        add("-" * 62)

        if r["dups"]:
            add("")
            add("  🔁 같은 기사가 여러 카테고리에 실림")
            for keep, extra_a in r["dups"]:
                add(f"     · {keep['title'][:46]}")
                add(f"         {keep['cat']}  +  {extra_a['cat']}")

        if r["missed"]:
            add("")
            add(f"  ❌ 놓친 기사 ({len(r['missed'])}건) — 담당자는 실었는데 우리는 없음")
            for a in r["missed"]:
                src = f"/{a['source']}" if a.get("source") else ""
                add(f"     · [{a.get('date','')}{src}] {a['title'][:48]}")
                add(f"         담당자 분류: {a['cat']}")

        if r["matched"]:
            add("")
            add(f"  ✅ 포착한 기사 ({len(r['matched'])}건)")
            for t, m in r["matched"]:
                same_cat = "일치" if t["cat"] == m["cat"] else f"다름 → 우리: {m['cat']}"
                add(f"     · {t['title'][:44]}")
                add(f"         카테고리 {same_cat}")

        if r["extra"]:
            add("")
            add(f"  ⚠️ 우리만 실은 기사 ({len(r['extra'])}건) — 불필요한 기사가 없는지 점검")
            for a in r["extra"]:
                add(f"     · [{a['cat'][:20]}] {a['title'][:44]}")

    add("")
    add("=" * 62)
    return "\n".join(L)


def main():
    argv = sys.argv[1:]
    days = 1
    if "--days" in argv:
        i = argv.index("--days")
        try:
            days = int(argv[i + 1])
        except (IndexError, ValueError):
            print("[오류] --days 뒤에 숫자를 넣으세요.")
            sys.exit(1)
        # --days 와 그 값을 목록에서 빼야 날짜 인자로 오해하지 않는다
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]

    truth_pool = load_pool(MAIL_DIR)
    mine_pool = load_pool(DATA_DIR)

    if not truth_pool:
        print("[중단] mail_data 에 담당자 발행분이 없습니다. 비교할 정답지가 필요합니다.")
        sys.exit(1)

    if args:
        dates = [args[0]]
    else:
        # 정답지가 있는 날짜 중 최근 N일
        dates = sorted(truth_pool.keys(), reverse=True)[:days]

    reports = []
    for d in sorted(dates):
        truth = truth_pool.get(d, [])
        mine = mine_pool.get(d, [])
        if not truth:
            print(f"[건너뜀] {d}: 담당자 발행분에 이 날짜 기사가 없습니다.")
            continue
        if not mine:
            print(f"[주의] {d}: 우리 수집분에 이 날짜 기사가 하나도 없습니다.")
        reports.append(compare_one(d, truth, mine))

    if not reports:
        print("[중단] 비교할 날짜가 없습니다.")
        sys.exit(1)

    text = render(reports)
    print(text)

    stamp = datetime.datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    path = os.path.join(REPORT_DIR, f"{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n리포트 저장: reports/{os.path.basename(path)}")


if __name__ == "__main__":
    main()
