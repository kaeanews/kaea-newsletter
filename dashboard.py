"""
====================================================================
협회 운영 대시보드 생성 모듈 (dashboard.py)
====================================================================
generate.py 가 호출한다. stats.json(통계 누적) + 오늘 data + keywords 를
읽어 dashboard.html 을 만든다. 웹진과 같은 저장소에 배포되며,
협회 담당자가 별도 웹 주소로 접근한다.

4개 탭 (첨부 v1.2 포맷 기반, 구현 가능한 것만):
  ① 오늘의 뉴스 모니터링 - 오늘 통계 + 카테고리별 목록 + 날짜별 추이
  ② AI 차단 검토        - 중복 제거·부정 차단 건수
  ③ 발송 실패 추적      - 빈 탭 (준비 중 · 스티비 연동 예정)
  ④ 키워드 관리         - 현재 키워드 목록 조회

주의:
  - 없는 데이터(신뢰도 %, 언론사명, 개별 차단 기사 목록)는 넣지 않는다.
  - 추이 그래프는 stats.json 이 며칠 쌓여야 의미가 생긴다.
====================================================================
"""
import json
import os
import html

DASH_CSS = """
  :root {
    --primary: #1F4E79; --primary-light: #E6F1FB; --primary-dark: #163C5E;
    --danger: #A32D2D; --danger-light: #FCEBEB; --green: #2E7D46; --green-light: #E9F5EE;
    --warning: #B8860B;
    --text: #1A1A1A; --text-sub: #555; --text-mute: #888;
    --border: #E5E5E5; --bg: #F8F9FB; --card: #FFFFFF;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }
  header { background: var(--primary); color: white; padding: 16px 28px;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
  .logo { font-size: 16px; font-weight: 700; }
  .logo small { font-size: 12px; font-weight: 400; opacity: 0.85; margin-left: 8px; }
  .lock { font-size: 11px; background: rgba(255,255,255,0.18); padding: 3px 10px;
    border-radius: 12px; margin-left: 10px; }
  .header-meta { font-size: 12px; opacity: 0.9; display: flex; align-items: center; gap: 8px; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #6EE7A0; display: inline-block; }
  .tabs { background: var(--card); border-bottom: 2px solid var(--border); padding: 0 20px;
    display: flex; gap: 4px; flex-wrap: wrap; }
  .tab { padding: 14px 18px; cursor: pointer; font-size: 14px; color: var(--text-sub);
    border-bottom: 3px solid transparent; margin-bottom: -2px; transition: all 0.15s; }
  .tab:hover { color: var(--primary); }
  .tab.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 700; }
  .tab .badge { background: var(--primary-light); color: var(--primary); font-size: 11px;
    padding: 1px 7px; border-radius: 10px; margin-left: 6px; font-weight: 700; }
  .tab .badge.alert { background: var(--danger-light); color: var(--danger); }
  main { max-width: 1000px; margin: 0 auto; padding: 24px 20px 48px; }
  .page { display: none; }
  .page.active { display: block; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 22px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .stat-label { font-size: 12px; color: var(--text-mute); margin-bottom: 6px; }
  .stat-value { font-size: 28px; font-weight: 800; color: var(--text); }
  .stat-value small { font-size: 13px; font-weight: 400; color: var(--text-mute); }
  .stat-value.green { color: var(--green); }
  .stat-value.danger { color: var(--danger); }
  .stat-value.primary { color: var(--primary); }
  .stat-sub { font-size: 11px; color: var(--text-mute); margin-top: 4px; }
  .info-box { background: var(--primary-light); border-left: 4px solid var(--primary);
    padding: 14px 18px; border-radius: 4px; font-size: 13px; color: #0C2C4A; margin-bottom: 22px; }
  .info-box strong { color: var(--primary); }
  .section-title { font-size: 15px; font-weight: 700; margin: 26px 0 12px; color: var(--text); }
  .cat-section { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    margin-bottom: 14px; overflow: hidden; }
  .cat-head { background: var(--primary); color: white; padding: 12px 18px; display: flex;
    justify-content: space-between; align-items: center; }
  .cat-name { font-size: 14.5px; font-weight: 700; }
  .cat-count { font-size: 12px; opacity: 0.9; }
  .news-item { padding: 14px 18px; border-bottom: 1px solid #F0F0F0; }
  .news-item:last-child { border-bottom: none; }
  .news-title { font-size: 14.5px; font-weight: 600; margin-bottom: 5px; }
  .news-title a { color: var(--primary); text-decoration: none; }
  .news-title a:hover { text-decoration: underline; }
  .news-summary { font-size: 13px; color: var(--text-sub); }
  .news-meta { font-size: 11.5px; color: var(--text-mute); margin-top: 5px; }
  .chart-box { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 20px; margin-bottom: 22px; }
  .chart-title { font-size: 14px; font-weight: 700; margin-bottom: 14px; }
  .bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 12.5px; }
  .bar-label { width: 132px; text-align: right; color: var(--text-sub); flex-shrink: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar-track { flex: 1; background: #EEF1F4; border-radius: 4px; height: 20px; position: relative; }
  .bar-fill { background: var(--primary); height: 100%; border-radius: 4px; min-width: 2px; }
  .bar-val { width: 40px; font-weight: 700; color: var(--primary); flex-shrink: 0; }
  .trend { display: flex; align-items: flex-end; gap: 6px; height: 170px; padding: 10px 0 0;
    border-bottom: 1px solid var(--border); }
  .trend-col { flex: 1; height: 100%; display: flex; flex-direction: column;
    justify-content: flex-end; align-items: center; min-width: 20px; }
  .trend-bars { display: flex; gap: 5px; align-items: flex-end; height: 135px; }
  .trend-barwrap { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; }
  .trend-num { font-size: 11px; font-weight: 700; margin-bottom: 3px; }
  .trend-num.col { color: var(--primary); }
  .trend-num.pub { color: var(--green); }
  .trend-bar { width: 14px; background: var(--primary); border-radius: 3px 3px 0 0; min-height: 3px; }
  .trend-bar.pub { background: var(--green); }
  .trend-date { font-size: 10px; color: var(--text-mute); margin-top: 4px; }
  .trend-legend { font-size: 11px; color: var(--text-mute); margin-top: 8px; display: flex; gap: 16px; }
  .legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
  .kw-group { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px; margin-bottom: 12px; }
  .kw-cat { font-size: 14px; font-weight: 700; color: var(--primary); margin-bottom: 10px; }
  .kw-label { font-size: 11px; color: var(--text-mute); font-weight: 700; margin: 8px 0 4px; }
  .kw-chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: var(--primary-light); color: var(--primary); font-size: 12px;
    padding: 3px 10px; border-radius: 12px; }
  .chip.gen { background: #F0F0F0; color: var(--text-sub); }
  .empty-tab { text-align: center; padding: 60px 20px; color: var(--text-mute); }
  .empty-tab .big { font-size: 40px; margin-bottom: 12px; }
  .empty-tab .msg { font-size: 15px; font-weight: 600; color: var(--text-sub); margin-bottom: 6px; }
  .empty-tab .sub { font-size: 13px; }
  .blocked-item { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--danger);
    border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }
  .blocked-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .reason-tag { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 10px; }
  .reason-tag.reason-kw { background: var(--danger-light); color: var(--danger); }
  .reason-tag.reason-ai { background: #FFF4E0; color: var(--warning); }
  .blocked-cat { font-size: 11px; color: var(--text-mute); }
  .blocked-title { font-size: 14.5px; font-weight: 600; margin-bottom: 5px; }
  .blocked-title a { color: var(--text); text-decoration: none; }
  .blocked-title a:hover { text-decoration: underline; color: var(--primary); }
  .blocked-summary { font-size: 13px; color: var(--text-sub); margin-bottom: 6px; }
  .blocked-detail { font-size: 12px; color: var(--danger); }
  .fail-item { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--danger);
    border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }
  .fail-main { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .fail-addr { font-size: 14.5px; font-weight: 700; color: var(--text); word-break: break-all; }
  .fail-count { font-size: 13px; font-weight: 700; color: var(--danger); background: var(--danger-light);
    padding: 3px 12px; border-radius: 12px; flex-shrink: 0; margin-left: 10px; }
  .fail-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .fail-detail { font-size: 12px; color: var(--text-sub); }
  .fail-history { font-size: 11.5px; color: var(--text-mute); }
  .footer { text-align: center; padding: 24px; font-size: 12px; color: var(--text-mute); }
"""


