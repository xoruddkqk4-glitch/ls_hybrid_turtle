# target_manager.py
# 미보유 종목 터틀 신호 갱신 모듈
#
# 역할:
#   - 오리지널 터틀 트레이딩 신호를 체크한다 (20일 / 55일 신고가 돌파)
#   - 돌파 후 최고값(peak_price)을 추적해 풀백(눌림) → 재돌파 진입 조건을 관리한다
#
# unheld_stock_record.json 구조:
# {
#   "005930": {
#     "turtle_s1_signal":      false,  ← 시스템1(20일 신고가) 돌파 여부
#     "turtle_s2_signal":      false,  ← 시스템2(55일 신고가) 돌파 여부
#     "turtle_s1_peak_price":  null,   ← S1 돌파 후 장중 최고값 (null=미돌파)
#     "turtle_s1_peak_locked": false,  ← S1 최고값 잠금 여부 (true=눌림 시작)
#     "turtle_s1_entry_ready": false,  ← S1 풀백 재돌파 진입 조건 충족 여부
#     "turtle_s2_peak_price":  null,   ← S2 동일
#     "turtle_s2_peak_locked": false,  ← S2 동일
#     "turtle_s2_entry_ready": false   ← S2 동일
#   }
# }
#
# 사용법:
#   import target_manager
#   target_manager.initialize_unheld_record(watchlist)  ← 09:05 종목 확정 직후 호출
#   target_manager.run_update()                         ← 미보유 종목 터틀 신호 갱신

import json
import os
import time
from datetime import datetime
from typing import Optional, Set

import pytz

import daily_chart_cache
import indicator_calc
import ls_client
from config import get_watchlist

# 상태 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR = os.path.dirname(os.path.abspath(__file__))
UNHELD_RECORD_FILE = os.path.join(_DIR, "unheld_stock_record.json")

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")


# ─────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────

def load_unheld_record() -> dict:
    """unheld_stock_record.json을 읽어서 반환한다.

    파일이 없거나 손상된 경우 빈 딕셔너리를 반환한다.

    Returns:
        종목코드 → {turtle_s1_signal, turtle_s2_signal, peak_price, peak_locked, entry_ready ...} 딕셔너리
    """
    if os.path.exists(UNHELD_RECORD_FILE):
        try:
            with open(UNHELD_RECORD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, IOError):
            print(f"[target_manager] {UNHELD_RECORD_FILE} 읽기 오류 → 새 파일로 시작")
    return {}


