"""
====================================================================
스티비 발송 실패 추적 (STEP 4)
====================================================================
역할: 스티비 API로 발송한 이메일의 로그를 가져와,
      발송 실패(하드/소프트 바운스)를 이메일 주소별로 누적 집계한다.
      결과를 sending_failures.json 에 저장 → 대시보드가 읽어 표시.

집계 방식 (대표님 요구):
  "각 이메일 주소마다, 그 주소로 발송한 게 지금까지 총 몇 번 실패했는지"
  → 이메일 주소를 키로, 누적 실패 횟수/최근 유형·사유/실패 이력을 저장.

중복 카운트 방지:
  이미 집계한 발송(emailId)은 processed_emails 에 기록해두고,
  새 발송분만 누적한다. (같은 발송을 두 번 세지 않음)

스티비 API:
  - 이메일 목록:      GET /v2/emails
  - 발송 상세 로그:   GET /v2/emails/{id}/logs?offset=0&limit=1000
  - 인증 헤더:        AccessToken: <API 키>
  - action 코드:      P=성공, F=소프트바운스, A/B=하드바운스,
                      D=수신거부, O=오픈, C=클릭
  → 발송 실패 = action 이 F, A, B 인 것.

계정 종속 값 (개인→협회 전환 시 이것만 교체, 코드는 그대로):
  - STIBEE_API_KEY : 스티비 API 키 (Secrets / .env)
  - 추적 대상 이메일: 자동 선별(최근 발송분) 또는 STIBEE_EMAIL_IDS 로 지정

보안:
  API 키는 절대 코드에 직접 쓰지 않는다. Secrets/.env 로만 주입.

라이브러리:
  pip install requests python-dotenv

사용법:
  python stibee.py            (실제 API 호출)
  python stibee.py --mock     (모의 데이터로 구조 테스트, 키 불필요)
====================================================================
"""
import os
import sys
import json
import glob
import hashlib
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
FAILURES_FILE = os.path.join(ROOT, "sending_failures.json")

BASE_URL = "https://api.stibee.com/v2"
API_KEY = os.getenv("STIBEE_API_KEY")

# 추적 대상 선별 규칙 ─────────────────────────────────────────────
# 제목에 이 문구가 든 이메일만 추적한다. (협회와 합의된 제목 규칙)
# 기본값이 협회 규칙이므로 Secrets 없이도 작동한다.
# 규칙이 바뀌면 Secrets/.env 의 STIBEE_SUBJECT_FILTER 에 새 문구를 넣으면
# 코드 수정 없이 그 값이 적용된다.
# ※ 환경변수가 비어 있으면(미등록 포함) 기본값을 쓴다 —
#   빈 값 때문에 필터가 꺼져 전체 이메일이 추적되는 사고를 막기 위함.
_DEFAULT_SUBJECT_FILTER = "한국자동차환경협회 뉴스 모니터링"
SUBJECT_FILTER = os.getenv("STIBEE_SUBJECT_FILTER", "").strip() or _DEFAULT_SUBJECT_FILTER

# 이메일 상태 코드 (스티비): 0=작성중, 1=예약중, 21·22=발송중, 3=발송완료
# 발송 완료된 것만 로그가 있으므로 3만 조회한다.
STATUS_SENT = 3

# 실패로 간주하는 action 코드와 유형 라벨
FAIL_ACTIONS = {
    "F": "소프트바운스",
    "A": "하드바운스",   # 주소록 자동삭제 적용
    "B": "하드바운스",   # 주소록 자동삭제 미적용
}

KST = datetime.timezone(datetime.timedelta(hours=9))


# ── 개인정보 보호 ────────────────────────────────────────────────
# 이 저장소는 public 이고 대시보드도 공개 URL 로 게시되므로,
# 구독자 이메일 원본을 절대 저장하지 않는다.
#  · 키(식별)  : 원본 주소의 해시 → 같은 주소를 정확히 누적하되 원본은 남기지 않음
#  · 표시(화면): 부분 마스킹된 문자열 → 앞 3글자만 노출
# 담당자는 대시보드로 '반복 실패 주소'를 파악하고,
# 정확한 주소는 스티비 [상세 통계] → [발송 실패] 에서 확인한다.
MASK_KEEP = 3   # 앞에서 노출할 글자 수