def _esc(t):
    return html.escape(str(t), quote=True)


def _bar(label, value, maxval):
    pct = int((value / maxval) * 100) if maxval else 0
    return f"""    <div class="bar-row">
      <div class="bar-label">{_esc(label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
      <div class="bar-val">{value}</div>
    </div>
"""


def _today_stat_from_stats(stats, date_str):
    for s in stats:
        if s.get("date") == date_str:
            return s
    return None


def build_dashboard(today, stats, keywords_data):
    """대시보드 HTML 생성.
    today: 오늘 data dict / stats: stats.json 리스트 / keywords_data: (KEYWORDS, MEMBERS) """
    date_str = today.get("date", "")
    dow = today.get("dow", "")

    # 발송 실패 누적 데이터 로드 (stibee.py가 만든 sending_failures.json)
    failures_data = {"by_address": {}, "updated": ""}
    _ffile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sending_failures.json")
    if os.path.exists(_ffile):
        try:
            with open(_ffile, "r", encoding="utf-8") as f:
                failures_data = json.load(f)
        except Exception:
            pass

    # 오늘 통계 (stats.json에서 오늘 항목 찾기)
    st = _today_stat_from_stats(stats, date_str) or {
        "collected": 0, "duplicates_removed": 0, "negative_blocked": 0,
        "hard_blocked": 0, "ai_blocked": 0, "final_published": 0, "by_category": {},
    }

    # ── 상단 통계 카드 ──
    stat_cards = f"""
    <div class="stat"><div class="stat-label">수집된 기사</div>
      <div class="stat-value">{st['collected']}<small>건</small></div>
      <div class="stat-sub">네이버 검색 API</div></div>
    <div class="stat"><div class="stat-label">중복 제거</div>
      <div class="stat-value primary">{st['duplicates_removed']}<small>건</small></div>
      <div class="stat-sub">AI 의미 판정</div></div>
    <div class="stat"><div class="stat-label">부정 차단</div>
      <div class="stat-value danger">{st['negative_blocked']}<small>건</small></div>
      <div class="stat-sub">키워드 {st['hard_blocked']} · AI {st['ai_blocked']}</div></div>
    <div class="stat"><div class="stat-label">최종 발행</div>
      <div class="stat-value green">{st['final_published']}<small>건</small></div>
      <div class="stat-sub">웹진 게시 완료</div></div>
"""

    # ── 카테고리별 분포 (막대) ──
    by_cat = st.get("by_category", {})
    maxcat = max(by_cat.values()) if by_cat else 1
    cat_bars = "".join(_bar(c, n, maxcat) for c, n in by_cat.items()) or '<div style="color:#888;font-size:13px">데이터 없음</div>'

    # ── 날짜별 추이 (최근 7일) ──
    recent = stats[-7:] if len(stats) > 7 else stats
    if recent:
        maxtrend = max(max(s.get("collected", 0), s.get("final_published", 0)) for s in recent) or 1
        trend_cols = ""
        for s in recent:
            col = s.get("collected", 0)
            pub = s.get("final_published", 0)
            ch = int((col / maxtrend) * 110)
            ph = int((pub / maxtrend) * 110)
            d = s.get("date", "")[5:].replace("-", ".")
            trend_cols += f"""      <div class="trend-col">
        <div class="trend-bars">
          <div class="trend-barwrap">
            <div class="trend-num col">{col}</div>
            <div class="trend-bar" style="height:{ch}px" title="수집 {col}건"></div>
          </div>
          <div class="trend-barwrap">
            <div class="trend-num pub">{pub}</div>
            <div class="trend-bar pub" style="height:{ph}px" title="발행 {pub}건"></div>
          </div>
        </div>
        <div class="trend-date">{_esc(d)}</div>
      </div>
"""
    else:
        trend_cols = '<div style="color:#888;font-size:13px;padding:20px">추이 데이터가 아직 없습니다.</div>'

    # ── 카테고리별 뉴스 목록 ──
    news_sections = ""
    daily = today.get("daily", {})
    for cat_name, articles in daily.items():
        if not articles:
            continue
        items = ""
        for art in articles:
            title = _esc(art.get("title", ""))
            url = art.get("url", "").strip()
            summary = _esc(art.get("summary", ""))
            date = _esc(art.get("date", ""))
            title_html = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{title}</a>' if url else title
            items += f"""      <div class="news-item">
        <div class="news-title">{title_html}</div>
        <div class="news-summary">{summary}</div>
        <div class="news-meta">{('[' + date + ']') if date else ''}</div>
      </div>
"""
        news_sections += f"""    <div class="cat-section">
      <div class="cat-head"><div class="cat-name">{_esc(cat_name)}</div>
        <div class="cat-count">{len(articles)}건 게시</div></div>
{items}    </div>
"""

    # ── ④ 키워드 관리 ──
    KEYWORDS, MEMBERS = keywords_data
    kw_html = ""
    for cat, conf in KEYWORDS.items():
        prim = "".join(f'<span class="chip">{_esc(k)}</span>' for k in conf.get("primary", []))
        gen = "".join(f'<span class="chip gen">{_esc(k)}</span>' for k in conf.get("general", []))
        kw_html += f"""    <div class="kw-group">
      <div class="kw-cat">{_esc(cat)}</div>
      <div class="kw-label">주요 키워드 (검색어)</div>
      <div class="kw-chips">{prim or '<span style="color:#aaa">없음</span>'}</div>
      <div class="kw-label">일반 키워드 (2차 필터)</div>
      <div class="kw-chips">{gen or '<span style="color:#aaa">없음</span>'}</div>
    </div>
"""
    member_chips = "".join(f'<span class="chip">{_esc(m)}</span>' for m in MEMBERS)
    kw_html += f"""    <div class="kw-group">
      <div class="kw-cat">회원사 ({len(MEMBERS)}개사)</div>
      <div class="kw-chips">{member_chips}</div>
    </div>
"""

    # ── ② 차단 기사 목록 (부정 차단만: HARD + AI) ──
    blocked = today.get("blocked", [])
    if blocked:
        blocked_list = ""
        for b in blocked:
            title = _esc(b.get("title", ""))
            url = b.get("url", "").strip()
            summary = _esc(b.get("summary", ""))
            reason = _esc(b.get("reason", ""))
            detail = _esc(b.get("detail", ""))
            cat = _esc(b.get("category", ""))
            title_html = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{title}</a>' if url else title
            reason_class = "reason-kw" if b.get("reason") == "부정 키워드" else "reason-ai"
            blocked_list += f"""    <div class="blocked-item">
      <div class="blocked-head">
        <span class="reason-tag {reason_class}">{reason}</span>
        <span class="blocked-cat">{cat}</span>
      </div>
      <div class="blocked-title">{title_html}</div>
      <div class="blocked-summary">{summary}</div>
      <div class="blocked-detail">차단 사유: {detail}</div>
    </div>
"""
    else:
        blocked_list = '<div style="color:#888;font-size:13px;padding:16px;background:var(--card);border:1px solid var(--border);border-radius:10px;">차단된 기사가 없습니다.</div>'

    # ── ③ 발송 실패 추적 (이메일 주소별 누적 실패 횟수) ──
    by_address = failures_data.get("by_address", {})
    fail_updated = failures_data.get("updated", "")
    # 누적 실패 횟수 많은 순 정렬
    sorted_fails = sorted(by_address.items(), key=lambda x: -x[1].get("count", 0))
    total_fail_addr = len(sorted_fails)
    total_fail_count = sum(r.get("count", 0) for _, r in sorted_fails)

    if sorted_fails:
        fail_rows = ""
        for key, rec in sorted_fails:
            # 개인정보 보호: 원본 주소는 저장하지 않으며, 마스킹된 display만 표시한다.
            # (구버전 데이터 호환: display가 없으면 키를 그대로 쓰지 않고 가림 처리)
            shown = rec.get("display") or "***"
            cnt = rec.get("count", 0)
            last_type = _esc(rec.get("last_type", ""))
            last_reason = _esc(rec.get("last_reason", ""))
            last_date = _esc(rec.get("last_date", ""))
            type_class = "reason-ai" if "소프트" in rec.get("last_type", "") else "reason-kw"
            # 실패 이력 날짜들
            hist = rec.get("history", [])
            hist_dates = ", ".join(_esc(h.get("date", "")) for h in hist)
            fail_rows += f"""    <div class="fail-item">
      <div class="fail-main">
        <span class="fail-addr">{_esc(shown)}</span>
        <span class="fail-count">{cnt}회 실패</span>
      </div>
      <div class="fail-meta">
        <span class="reason-tag {type_class}">{last_type}</span>
        <span class="fail-detail">최근: {last_date} · {last_reason}</span>
      </div>
      <div class="fail-history">실패 이력: {hist_dates}</div>
    </div>
"""
        fail_section = f"""  <div class="info-box">
    <strong>발송 실패 추적</strong> · 이메일 주소별 누적 발송 실패 횟수입니다. 반복 실패하는 주소는 주소록에서 정리를 검토하세요. 개인정보 보호를 위해 주소는 일부만 표시되며, 정확한 주소는 스티비 [상세 통계] → [발송 실패]에서 확인할 수 있습니다. 소프트바운스는 재시도로 이후 성공할 수 있어 조회 시점 기준입니다.{(' · 갱신: ' + _esc(fail_updated)) if fail_updated else ''}
  </div>
  <div class="stats">
    <div class="stat"><div class="stat-label">실패 발생 주소</div>
      <div class="stat-value danger">{total_fail_addr}<small>개</small></div>
      <div class="stat-sub">누적 기준</div></div>
    <div class="stat"><div class="stat-label">총 실패 횟수</div>
      <div class="stat-value danger">{total_fail_count}<small>회</small></div>
      <div class="stat-sub">전체 발송 누적</div></div>
  </div>
  <div class="section-title">주소별 실패 현황 (실패 많은 순)</div>
{fail_rows}"""
    else:
        fail_section = """  <div class="empty-tab">
    <div class="big">📮</div>
    <div class="msg">발송 실패 데이터 없음</div>
    <div class="sub">아직 집계된 발송 실패가 없습니다. 스티비로 발송 후 추적이 실행되면 여기에 이메일 주소별 누적 실패가 표시됩니다.</div>
  </div>"""

    # ── 조립 ──
    tmpl = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>협회 운영 대시보드 · 한국자동차환경협회</title>
