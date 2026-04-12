# timer_agent.py
# 30분 가드 타이머 모듈
#
# 역할:
#   target_manager.py가 기록한 "목표가 돌파 시각(above_target_since)"을 읽어서,
#   30분 이상 지났는지 확인한다.
#   30분이 지난 종목만 "진입 신호" 목록에 넣어 반환한다.
#
# 왜 30분인가?
#   순간적으로 가격이 튀는 "가짜 돌파"를 걸러내기 위해서다.
#   30분 동안 목표가 이상을 유지해야 진짜 추세 전환으로 판단한다.
#
# 사용법:
#   import timer_agent
#   entry_signals = timer_agent.run_timer_check()
#   # entry_signals 예: ["005930", "064350"]  ← 이 종목들에 진입 신호 발생

from datetime import datetime, timedelta

import pytz

from config import lovely_stock_list
from target_manager import load_unheld_record

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# 가드 시간: 30분 (이 시간 이상 목표가 위에 있어야 진입 신호)
GUARD_MINUTES = 30


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
        elapsed = now_kst - above_since  # 목표가 위에 있었던 시간

        if elapsed >= timedelta(minutes=GUARD_MINUTES):
            # 30분 이상 유지 → 진입 신호 발생
            elapsed_min  = int(elapsed.total_seconds() / 60)
            name         = lovely_stock_list.get(code, {}).get("name", code)
            print(f"[timer_agent] {name}({code}) ✅ 30분 가드 통과! "
                  f"({elapsed_min}분 동안 목표가 위 유지)")
            return True
        else:
            # 아직 30분이 안 됨 → 계속 대기
            remaining     = timedelta(minutes=GUARD_MINUTES) - elapsed
            remaining_min = int(remaining.total_seconds() / 60) + 1
            name          = lovely_stock_list.get(code, {}).get("name", code)
            print(f"[timer_agent] {name}({code}) ⏳ 대기 중 "
                  f"(앞으로 약 {remaining_min}분 더 유지해야 함)")
            return False

    except (ValueError, TypeError) as e:
        # 시각 문자열 파싱 오류 (잘못된 형식 등)
        print(f"[timer_agent] {code} 타이머 시각 파싱 오류: {e}")
        return False


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_timer_check() -> list:
    """전체 미보유 종목의 30분 가드를 체크하고, 통과한 종목 코드 목록을 반환한다.

    이 함수가 반환한 목록이 turtle_order_logic.run_orders()의 입력값이 된다.
    즉, "지금 바로 매수를 검토해야 할 종목들"의 목록이다.

    실행 순서:
    1. unheld_stock_record.json 읽기
    2. 각 종목마다 check_30min_passed() 호출
    3. 통과한 종목만 entry_signals 리스트에 추가
    4. 결과 반환

    Returns:
        진입 신호 종목 코드 리스트 (예: ["005930", "064350"])
        진입 신호 없으면 빈 리스트 []
    """
    print("[timer_agent] 30분 가드 체크 시작")

    # 현재 미보유 종목 상태 읽기
    unheld_record = load_unheld_record()

    if not unheld_record:
        print("[timer_agent] 미보유 종목 상태 파일 비어있음 "
              "(target_manager.run_update()를 먼저 실행하세요)")
        return []

    # 각 종목 30분 가드 체크
    entry_signals = []
    for code in unheld_record:
        # lovely_stock_list에 없는 종목은 처리하지 않음 (안전장치)
        if code not in lovely_stock_list:
            print(f"[timer_agent] {code} lovely_stock_list 외 종목 → 스킵")
            continue

        if check_30min_passed(code, unheld_record):
            entry_signals.append(code)

    # 결과 요약 출력
    if entry_signals:
        names = [lovely_stock_list.get(c, {}).get("name", c) for c in entry_signals]
        print(f"[timer_agent] 진입 신호 발생 종목: {', '.join(names)}")
    else:
        print("[timer_agent] 진입 신호 없음")

    return entry_signals
