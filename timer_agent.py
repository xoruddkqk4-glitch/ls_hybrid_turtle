# timer_agent.py
# 진입 신호 통합 모듈
#
# 역할:
#   터틀 트레이딩 신고가 돌파 신호와 30분 가드를 AND 조건으로 결합해
#   진입 신호 목록을 반환한다.
#
#   진입 조건 (세 가지 모두 충족):
#     1. 터틀 신호: turtle_s1_signal(20일 신고가) 또는 turtle_s2_signal(55일 신고가)가 True
#     2. 30분 가드: 돌파 발생 시각(turtle_s1/s2_breakout_since)으로부터 장중 30분 이상 경과
#     3. 시간 제한: 현재 시각이 오전 10시 이상 (10시 이전 진입 불가)
#
#   ※ 돌파가 9시 20분에 발생했어도 시각은 그때부터 기록됨.
#      10시가 됐을 때 이미 30분이 지났으면 즉시 진입, 안 지났으면 30분 채운 뒤 진입.
#
# 반환 형식:
#   [{"code": "005930", "entry_source": "TURTLE_S1"}, ...]
#   entry_source: "TURTLE_S1" / "TURTLE_S2"
#
# 같은 종목이 S1·S2 동시 해당이면 TURTLE_S2 > TURTLE_S1 우선순위로 하나만 포함.
#
# 사용법:
#   import timer_agent
#   entry_signals = timer_agent.run_timer_check()
#   # entry_signals 예: [{"code": "005930", "entry_source": "TURTLE_S1"}, ...]

from datetime import datetime, timedelta

import pytz

from config import get_watchlist
from target_manager import load_unheld_record

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# 가드 시간: 30분 (돌파 이후 이 시간 이상 유지해야 진입 신호)
GUARD_MINUTES = 30

# 최소 진입 가능 시각: 오전 10시 (이전에는 가격 변동이 커서 진입하지 않음)
MIN_ENTRY_HOUR = 10


# ─────────────────────────────────────────
# 장중 경과 분 계산 헬퍼
# ─────────────────────────────────────────

def _market_minutes_elapsed(start_kst: datetime, end_kst: datetime) -> float:
    """start_kst 부터 end_kst 까지 실제 장중(09:00~15:30) 경과 분만 계산한다.

    주말과 장 외 시간(09:00 이전, 15:30 이후)은 카운트하지 않는다.

    예시:
      14:58에 타이머 시작 → 당일 15:30까지 32분만 카운트,
      그날 밤~다음날 09:00까지는 0분, 09:00부터 다시 카운트.
    """
    if end_kst <= start_kst:
        return 0.0

    total_seconds = 0.0
    current_date = start_kst.date()  # KST 날짜 기준 시작일
    end_date     = end_kst.date()

    while current_date <= end_date:
        if current_date.weekday() < 5:  # 월(0)~금(4)만 장이 열림, 토(5)·일(6) 건너뜀
            # 이 날의 장 시작·종료 시각 (KST)
            day_open = KST.localize(
                datetime(current_date.year, current_date.month, current_date.day, 9, 0)
            )
            day_close = KST.localize(
                datetime(current_date.year, current_date.month, current_date.day, 15, 30)
            )

            # [start_kst, end_kst] 구간과 [day_open, day_close] 구간이 겹치는 부분만 합산
            seg_start = max(start_kst, day_open)
            seg_end   = min(end_kst,   day_close)

            if seg_end > seg_start:
                total_seconds += (seg_end - seg_start).total_seconds()

        current_date += timedelta(days=1)

    return total_seconds / 60.0  # 초 → 분 변환


def _now_kst() -> datetime:
    """현재 KST 시각을 반환한다 (테스트에서 이 함수만 패치하면 됨)."""
    return datetime.now(KST)


# ─────────────────────────────────────────
# 터틀 30분 가드 확인 헬퍼
# ─────────────────────────────────────────