<style>@@CSS@@</style>
</head>
<body>

<header>
  <div class="logo">한국자동차환경협회 <small>뉴스레터 자동화 시스템</small><span class="lock">🔒 협회 운영 전용</span></div>
  <div class="header-meta"><span class="status-dot"></span>정상 작동 중 · @@DATE@@ (@@DOW@@)</div>
</header>

<nav class="tabs">
  <div class="tab active" onclick="go('monitoring')">오늘의 뉴스 모니터링</div>
  <div class="tab" onclick="go('blocked')">AI 차단 검토<span class="badge">@@BLOCKED@@</span></div>
  <div class="tab" onclick="go('failures')">발송 실패 추적@@FAIL_BADGE@@</div>
  <div class="tab" onclick="go('keywords')">키워드 관리</div>
</nav>

<main>

<section class="page active" id="monitoring">
  <div class="info-box">
    <strong>운영 방식 안내</strong> · 본 화면은 협회 담당자용 운영 모니터링입니다. 자동 수집·정리된 뉴스를 참고해 스티비에서 뉴스레터를 편집·발송하며, 동시에 회원사 웹진에도 자동 게시됩니다.
  </div>
  <div class="stats">@@STAT_CARDS@@</div>

  <div class="chart-box">
    <div class="chart-title">카테고리별 발행 분포 (오늘)</div>
