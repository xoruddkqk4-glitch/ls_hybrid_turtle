# timer_agent.py
# 진입 신호 통합 모듈
#
# 역할:
#   터틀 트레이딩 신고가 돌파 신호와 풀백(눌림) 재돌파 조건을 AND로 결합해
#   진입 신호 목록을 반환한다.
#
#   진입 조건 (두 가지 모두 충족):
#     1. 풀백 재돌파: target_manager가 계산한 entry_ready = True
#        (돌파 → 최고값 형성 → 눌림 → 최고값 재돌파 4단계 완료)
#     2. 시간 제한: 현재 시각이 오전 10시 이상 (10시 이전 진입 불가)
#
#   ※ S1·S2 동시 해당이면 TURTLE_S2 우선 (55일 > 20일)
#
# 반환 형식:
#   [{"code": "005930", "entry_source": "TURTLE_S1"}, ...]
#   entry_source: "TURTLE_S1" / "TURTLE_S2"
#
# 사용법:
#   import timer_agent
#   entry_signals = timer_agent.run_timer_check()

from datetime import datetime

import pytz

from config import get_watchlist
from target_manager import load_unheld_record

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# 최소 진입 가능 시각: 오전 10시 (이전에는 가격 변동이 커서 진입하지 않음)
MIN_ENTRY_HOUR = 10


def _now_kst() -> datetime:
    """현재 KST 시각을 반환한다 (테스트에서 이 함수만 패치하면 됨)."""
    return datetime.now(KST)


# ─────────────────────────────────────────
# 풀백 재돌파 진입 조건 확인 헬퍼
# ─────────────────────────────────────────

def _check_pullback_retest(code: str, entry_ready_key: str, unheld_record: dict) -> bool:
    """풀백 재돌파 진입 조건을 확인한다.

    두 조건을 모두 충족해야 True를 반환한다:
      1. entry_ready = True (target_manager가 풀백 재돌파 가격 조건 확인 완료)
      2. 현재 시각이 오전 10시 이상 (10시 이전에는 진입 불가)

    Args:
        code:           종목코드 6자리
        entry_ready_key: 확인할 필드명 ("turtle_s1_entry_ready" 또는 "turtle_s2_entry_ready")
        unheld_record:  load_unheld_record()가 반환한 딕셔너리

    Returns:
        True:  두 조건 모두 충족 → 진입 신호 발생
        False: 조건 미충족 (10시 이전이거나 entry_ready 미충족)
    """
    stock_data = unheld_record.get(code)
    if not stock_data:
        return False

    # 조건 1: entry_ready 확인 (가격 재돌파 조건)
    if not stock_data.get(entry_ready_key, False):
        name = get_watchlist().get(code, {}).get("name", code)
        print(f"[timer_agent] {name}({code}) 풀백 재돌파 대기 중")
        return False

    # 조건 2: 오전 10시 이상이어야 진입 가능
    now_kst = _now_kst()
    if now_kst.hour < MIN_ENTRY_HOUR:
        name = get_watchlist().get(code, {}).get("name", code)
        print(f"[timer_agent] {name}({code}) 10시 이전 진입 불가 "
              f"(현재 {now_kst.strftime('%H:%M')})")
        return False

    name = get_watchlist().get(code, {}).get("name", code)
    print(f"[timer_agent] {name}({code}) [OK] 풀백 재돌파 진입 조건 통과!")
    return True


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_timer_check() -> list:
    """진입 신호 종목 목록을 반환한다.

    풀백 재돌파(entry_ready=True) AND 오전 10시 이후, 두 조건을 모두 만족한 종목만 반환.

    경로 B — TURTLE_S2: 55일 신고가 + 풀백 재돌파 (우선순위 최고)
    경로 C — TURTLE_S1: 20일 신고가 + 풀백 재돌파

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

        # 경로 C: 터틀 시스템1 — 20일 신고가 + 풀백 재돌파 (우선순위 1)
        if data.get("turtle_s1_signal", False) and 1 < current_priority:
            if _check_pullback_retest(code, "turtle_s1_entry_ready", unheld_record):
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) [OK] 터틀 S1 진입 신호 "
                      f"(20일 신고가 + 풀백 재돌파)")
                signal_priority[code] = (1, "TURTLE_S1")
                current_priority = 1

        # 경로 B: 터틀 시스템2 — 55일 신고가 + 풀백 재돌파 (우선순위 0 — 최우선)
        if data.get("turtle_s2_signal", False) and 0 < current_priority:
            if _check_pullback_retest(code, "turtle_s2_entry_ready", unheld_record):
                name = watchlist.get(code, {}).get("name", code)
                print(f"[timer_agent] {name}({code}) [OK] 터틀 S2 진입 신호 "
                      f"(55일 신고가 + 풀백 재돌파)")
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
            code = s["code"]
            src  = s["entry_source"]
            name = watchlist.get(code, {}).get("name", code)
            peak_key = "turtle_s2_peak_price" if src == "TURTLE_S2" else "turtle_s1_peak_price"
            peak = unheld_record[code].get(peak_key, "?")
            peak_str = f"{peak:,}원" if isinstance(peak, (int, float)) else str(peak)
            print(f"[timer_agent]   → {name}({code}) [{src}] 재돌파 기준가: {peak_str}")
        names = [watchlist.get(s["code"], {}).get("name", s["code"]) for s in entry_signals]
        print(f"[timer_agent] 진입 신호 종목: {', '.join(names)}")
    else:
        print("[timer_agent] 진입 신호 없음")

    return entry_signals