def _check_turtle_30min(code: str, breakout_since_key: str, unheld_record: dict) -> bool:
    """터틀 돌파 시각으로부터 30분 경과 + 오전 10시 이후인지 확인한다.

    두 조건을 모두 충족해야 True를 반환한다:
      1. 현재 시각이 오전 10시 이상 (10시 이전에는 진입 불가)
      2. 돌파 발생 시각으로부터 장중 30분 이상 경과

    9시 20분에 돌파했고 10시에 확인하면 이미 40분 경과 → 10시에 즉시 True.
    9시 40분에 돌파했고 10시에 확인하면 20분 경과 → 10시 10분에 True.

    Args:
        code:               종목코드 6자리
        breakout_since_key: 확인할 필드명 ("turtle_s1_breakout_since" 또는 "turtle_s2_breakout_since")
        unheld_record:      load_unheld_record()가 반환한 딕셔너리

    Returns:
        True:  두 조건 모두 충족 → 진입 신호 발생
        False: 조건 미충족 (10시 이전이거나 30분 미달)
    """
    stock_data = unheld_record.get(code)
    if not stock_data:
        return False

    since_str = stock_data.get(breakout_since_key)
    if not since_str:
        # 돌파 시각이 기록되지 않은 상태 (신호 없거나 아직 미돌파)
        return False

    try:
        since   = KST.localize(datetime.strptime(since_str, "%Y-%m-%d %H:%M:%S"))
        now_kst = _now_kst()
        name    = get_watchlist().get(code, {}).get("name", code)

        # 조건 1: 오전 10시 이상이어야 진입 가능
        if now_kst.hour < MIN_ENTRY_HOUR:
            print(f"[timer_agent] {name}({code}) ⏳ 10시 이전 진입 불가 "
                  f"(현재 {now_kst.strftime('%H:%M')})")
            return False

        # 조건 2: 돌파 시각으로부터 장중 30분 이상 경과
        elapsed = _market_minutes_elapsed(since, now_kst)
        if elapsed >= GUARD_MINUTES:
            print(f"[timer_agent] {name}({code}) ✅ 터틀 30분 가드 통과! "
                  f"(장중 {int(elapsed)}분 경과, 돌파 시각: {since_str})")
            return True
        else:
            remaining = int(GUARD_MINUTES - elapsed) + 1
            print(f"[timer_agent] {name}({code}) ⏳ 대기 중 "
                  f"(앞으로 약 {remaining}분 더 필요, 돌파 시각: {since_str})")
            return False

    except (ValueError, TypeError) as e:
        # 시각 문자열 파싱 오류 (잘못된 형식 등)
        print(f"[timer_agent] {code} 타이머 시각 파싱 오류: {e}")
        return False


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_timer_check() -> list:
    """진입 신호 종목 목록을 반환한다.

    터틀 신고가 돌파 AND 30분 가드 AND 오전 10시 이후, 세 조건을 모두 만족한 종목만 반환.

    경로 B — TURTLE_S2: 55일 신고가 돌파 + 30분 가드 통과 (우선순위 최고)
    경로 C — TURTLE_S1: 20일 신고가 돌파 + 30분 가드 통과

    같은 종목이 S1·S2 동시 해당이면 TURTLE_S2 하나만 포함한다.

    Returns:
        [{"code": "005930", "entry_source": "TURTLE_S1"}, ...] 형식의 리스트
        진입 신호 없으면 빈 리스트 []
    """
    print("[timer_agent] 진입 신호 체크 시작")

    # 현재 미보유 종목 상태 읽기
    unheld_record = load_unheld_record()

    if not unheld_record:
        print("[timer_agent] 미보유 종목 상태 파일 비어있음 "
              "(target_manager.run_update()를 먼저 실행하세요)")
        return []

    watchlist = get_watchlist()

    # 종목별로 해당되는 신호 유형 수집 (우선순위 적용)
    # 우선순위: TURTLE_S2(0) > TURTLE_S1(1)
    signal_priority: dict = {}  # code → (우선순위 숫자, entry_source 문자열)

    for code, data in unheld_record.items():
        # 감시 종목에 없는 종목은 처리하지 않음 (안전장치)
        if code not in watchlist:
            print(f"[timer_agent] {code} 감시 종목 외 → 스킵")
            continue

        current_priority = signal_priority.get(code, (99, None))[0]

        # 경로 C: 터틀 시스템1 — 20일 신고가 + 30분 가드 (우선순위 1)
        if data.get("turtle_s1_signal", False) and 1 < current_priority:
            if _check_turtle_30min(code, "turtle_s1_breakout_since", unheld_record):
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) ✅ 터틀 S1 진입 신호 "
                      f"(20일 신고가 + 30분 가드)")
                signal_priority[code] = (1, "TURTLE_S1")
                current_priority = 1

        # 경로 B: 터틀 시스템2 — 55일 신고가 + 30분 가드 (우선순위 0 — 최우선)
        if data.get("turtle_s2_signal", False) and 0 < current_priority:
            if _check_turtle_30min(code, "turtle_s2_breakout_since", unheld_record):
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) ✅ 터틀 S2 진입 신호 "
                      f"(55일 신고가 + 30분 가드)")
                signal_priority[code] = (0, "TURTLE_S2")

    # entry_signals 구성
    entry_signals = [
        {"code": code, "entry_source": src}
        for code, (_, src) in signal_priority.items()
        if src is not None
    ]

    # 정렬: TURTLE_S2(0) → TURTLE_S1(1) 순
    def _sort_key(s: dict):
        return (0, "") if s["entry_source"] == "TURTLE_S2" else (1, "")

    entry_signals.sort(key=_sort_key)

    # 결과 요약 출력
    if entry_signals:
        for s in entry_signals:
            code  = s["code"]
            src   = s["entry_source"]
            name  = watchlist.get(code, {}).get("name", code)
            key   = "turtle_s2_breakout_since" if src == "TURTLE_S2" else "turtle_s1_breakout_since"
            since = unheld_record[code].get(key, "?")
            print(f"[timer_agent]   → {name}({code}) [{src}] 돌파 시각: {since}")
        names = [watchlist.get(s["code"], {}).get("name", s["code"]) for s in entry_signals]
        print(f"[timer_agent] 진입 신호 종목: {', '.join(names)}")
    else:
        print("[timer_agent] 진입 신호 없음")

    return entry_signals