@@CAT_BARS@@  </div>

  <div class="chart-box">
    <div class="chart-title">날짜별 추이 (최근 7일)</div>
    <div class="trend">
@@TREND@@    </div>
    <div class="trend-legend">
      <span><span class="legend-dot" style="background:var(--primary)"></span>수집</span>
      <span><span class="legend-dot" style="background:var(--green)"></span>발행</span>
    </div>
  </div>

  <div class="section-title">카테고리별 게시 뉴스</div>
@@NEWS@@
</section>

<section class="page" id="blocked">
  <div class="info-box">
    <strong>AI 차단 검토</strong> · 부정 뉴스로 차단된 기사 목록입니다. 혹시 잘못 걸러진 뉴스가 없는지 검토하세요. (중복 제거 기사는 목록에서 제외하고 건수만 표시합니다.)
  </div>
  <div class="stats">
    <div class="stat"><div class="stat-label">중복 제거</div>
      <div class="stat-value primary">@@DUP@@<small>건</small></div>
      <div class="stat-sub">같은 사건 기사 통합</div></div>
    <div class="stat"><div class="stat-label">부정 차단(키워드)</div>
      <div class="stat-value danger">@@HARD@@<small>건</small></div>
      <div class="stat-sub">리콜·담합 등 즉시 제외</div></div>
    <div class="stat"><div class="stat-label">부정 차단(AI)</div>
      <div class="stat-value danger">@@AI@@<small>건</small></div>
      <div class="stat-sub">Claude 톤 판정</div></div>
  </div>

  <div class="section-title">차단된 기사 (@@BLOCKED_COUNT@@건)</div>
