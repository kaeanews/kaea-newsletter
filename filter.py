"""
====================================================================
한국자동차환경협회 뉴스 웹진 - 중복제거 + 부정필터 + 요약 + 통계 (STEP 2)
====================================================================
역할: collect.py 가 만든 data/YYYY-MM-DD.json 을 읽어서,
      ① 중복 기사 제거 (Claude 의미 기반, 카테고리별 1회 호출)
      ② 부정 뉴스 제거 (부정 키워드 1차 + Claude 2차)
      ③ 각 기사 요약 2~3줄 재작성
      ④ 웹진 한 줄 요약 생성
      ⑤ 그날 통계를 stats.json 에 누적 (대시보드용)
      ⑥ 부정 차단된 기사를 data JSON 의 blocked 에 저장 (대시보드 검토용)  ← 이번 추가
      후 data JSON 을 덮어써 저장한다.

차단 기사 저장(blocked):
  부정 차단(HARD 키워드 / AI 판정)된 기사만 blocked 에 담는다.
  협회 담당자가 대시보드 'AI 차단 검토' 탭에서 "혹시 잘못 걸러진 뉴스가 없나" 확인용.
  ※ 중복 제거된 기사는 담지 않는다(건수만 통계에 반영).

API 키:
  - 로컬: .env 의 ANTHROPIC_API_KEY
  - 자동: GitHub Secrets 의 ANTHROPIC_API_KEY

사용법:
  python filter.py            (data 최신 날짜)
  python filter.py 2026-07-08 (특정 날짜)
====================================================================
"""
import os
import sys
import json
import glob

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from negative_keywords import HARD_NEGATIVE, SOFT_NEGATIVE

try:
    from anthropic import Anthropic
except ImportError:
    print("[오류] anthropic 라이브러리가 없습니다: pip install anthropic python-dotenv")
    raise SystemExit(1)

API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not API_KEY:
    print("[오류] Claude API 키가 없습니다.")
    print("  로컬: .env 에 ANTHROPIC_API_KEY=sk-ant-...")
    print("  자동: GitHub Secrets 에 ANTHROPIC_API_KEY 등록")
    raise SystemExit(1)

client = Anthropic(api_key=API_KEY)
MODEL = "claude-haiku-4-5-20251001"

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
STATS_FILE = os.path.join(ROOT, "stats.json")


def has_keyword(text, keywords):
    return any(kw in text for kw in keywords)


def matched_hard(text):
    """제목에 든 HARD 부정 키워드들을 반환 (차단 사유 표시용)."""
    return [kw for kw in HARD_NEGATIVE if kw in text]


def dedupe_category(articles):
    """카테고리 내 중복 기사를 Claude로 판정해 제거한다.
    반환: (남긴 기사 리스트, 제거된 개수)"""
    if len(articles) <= 1:
        return articles, 0

    titles_text = "\n".join(f"{i+1}. {a.get('title','')}" for i, a in enumerate(articles))
    prompt = f"""아래는 같은 카테고리로 수집된 뉴스 제목 목록입니다.
같은 사건·내용을 다룬 중복 기사를 찾아 묶어주세요.
표현이 달라도(예: '돌입'='시작'='개시', '합작사'='합작법인') 같은 사건이면 중복입니다.

각 중복 그룹에서는 제목이 가장 완결되고 정보가 풍부한 기사 1건의 번호를 'keep'으로 지정하고,
나머지 중복 번호를 'remove'에 넣으세요. 중복이 없는 기사는 어디에도 넣지 않습니다.

제목 목록:
{titles_text}

아래 JSON 형식으로만 답하세요. 다른 말은 하지 마세요.
{{"groups": [{{"keep": 번호, "remove": [번호, ...]}}, ...]}}
중복이 전혀 없으면 {{"groups": []}} 로 답하세요."""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
    except Exception as e:
        print(f"    [중복 판정 실패 - 전부 유지] ({e})")
        return articles, 0

    remove_idx = set()
    for grp in result.get("groups", []):
        for n in grp.get("remove", []):
            if isinstance(n, int) and 1 <= n <= len(articles):
                remove_idx.add(n - 1)
    kept = [a for i, a in enumerate(articles) if i not in remove_idx]
    return kept, len(remove_idx)