def mask_email(addr):
    """이메일을 부분 마스킹. 예) hong.gildong@hyundai.com → hon*********@hyu****.com"""
    addr = (addr or "").strip()
    if "@" not in addr:
        return "***"

    local, _, domain = addr.partition("@")

    def _mask(s, keep=MASK_KEEP):
        if not s:
            return "***"
        if len(s) <= keep:
            # 짧으면 최소 1글자는 가린다
            return s[0] + "*" * max(1, len(s) - 1)
        return s[:keep] + "*" * (len(s) - keep)

    # 도메인은 최상위(.com/.kr 등)만 남기고 이름 부분을 가린다
    if "." in domain:
        name, _, tld = domain.rpartition(".")
        masked_domain = f"{_mask(name)}.{tld}"
    else:
        masked_domain = _mask(domain)

    return f"{_mask(local)}@{masked_domain}"


def addr_key(addr):
    """원본 주소의 해시(식별용). 원본을 저장하지 않고도 같은 주소를 누적하기 위함."""
    return hashlib.sha256((addr or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def _headers():
    return {"AccessToken": API_KEY, "Content-Type": "application/json"}


def get_email_list():
    """이메일 목록 조회 후 '추적 대상'만 추려서 반환.
    조건 ① status == 3 (발송 완료) — 작성중/예약중은 로그가 없어 조회할 필요가 없다.
    조건 ② 제목에 SUBJECT_FILTER 문구 포함 — 우리 뉴스레터만 추적(협회 다른 메일 제외).
    반환: [{id, subject, ...}, ...]"""
    try:
        resp = requests.get(f"{BASE_URL}/emails", headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # 응답 구조는 계정/버전에 따라 다를 수 있어 유연하게 처리
        if isinstance(data, dict):
            items = data.get("items") or data.get("list") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
    except Exception as e:
        print(f"[이메일 목록 조회 실패] {e}")
        return []

    total = len(items)
    # ① 발송 완료만 (작성중·예약중은 로그가 없음)
    sent = [e for e in items if e.get("status") == STATUS_SENT]
    # ② 제목 규칙에 맞는 것만 (협회의 다른 이메일 제외)
    targets = [e for e in sent if SUBJECT_FILTER in (e.get("subject") or "")]

    print(f"이메일 목록: 전체 {total}건 → 발송완료 {len(sent)}건 → 추적대상 {len(targets)}건")
    print(f"  (제목 규칙: '{SUBJECT_FILTER}' 포함)")
    return targets


def get_email_logs(email_id):
    """특정 이메일의 발송 로그 전체 조회 (페이지네이션). 반환: [log, ...]"""
    logs = []
    offset = 0
    limit = 1000
    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/emails/{email_id}/logs",
                headers=_headers(),
                params={"offset": offset, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    [로그 조회 실패] emailId={email_id}: {e}")
            break

        # 스티비는 로그가 없으면 items 를 null 로 줄 수 있다 → None 방어
        items = data.get("items") or []
        logs.extend(items)
        total = data.get("total") or len(logs)
        offset += limit
        if offset >= total or not items:
            break
    return logs


def extract_failures(logs):
    """로그에서 발송 실패(F/A/B)만 뽑아 정리.
    반환: [{subscriber, type, reason, time}, ...]"""
    failures = []
    for log in logs:
        action = log.get("action", "")
        if action in FAIL_ACTIONS:
            failures.append({
                "subscriber": log.get("subscriber", ""),
                "type": FAIL_ACTIONS[action],
                "reason": (log.get("value2", "") or "").replace("\n", " ").strip(),
                "time": log.get("createdTime", ""),
            })
    return failures


def load_state():
    """기존 누적 상태 로드. 없으면 초기 구조."""
    if os.path.exists(FAILURES_FILE):
        try:
            with open(FAILURES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"by_address": {}, "processed_emails": [], "updated": ""}


def accumulate(state, email_id, failures):
    """이 발송(email_id)의 실패들을 주소별로 누적.
    이미 집계한 email_id면 건너뛴다 (중복 방지).
    ※ 개인정보 보호: 키는 원본 주소의 해시, 화면 표시는 마스킹된 값만 저장한다.
      (마스킹된 값을 키로 쓰면 서로 다른 주소가 같은 키로 합쳐질 수 있으므로 해시를 쓴다)"""
    if email_id in state["processed_emails"]:
        return 0   # 이미 집계함

    added = 0
    for f in failures:
        addr = f["subscriber"]
        if not addr:
            continue
        key = addr_key(addr)
        rec = state["by_address"].get(key, {
            "display": mask_email(addr),
            "count": 0, "last_type": "", "last_reason": "", "last_date": "", "history": [],
        })
        rec["display"] = mask_email(addr)   # 기존 기록에 display가 없던 경우 대비
        rec["count"] += 1
        rec["last_type"] = f["type"]
        rec["last_reason"] = f["reason"]
        date_only = (f["time"] or "")[:10]
        rec["last_date"] = date_only
        rec["history"].append({"date": date_only, "type": f["type"], "email_id": email_id})
        state["by_address"][key] = rec
        added += 1

    state["processed_emails"].append(email_id)
    return added


def save_state(state):
    state["updated"] = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    with open(FAILURES_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_mock():
    """모의 데이터로 누적 구조 테스트 (API 키 불필요)."""
    print("[모의 모드] 가상 발송 2건으로 누적 테스트")
    state = load_state()

    # 가상 발송 #1001
    logs1 = [
        {"subscriber": "aaa@example.com", "action": "A", "value2": "550 no such user", "createdTime": "2026-07-06T07:20:00+09:00"},
        {"subscriber": "bbb@example.com", "action": "F", "value2": "450 mailbox busy", "createdTime": "2026-07-06T07:20:01+09:00"},
        {"subscriber": "ccc@example.com", "action": "P", "value2": "250 ok", "createdTime": "2026-07-06T07:20:02+09:00"},
    ]
    added1 = accumulate(state, 1001, extract_failures(logs1))
    # 가상 발송 #1002 (aaa 또 실패)
    logs2 = [
        {"subscriber": "aaa@example.com", "action": "A", "value2": "550 no such user", "createdTime": "2026-07-07T07:20:00+09:00"},
        {"subscriber": "ddd@example.com", "action": "B", "value2": "553 blocked", "createdTime": "2026-07-07T07:20:01+09:00"},
    ]
    added2 = accumulate(state, 1002, extract_failures(logs2))
    # 같은 발송 재실행(중복 방지 확인)
    added_dup = accumulate(state, 1001, extract_failures(logs1))

    save_state(state)
    print(f"  발송#1001 실패 {added1}건, 발송#1002 실패 {added2}건, 재실행 중복추가 {added_dup}건(0이어야 정상)")
    print(f"  누적 주소 수: {len(state['by_address'])}")
    for key, rec in sorted(state["by_address"].items(), key=lambda x: -x[1]["count"]):
        print(f"    {rec.get('display','***')}: {rec['count']}회 실패 ({rec['last_type']})")


def main():
    if "--mock" in sys.argv:
        run_mock()
        return

    if not API_KEY:
        print("[오류] STIBEE_API_KEY 가 없습니다.")
        print("  로컬: .env 에 STIBEE_API_KEY=...")
        print("  자동: GitHub Secrets 에 STIBEE_API_KEY 등록")
        print("  (구조만 테스트하려면: python stibee.py --mock)")
        sys.exit(1)

    state = load_state()

    # 추적 대상 이메일 선정
    #  - STIBEE_EMAIL_IDS 가 있으면 그 ID만 (수동 지정)
    #  - 없으면 목록에서 '발송완료 + 제목 규칙'에 맞는 것만 자동 선별
    id_env = os.getenv("STIBEE_EMAIL_IDS", "").strip()
    subjects = {}
    if id_env:
        target_ids = [int(x) for x in id_env.split(",") if x.strip().isdigit()]
        print(f"추적 대상: STIBEE_EMAIL_IDS 로 지정된 {len(target_ids)}건")
    else:
        emails = get_email_list()
        target_ids = [e.get("id") for e in emails if e.get("id")]
        subjects = {e.get("id"): e.get("subject", "") for e in emails}
        for e in emails:
            print(f"  · [{e.get('id')}] {e.get('subject','')}")

    if not target_ids:
        print("[안내] 추적할 이메일이 없습니다.")
        print("  (발송 완료된 이메일 중 제목 규칙에 맞는 것이 없거나, 목록 조회 실패)")
        save_state(state)
        return

    total_added = 0
    for eid in target_ids:
        if eid in state["processed_emails"]:
            continue
        logs = get_email_logs(eid)
        failures = extract_failures(logs)
        added = accumulate(state, eid, failures)
        total_added += added
        print(f"  이메일 {eid}: 로그 {len(logs)}건 중 실패 {len(failures)}건 누적")

    save_state(state)
    print(f"\n완료: sending_failures.json 갱신 (신규 실패 {total_added}건, 누적 주소 {len(state['by_address'])}개)")


if __name__ == "__main__":
    main()