@@BLOCKED_LIST@@
</section>

<section class="page" id="failures">
@@FAIL_SECTION@@
</section>

<section class="page" id="keywords">
  <div class="info-box">
    <strong>키워드 관리</strong> · 현재 뉴스 수집에 사용되는 키워드입니다. 변경은 keywords.py 파일에서 이루어집니다.
  </div>
@@KEYWORDS@@
</section>

</main>

<footer class="footer">한국자동차환경협회 (KAEA) · 뉴스레터 자동화 시스템 운영 대시보드</footer>

<script>
  function go(id) {
    document.querySelectorAll('.page').forEach(function(p){ p.classList.remove('active'); });
    document.querySelectorAll('.tab').forEach(function(t){ t.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
    var ids = ['monitoring', 'blocked', 'failures', 'keywords'];
    var tabs = document.querySelectorAll('.tab');
    var idx = ids.indexOf(id);
    if (idx >= 0) tabs[idx].classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
</script>

</body>
</html>
"""
    return (tmpl
            .replace("@@CSS@@", DASH_CSS)
            .replace("@@DATE@@", _esc(date_str))
            .replace("@@DOW@@", _esc(dow))
            .replace("@@BLOCKED@@", str(st["negative_blocked"]))
            .replace("@@STAT_CARDS@@", stat_cards)
            .replace("@@CAT_BARS@@", cat_bars)
            .replace("@@TREND@@", trend_cols)
            .replace("@@NEWS@@", news_sections or '<div style="color:#888">게시된 뉴스가 없습니다.</div>')
            .replace("@@DUP@@", str(st["duplicates_removed"]))
            .replace("@@HARD@@", str(st["hard_blocked"]))
            .replace("@@AI@@", str(st["ai_blocked"]))
            .replace("@@BLOCKED_COUNT@@", str(len(blocked)))
            .replace("@@BLOCKED_LIST@@", blocked_list)
            .replace("@@FAIL_SECTION@@", fail_section)
            .replace("@@FAIL_BADGE@@", (f'<span class="badge alert">{total_fail_addr}</span>' if total_fail_addr else ""))
            .replace("@@KEYWORDS@@", kw_html))