# 카테고리별 '실제 주제' 설명 — AI 관련성 판정의 기준이 된다.
# 키워드가 본문에 스쳐 지나가기만 한 무관 기사를 걸러내기 위함.
# (예: 편의점 해외진출 기사에 '전기차 충전소'가 부대시설로 한 번 언급된 경우)
CATEGORY_TOPICS = {
    "한국자동차환경협회 뉴스":
        "한국자동차환경협회(자동차환경협회, AEA)의 활동·사업·발표·행사",
    "상위기관 뉴스":
        "정부·공공기관(기후에너지환경부 등)의 자동차 환경 관련 정책·제도·법령·사업",
    "배출 저감사업 뉴스":
        "자동차 배출가스 저감사업(조기폐차, 매연저감장치(DPF), 저공해차 전환, 배출가스 검사, 운행차 관리 등)",
    "전기·수소차 뉴스 - 협회 사업 관련 뉴스":
        "전기차·수소차 충전 인프라(충전소·충전기의 구축·운영·보조금·고장·관리 문제) "
        "및 전기·수소 건설기계/지게차, 전기·수소 차량 개조 사업",
    "전기·수소차 뉴스 - 업계 동향":
        "전기차·수소차 차량과 배터리, 완성차·부품 업계의 시장·기술·판매·정책 동향",
    "회원사 뉴스":
        "협회 회원사(자동차 환경 관련 기업)의 사업·기술·투자·경영 소식",
    "기타 뉴스":
        "친환경 모빌리티, 폐배터리 재활용, V2G 등 자동차 환경 관련 주제",
    "유럽 (EU)": "유럽의 자동차 환경·배출 규제, 전기차·수소차 정책 및 시장 동향",
    "미국 (USA)": "미국의 자동차 환경·배출 규제, 전기차·수소차 정책 및 시장 동향",
    "중국 (China)": "중국의 자동차 환경·배출 규제, 전기차·수소차 정책 및 시장 동향",
    "글로벌 종합": "글로벌 자동차 환경·전기차·수소차 정책 및 시장 동향",
}
DEFAULT_TOPIC = "자동차 환경 관련 주제"