def save_unheld_record(record: dict):
    """미보유 종목 상태를 unheld_stock_record.json에 저장한다.

    Args:
        record: 종목코드 → 상태 딕셔너리
    """
    try:
        with open(UNHELD_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[target_manager] 파일 저장 오류: {e}")



# ─────────────────────────────────────────
# 09:05 종목 확정 직후 초기화
# ─────────────────────────────────────────

def initialize_unheld_record(watchlist: dict):
    """09:05 종목 확정 직후 호출 — 신규 종목의 터틀 신호 필드를 초기화한다.

    이미 unheld_stock_record.json에 있는 종목은 덮어쓰지 않는다 (기존 돌파 시각 보존).
    감시 종목에서 빠진 종목은 삭제한다.

    Args:
        watchlist: stock_screener.py가 확정한 감시 종목 딕셔너리
                   {"종목코드": {"name": ..., "score": ..., "atr": ...}}
    """
    print("[target_manager] 신규 종목 터틀 신호 필드 초기화 시작")

    # 기존 상태 파일 불러오기
    unheld_record = load_unheld_record()

    # 신규 종목 목록 (watchlist에 있지만 unheld_record에 없는 종목)
    new_codes = [code for code in watchlist if code not in unheld_record]

    if new_codes:
        for code in new_codes:
            name = watchlist.get(code, {}).get("name", code)
            unheld_record[code] = {
                "turtle_s1_signal":          False,
                "turtle_s1_breakout_at":     None,  # 최초 돌파 시각
                "turtle_s1_breakout_price":  None,  # 최초 돌파 시점의 돌파선 가격
                "turtle_s1_limit_price":     None,  # 마지노선 지지선 가격 (breakout_price - 0.5 * ATR)
                "turtle_s1_target_price":    None,  # 1차 진입 목표가
                "turtle_s1_peak_price":      None,  # 감시 중 기록한 최고가
                "turtle_s1_entry_ready":     False, # 시간가드 만료 후 최종 진입 조건 충족 여부
                "turtle_s2_signal":          False,
                "turtle_s2_breakout_at":     None,
                "turtle_s2_breakout_price":  None,
                "turtle_s2_limit_price":     None,
                "turtle_s2_target_price":    None,
                "turtle_s2_peak_price":      None,
                "turtle_s2_entry_ready":     False,
            }
            print(f"[target_manager] {name}({code}) 초기화")

    # 감시 종목에서 제외된 종목 삭제
    removed = [code for code in list(unheld_record.keys()) if code not in watchlist]
    for code in removed:
        print(f"[target_manager] {code} 감시 종목 제외 → unheld_record 삭제")
        del unheld_record[code]

    save_unheld_record(unheld_record)
    print(f"[target_manager] 초기화 완료 - 신규: {len(new_codes)}개, 제거: {len(removed)}개")


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────


def _update_guard_status(pos: dict, prefix: str, current_price: int, atr: float, guard_seconds: int, now_str: str, name: str):
    """지정된 접두사(turtle_s1 or turtle_s2)의 시간 가드 상태를 갱신한다."""
    signal         = pos.get(f"{prefix}_signal", False)
    breakout_at    = pos.get(f"{prefix}_breakout_at")
    breakout_price = pos.get(f"{prefix}_breakout_price")
    limit_price    = pos.get(f"{prefix}_limit_price")
    target_price   = pos.get(f"{prefix}_target_price") or 0.0
    peak_price     = pos.get(f"{prefix}_peak_price")
    entry_ready    = pos.get(f"{prefix}_entry_ready", False)

    # 1. 미돌파 상태 (대기 중)
    if not signal:
        if current_price > target_price:
            # 최초 돌파 발생
            signal = True
            breakout_at = now_str
            breakout_price = target_price
            limit_price = int(breakout_price - 0.5 * atr)
            peak_price = current_price
            entry_ready = False
            print(f"[target_manager] {name} {prefix.upper()} 최초 돌파! "
                  f"현재가: {current_price:,}원 > 목표가: {target_price:,}원 "
                  f"(마지노선: {limit_price:,}원, 가드 시작)")
        else:
            # 돌파선 미달
            entry_ready = False
    
    # 2. 돌파 상태 (타이머 작동 중)
    else:
        # 최고가 갱신
        if peak_price is None or current_price > peak_price:
            peak_price = current_price

        # A. 마지노선 붕괴 체크
        if limit_price is not None and current_price < limit_price:
            # 붕괴 리셋 발생
            new_target = int(peak_price + 0.2 * atr)
            print(f"[target_manager] {name} {prefix.upper()} 마지노선 붕괴! "
                  f"현재가: {current_price:,}원 < 마지노선: {limit_price:,}원 "
                  f"(타이머 리셋, 새로운 목표가: {new_target:,}원)")
            signal = False
            breakout_at = None
            breakout_price = None
            limit_price = None
            peak_price = None
            entry_ready = False
            target_price = new_target
            
        # B. 시간 가드 체크
        else:
            try:
                # 경과 시간 계산
                fmt = "%Y-%m-%d %H:%M:%S"
                dt_breakout = datetime.strptime(breakout_at, fmt)
                dt_now = datetime.strptime(now_str, fmt)
                elapsed = (dt_now - dt_breakout).total_seconds()
            except Exception:
                elapsed = 0.0

            if elapsed >= guard_seconds:
                # C. 시간 만료 시 최종 판정
                if current_price >= breakout_price:
                    # 최종 안착 성공
                    entry_ready = True
                    print(f"[target_manager] {name} {prefix.upper()} 시간 안착 성공! "
                          f"현재가: {current_price:,}원 >= 돌파선: {breakout_price:,}원 "
                          f"(경과: {elapsed/60:.1f}분 / 가드: {guard_seconds/60:.1f}분)")
                else:
                    # 최종 복구 실패 리셋
                    new_target = int(peak_price + 0.2 * atr)
                    print(f"[target_manager] {name} {prefix.upper()} 시간 만료 후 회복 실패! "
                          f"현재가: {current_price:,}원 < 돌파선: {breakout_price:,}원 "
                          f"(타이머 리셋, 새로운 목표가: {new_target:,}원)")
                    signal = False
                    breakout_at = None
                    breakout_price = None
                    limit_price = None
                    peak_price = None
                    entry_ready = False
                    target_price = new_target
            else:
                # 대기 중
                entry_ready = False
                print(f"[target_manager] {name} {prefix.upper()} 시간 가드 대기 중... "
                      f"(경과: {elapsed/60:.1f}분 / 가드: {guard_seconds/60:.1f}분, "
                      f"현재가: {current_price:,}원, 돌파선: {breakout_price:,}원)")

    # 갱신된 값을 다시 딕셔너리에 덮어쓰기
    pos[f"{prefix}_signal"]         = signal
    pos[f"{prefix}_breakout_at"]     = breakout_at
    pos[f"{prefix}_breakout_price"]  = breakout_price
    pos[f"{prefix}_limit_price"]     = limit_price
    pos[f"{prefix}_target_price"]    = target_price
    pos[f"{prefix}_peak_price"]      = peak_price
    pos[f"{prefix}_entry_ready"]     = entry_ready


def run_update(held_codes: Optional[Set[str]] = None):
    """미보유 종목 터틀 신호 갱신 (메인 실행 함수).

    실행 순서:
    1. 현재 보유 중인 종목 파악
    2. 미보유 종목에 대해:
       a. 일봉 60개 조회 (캐시 우선)
       b. 터틀 시스템1(20일), 시스템2(55일) 신고가 돌파 여부 + 풀백 상태 관리
    3. 보유 중인 종목은 unheld_record에서 제거

    Args:
        held_codes: 이미 파악한 보유 종목 코드 집합.
                    None이면 내부에서 ls_client.get_balance()로 조회한다.
    """
    print("[target_manager] 터틀 신호 갱신 시작")

    # ① 현재 보유 중인 종목 코드 목록 파악
    if held_codes is None:
        try:
            balance    = ls_client.get_balance()
            held_codes = {item["code"] for item in balance}
        except Exception as e:
            print(f"[target_manager] 잔고 조회 오류: {e}")
            held_codes = set()
    else:
        print(f"[target_manager] 전달받은 보유 종목 사용: {len(held_codes)}개")

    # ② 미보유 종목 목록 (감시 종목 중 보유 안 한 것만)
    watchlist    = get_watchlist()
    unheld_codes = [code for code in watchlist if code not in held_codes]

    if not unheld_codes:
        print("[target_manager] 미보유 종목 없음 (모두 보유 중)")
    else:
        # ③ 현재가 한 번에 조회
        prices = ls_client.get_multi_price(unheld_codes)

        # ④ 기존 상태 파일 불러오기
        unheld_record = load_unheld_record()

        # ⑤ 종목별 터틀 신호 갱신
        for code in unheld_codes:
            current_price = prices.get(code, 0)
            if current_price <= 0:
                print(f"[target_manager] {code} 현재가 조회 실패 → 스킵")
                continue

            # 일봉: 캐시 우선 — 캐시 미스 시에만 API 호출 + 속도 제한 대기 (A안)
            daily_60 = daily_chart_cache.get_daily_cached(code, count=60)
            if not daily_60:
                # 캐시 없음 → API 속도 제한 방지 5초 대기 후 직접 조회
                print(f"[target_manager] {code} 일봉 캐시 없음 → API 직접 조회")
                time.sleep(5.0)
                daily_60 = ls_client.get_daily_chart(code, count=60)
                if daily_60:
                    # 폴백 결과를 파일 캐시에 즉시 반영해 같은 실행 주기의 중복 폴백을 줄인다.
                    daily_chart_cache.update_daily_cache(code, daily_60)

            if not daily_60:
                print(f"[target_manager] {code} 일봉 데이터 없음 → 스킵")
                continue

            # 터틀 신호 계산 (직전 N일 장중 고가 vs 현재가)
            s1_high   = indicator_calc.calc_n_day_high(daily_60, n=20)
            s2_high   = indicator_calc.calc_n_day_high(daily_60, n=55)
            
            # ATR 계산
            atr_val   = indicator_calc.calc_atr(daily_60, period=20)
            if atr_val <= 0:
                print(f"[target_manager] {code} ATR 계산 불가 (0) → 스킵")
                continue

            # 최초 돌파 시각 기록용 현재 시각 문자열 (이번 주기 공통 사용)
            now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            name = watchlist.get(code, {}).get("name", code)

            if code not in unheld_record:
                # 처음 등록 — 신호 상태로 peak 초기값 결정
                unheld_record[code] = {
                    "turtle_s1_signal":          False,
                    "turtle_s1_breakout_at":     None,
                    "turtle_s1_breakout_price":  None,
                    "turtle_s1_limit_price":     None,
                    "turtle_s1_target_price":    s1_high,
                    "turtle_s1_peak_price":      None,
                    "turtle_s1_entry_ready":     False,
                    "turtle_s2_signal":          False,
                    "turtle_s2_breakout_at":     None,
                    "turtle_s2_breakout_price":  None,
                    "turtle_s2_limit_price":     None,
                    "turtle_s2_target_price":    s2_high,
                    "turtle_s2_peak_price":      None,
                    "turtle_s2_entry_ready":     False,
                }
            
            pos = unheld_record[code]
            # 구버전 호환: 새 필드가 없는 JSON 대비 기본값으로 초기화
            pos.setdefault("turtle_s1_signal",          False)
            pos.setdefault("turtle_s1_breakout_at",     None)
            pos.setdefault("turtle_s1_breakout_price",  None)
            pos.setdefault("turtle_s1_limit_price",     None)
            pos.setdefault("turtle_s1_target_price",    s1_high)
            pos.setdefault("turtle_s1_peak_price",      None)
            pos.setdefault("turtle_s1_entry_ready",     False)
            
            pos.setdefault("turtle_s2_signal",          False)
            pos.setdefault("turtle_s2_breakout_at",     None)
            pos.setdefault("turtle_s2_breakout_price",  None)
            pos.setdefault("turtle_s2_limit_price",     None)
            pos.setdefault("turtle_s2_target_price",    s2_high)
            pos.setdefault("turtle_s2_peak_price",      None)
            pos.setdefault("turtle_s2_entry_ready",     False)

            # 일봉 갱신 등으로 신고가가 타겟가보다 더 상향되었을 때의 동기화
            if pos["turtle_s1_target_price"] is None or s1_high > pos["turtle_s1_target_price"]:
                pos["turtle_s1_target_price"] = s1_high
            if pos["turtle_s2_target_price"] is None or s2_high > pos["turtle_s2_target_price"]:
                pos["turtle_s2_target_price"] = s2_high

            # 상태 업데이트 (주식 가드: 20분 = 1200초)
            _update_guard_status(pos, "turtle_s1", current_price, atr_val, 1200, now_str, name)
            _update_guard_status(pos, "turtle_s2", current_price, atr_val, 1200, now_str, name)

        # ⑥ 보유 종목은 unheld_record에서 제거
        for code in list(unheld_record.keys()):
            if code in held_codes:
                print(f"[target_manager] {code} 보유 중 → unheld_record에서 제거")
                del unheld_record[code]

        # ⑦ 저장
        save_unheld_record(unheld_record)

    print("[target_manager] 터틀 신호 갱신 완료")
