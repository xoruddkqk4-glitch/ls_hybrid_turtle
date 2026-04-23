# timer_agent.py
# 진입 신호 통합 모듈
#
# 역할:
#   두 가지 진입 경로를 통합해서 진입 신호 목록을 반환한다.
#
#   경로 A — 동적 목표가 + 30분 가드 (TARGET_30MIN):
#     target_manager.py가 기록한 "above_target_since"가 30분 이상 경과한 종목
#   경로 B — 오리지널 터틀 시스템1, 시스템2 신고가 돌파 (TURTLE_S1 / TURTLE_S2):
#     target_manager.py가 기록한 turtle_s1_signal 또는 turtle_s2_signal이 True인 종목
#     ※ 일봉 전략이므로 30분 가드 없이 즉시 신호 발생
#
# 반환 형식:
#   [{"code": "005930", "entry_source": "TARGET_30MIN"}, ...]
#   entry_source: "TARGET_30MIN" / "TURTLE_S1" / "TURTLE_S2"
#
# 같은 종목이 여러 경로에 해당하면 우선순위 높은 것 하나만 포함:
#   TURTLE_S2 > TURTLE_S1 > TARGET_30MIN
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

# 가드 시간: 30분 (이 시간 이상 목표가 위에 있어야 진입 신호)
GUARD_MINUTES = 30


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


# ─────────────────────────────────────────
# 30분 경과 확인
# ─────────────────────────────────────────

def check_30min_passed(code: str, unheld_record: dict) -> bool:
    """해당 종목이 목표가 이상에서 30분 이상 머물렀는지 확인한다.

    판단 기준:
    - above_target_since가 없거나 null  → False (타이머가 시작되지 않음)
    - above_target_since로부터 30분 이상 경과 → True  (진입 신호 발생)
    - 아직 30분이 안 됨                  → False (계속 대기)

    Args:
        code:          종목코드 6자리
        unheld_record: load_unheld_record()가 반환한 딕셔너리

    Returns:
        True:  30분 이상 유지 → 진입 신호 발생
        False: 조건 미충족 (대기 중 또는 타이머 미시작)
    """
    stock_data = unheld_record.get(code)
    if not stock_data:
        # 해당 종목의 상태 정보 자체가 없는 경우
        return False

    above_since_str = stock_data.get("above_target_since")
    if not above_since_str:
        # 아직 목표가를 한 번도 넘지 않았거나, 이탈 후 초기화된 상태
        return False

    try:
        # 문자열 → datetime 변환 (KST 기준)
        above_since = KST.localize(
            datetime.strptime(above_since_str, "%Y-%m-%d %H:%M:%S")
        )
        now_kst = datetime.now(KST)

        # 벽시계 시간이 아닌 실제 장중(09:00~15:30, 평일) 경과 분만 계산
        market_elapsed_min = _market_minutes_elapsed(above_since, now_kst)

        if market_elapsed_min >= GUARD_MINUTES:
            # 장중 30분 이상 유지 → 진입 신호 발생
            name = get_watchlist().get(code, {}).get("name", code)
            print(f"[timer_agent] {name}({code}) ✅ 30분 가드 통과! "
                  f"(장중 {int(market_elapsed_min)}분 동안 목표가 위 유지)")
            return True
        else:
            # 아직 장중 30분 미달 → 계속 대기
            remaining_min = int(GUARD_MINUTES - market_elapsed_min) + 1
            name          = get_watchlist().get(code, {}).get("name", code)
            print(f"[timer_agent] {name}({code}) ⏳ 대기 중 "
                  f"(장중으로 앞으로 약 {remaining_min}분 더 유지해야 함)")
            return False

    except (ValueError, TypeError) as e:
        # 시각 문자열 파싱 오류 (잘못된 형식 등)
        print(f"[timer_agent] {code} 타이머 시각 파싱 오류: {e}")
        return False


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_timer_check() -> list:
    """진입 신호 종목 목록을 반환한다 (두 가지 경로 통합).

    경로 A — TARGET_30MIN: 동적 목표가 위에서 30분 유지한 종목
    경로 B — TURTLE_S2:    55일 신고가 돌파 종목 (30분 가드 없음)
    경로 C — TURTLE_S1:    20일 신고가 돌파 종목 (30분 가드 없음)

    같은 종목이 여러 경로에 해당하면 TURTLE_S2 > TURTLE_S1 > TARGET_30MIN 우선순위로
    하나만 포함한다.

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
    # 우선순위: TURTLE_S2(0) > TURTLE_S1(1) > TARGET_30MIN(2)
    signal_priority: dict = {}  # code → (우선순위 숫자, entry_source 문자열)

    for code, data in unheld_record.items():
        # 감시 종목에 없는 종목은 처리하지 않음 (안전장치)
        if code not in watchlist:
            print(f"[timer_agent] {code} 감시 종목 외 → 스킵")
            continue

        current_priority = signal_priority.get(code, (99, None))[0]

        # 경로 A: 30분 가드 통과 여부 (우선순위 2)
        if check_30min_passed(code, unheld_record):
            if 2 < current_priority:
                signal_priority[code] = (2, "TARGET_30MIN")
                current_priority = 2

        # 경로 C: 터틀 시스템1 — 20일 신고가 돌파 (우선순위 1)
        if data.get("turtle_s1_signal", False):
            if 1 < current_priority:
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) ✅ 터틀 S1 신호 (20일 신고가 돌파)")
                signal_priority[code] = (1, "TURTLE_S1")
                current_priority = 1

        # 경로 B: 터틀 시스템2 — 55일 신고가 돌파 (우선순위 0 — 최우선)
        if data.get("turtle_s2_signal", False):
            if 0 < current_priority:
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) ✅ 터틀 S2 신호 (55일 신고가 돌파)")
                signal_priority[code] = (0, "TURTLE_S2")

    # entry_signals 구성
    entry_signals = [
        {"code": code, "entry_source": src}
        for code, (_, src) in signal_priority.items()
        if src is not None
    ]

    # 정렬: TARGET_30MIN은 above_target_since 오름차순 (오래된 신호 먼저)
    #       TURTLE 신호는 그 뒤 (S2 → S1 순)
    def _sort_key(s: dict):
        src  = s["entry_source"]
        code = s["code"]
        if src == "TARGET_30MIN":
            # 오름차순 정렬 — 문자열 비교가 곧 시간 비교
            return (2, unheld_record[code].get("above_target_since") or "")
        elif src == "TURTLE_S2":
            return (0, "")
        else:  # TURTLE_S1
            return (1, "")

    entry_signals.sort(key=_sort_key)

    # 결과 요약 출력
    if entry_signals:
        for s in entry_signals:
            code = s["code"]
            src  = s["entry_source"]
            name = watchlist.get(code, {}).get("name", code)
            if src == "TARGET_30MIN":
                since = unheld_record[code].get("above_target_since", "?")
                print(f"[timer_agent]   → {name}({code}) [{src}] 안착 시각: {since}")
            else:
                print(f"[timer_agent]   → {name}({code}) [{src}]")
        names = [watchlist.get(s["code"], {}).get("name", s["code"]) for s in entry_signals]
        print(f"[timer_agent] 진입 신호 종목: {', '.join(names)}")
    else:
        print("[timer_agent] 진입 신호 없음")

    return entry_signals