def judge_and_rewrite(article, cat_name=""):
    """Claude 1회 호출로 ①카테고리 관련성 ②부정 판정 ③요약 재작성.
    반환: (관련여부, 적절여부, 새요약)
    ※ 관련성 판정을 기존 호출에 합쳐 처리하므로 API 비용이 늘지 않는다."""
    title = article.get("title", "")
    summary = article.get("summary", "")
    topic = CATEGORY_TOPICS.get(cat_name, DEFAULT_TOPIC)
    prompt = f"""당신은 한국자동차환경협회가 회원사에게 보내는 뉴스 웹진의 편집자입니다.
아래 기사가 ①이 카테고리 주제에 실제로 관한 기사인지, ②회원사 대상 뉴스로 적절한지 판단하고, 요약을 다듬어 주세요.

[관련성 판단 기준]
이 기사는 '{cat_name}' 카테고리에 실릴 예정입니다.
카테고리 주제: {topic}

- 관련 있음(true): 기사의 핵심 주제가 위 카테고리 주제에 해당함
- 관련 없음(false): 위 주제의 단어가 본문에 한두 번 스쳐 지나갈 뿐,
  기사의 실제 주제는 전혀 다른 것

  판단 예시)
  · 편의점의 해외 진출 기사에서 매장 부대시설로 '전기차 충전소'가 한 번 언급 → 관련 없음
  · 기업인 프로필 기사에서 경력 중 하나로 '수소충전소 실증'이 언급 → 관련 없음
  · 버스 준공영제 비리 기사에서 대표의 사업 목록에 '충전소'가 언급 → 관련 없음
  · 제주 수소버스 도입 기사에서 '충전소 부족'이 핵심 쟁점으로 다뤄짐 → 관련 있음
  · 방치된 충전기와 복구 문제를 다룬 기획기사 → 관련 있음

[적절성 판단 기준]
협회가 회원사에게 보내는 뉴스입니다.
회원사에게 도움이 되는 정보성 기사만 싣고, 부정적인 내용은 싣지 않습니다.
아래에 하나라도 해당하면 부적절(false)로 판정하세요.

부적절 ① 특정 기업·기관의 사고·사건
  - 해킹, 개인정보·기술 유출, 전산 장애
  - 화재·폭발·감전 등 사고, 인명 피해
  - 제품 결함·리콜·성능 미달·품질 문제
  - 소송·수사·제재·과징금·비리·담합
  - 부도·법정관리·보조금 먹튀·계약 불이행 피해
  예) "전기차 충전업체 OO 해킹…회원정보 29만여건 유출" → 부적절
  예) "OO충전기 업체 보조금 받고 폐업" → 부적절

부적절 ② 산업에 대한 불안·공포를 조장하는 내용
  - 전기차 화재 위험을 자극적으로 부각
  - 근거 없는 전기차 무용론·위험론
  예) "또 터진 전기차 화재…지하주차장 공포 확산" → 부적절

부적절 ③ 정책·제도·인프라·시장에 대한 비판, 문제 제기, 부정적 전망
  - 제도의 허점·부실을 지적하거나 개선을 요구하는 내용
  - 인프라의 부실·방치·미흡을 지적하는 내용
  - 산업의 침체·위기·부진을 진단하거나 전망하는 내용
  - 정책의 실패·역효과를 주장하는 칼럼·시론·사설
  예) "방치된 충전기, 복구는 또 다른 산" → 부적절 (인프라 부실 지적)
  예) "美 전기차 지원 정책의 역설" → 부적절 (정책 비판)
  예) "유럽 자동차산업, 구조적 침체와 공세 직면" → 부적절 (산업 위기 진단)
  예) "[로터리] 전기차 숫자의 착시" → 부적절 (부정적 전망)
  예) "무법지대의 '촉법소년' 테슬라 FSD" → 부적절 (제도 공백 비판)

적절: 사실 전달 중심의 정보성 기사
  - 정책·제도의 신설·시행·개정 발표
  - 기술 개발·연구 성과, 실증 사업
  - 보급 실적, 시장 성장, 판매 동향
  - 충전 인프라 구축·확충·지원사업
  - 기관·기업의 사업 추진, 협약, 행사
  ※ 문제 상황이 배경으로 언급되더라도, 기관·기업의 대응이나 해결책 추진이
     기사의 중심이면 '적절'입니다.
  예) "수소엔진차 배출가스 인증기준 마련…시험절차 도입" → 적절 (제도 신설)
  예) "산청군, 충전소 단전에 이동형 전기차 충전차 투입" → 적절 (지자체 대응이 중심)
  예) "성남시, 전기차 충전시설 화재예방 최대 200만원 지원" → 적절 (지원사업)
  예) "LG화학, 그린수소 생산 전극 수명 2배 향상" → 적절 (기술 성과)

[요약 기준]
- 2~3문장으로 핵심만 간결하게, 객관적 정보 전달 문체
- 원문에 없는 내용을 지어내지 말 것
- 모든 문장을 명사형 종결어미(~임, ~됨, ~함, ~ㅁ)로 끝낼 것. '~입니다', '~이다', '~한다' 등은 쓰지 말 것.
  예시) "전기차 보급이 확대됨. 정부가 보조금을 늘릴 계획임. 충전 인프라도 함께 구축될 예정임."

기사 제목: {title}
기사 원문 요약: {summary}

아래 JSON 형식으로만 답하세요.
{{"relevant": true 또는 false, "appropriate": true 또는 false, "summary": "다듬은 2~3문장 요약"}}"""
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        # relevant 키가 없으면(구버전 응답 등) 통과시켜 기사가 사라지지 않도록 한다
        relevant = bool(result.get("relevant", True))
        return relevant, bool(result.get("appropriate", False)), result.get("summary", summary).strip()
    except Exception as e:
        print(f"    [AI 판정 실패 - 원본 유지] {title[:30]}... ({e})")
        return True, True, summary


def process_category(cat_name, articles):
    """카테고리 하나: 중복 제거 → 부정 필터 + 요약.
    반환: (통과 기사, 통계, 차단된 기사 리스트)"""
    stats = {"input": len(articles), "dup": 0, "hard": 0, "ai": 0, "kept": 0}
    blocked = []
    if not articles:
        print(f"  [{cat_name}] 0건")
        return [], stats, blocked

    # ① 중복 제거 (차단 목록에는 넣지 않음)
    articles, dup_removed = dedupe_category(articles)
    stats["dup"] = dup_removed

    # ② 부정 필터 + 요약
    kept = []
    for art in articles:
        title = art.get("title", "")
        hits = matched_hard(title)
        if hits:                                   # 1차: HARD 부정어 → 차단
            stats["hard"] += 1
            blocked.append({
                "title": title,
                "summary": art.get("summary", ""),
                "url": art.get("url", ""),
                "category": cat_name,
                "reason": "부정 키워드",
                "detail": ", ".join(hits),
            })
            continue
        # 2차: Claude 1회 호출로 관련성 + 적절성 + 요약을 한꺼번에 처리
        relevant, appropriate, new_summary = judge_and_rewrite(art, cat_name)
        if not appropriate:
            stats["ai"] += 1
            blocked.append({
                "title": title,
                "summary": art.get("summary", ""),
                "url": art.get("url", ""),
                "category": cat_name,
                "reason": "AI 판정",
                "detail": "부적절/자극적 내용으로 판정",
            })
            continue
        if not relevant:
            # 키워드만 스쳐 지나간 무관 기사 (예: 편의점 기사에 '충전소' 한 번 언급)
            # ※ 통계는 기존 'ai' 항목에 합산해 stats.json 구조를 그대로 유지한다.
            stats["ai"] += 1
            blocked.append({
                "title": title,
                "summary": art.get("summary", ""),
                "url": art.get("url", ""),
                "category": cat_name,
                "reason": "AI 판정",
                "detail": "카테고리 주제와 무관한 기사로 판정",
            })
            continue
        art["summary"] = new_summary
        kept.append(art)
        stats["kept"] += 1

    print(f"  [{cat_name}] 통과 {len(kept)}건 (중복제거 {stats['dup']}, 부정제외 {stats['hard']+stats['ai']})")
    return kept, stats, blocked


def filter_all(cat_dict):
    result = {}
    per_cat = {}
    all_blocked = []
    total = {"input": 0, "dup": 0, "hard": 0, "ai": 0, "kept": 0}
    for cat_name, articles in cat_dict.items():
        kept, stats, blocked = process_category(cat_name, articles)
        result[cat_name] = kept
        per_cat[cat_name] = stats["kept"]
        all_blocked.extend(blocked)
        for k in total:
            total[k] += stats[k]
    return result, total, per_cat, all_blocked


def make_edition_summary(daily):
    titles = [arts[0].get("title", "") for arts in daily.values() if arts]
    if not titles:
        return ""
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
        return resp.content[0].text.strip().strip('"')
    except Exception as e:
        print(f"  [웹진 요약 생성 실패] ({e})")
        return ""


def update_stats(date_str, total_d, total_g, per_cat_d):
    """그날 통계를 stats.json 에 누적한다 (같은 날짜는 갱신)."""
    collected = total_d["input"] + total_g["input"]
    dup = total_d["dup"] + total_g["dup"]
    hard = total_d["hard"] + total_g["hard"]
    ai = total_d["ai"] + total_g["ai"]
    kept = total_d["kept"] + total_g["kept"]

    entry = {
        "date": date_str,
        "collected": collected,
        "duplicates_removed": dup,
        "negative_blocked": hard + ai,
        "hard_blocked": hard,
        "ai_blocked": ai,
        "final_published": kept,
        "by_category": per_cat_d,
    }

    stats = []
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                stats = json.load(f)
        except Exception:
            stats = []

    stats = [s for s in stats if s.get("date") != date_str]
    stats.append(entry)
    stats.sort(key=lambda s: s.get("date", ""))

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    return entry


def main():
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
        if not files:
            print("[오류] data 폴더에 JSON 파일이 없습니다. collect.py 를 먼저 실행하세요.")
            sys.exit(1)
        date_str = os.path.basename(files[-1]).replace(".json", "")

    data_path = os.path.join(DATA_DIR, f"{date_str}.json")
    if not os.path.exists(data_path):
        print(f"[오류] 데이터 파일이 없습니다: {data_path}")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"중복제거 + 부정필터 + 요약 + 통계: {date_str}")
    print("=" * 50)

    print("[국내 뉴스]")
    daily, total_d, per_cat_d, blocked_d = filter_all(data.get("daily", {}))

    print("\n[해외 뉴스]")
    global_news, total_g, _, blocked_g = filter_all(data.get("global", {}))

    print("\n[웹진 한 줄 요약]")
    edition_summary = make_edition_summary(daily)
    print(f"  요약: {edition_summary}")

    data["daily"] = daily
    data["global"] = global_news
    data["summary"] = edition_summary
    data["blocked"] = blocked_d + blocked_g       # 부정 차단 기사 (대시보드 검토용)

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    entry = update_stats(date_str, total_d, total_g, per_cat_d)

    print("\n" + "=" * 50)
    print(f"완료: data/{date_str}.json 갱신 + stats.json 누적")
    print(f"  수집 {entry['collected']}건 → 중복제거 {entry['duplicates_removed']}, "
          f"부정차단 {entry['negative_blocked']}, 최종 {entry['final_published']}건")
    print(f"  차단 기사 {len(data['blocked'])}건 저장 (대시보드 검토용)")


if __name__ == "__main__":
    main()
