# target_manager.py
# 동적 목표가 산출 및 종목 상태 관리 모듈
#
# 역할:
#   1. 각 종목의 "동적 목표가(pending_target)"를 계산한다
#      공식: max(현재가 × 1.02, 240분봉 20MA × 1.005)
#   2. 현재가가 목표가를 넘었는지 확인하고, 언제부터 넘었는지 기록한다 (30분 가드)
#   3. 오리지널 터틀 트레이딩 신호를 체크한다 (20일 / 55일 신고가 돌파)
#
# unheld_stock_record.json 구조:
# {
#   "005930": {
#     "pending_target":    75000,   ← 동적 목표가 (이 가격을 넘어야 진입 검토)
#     "reference_price":   73500,   ← 목표가 기준가 (현재가가 이 아래면 목표가 하향 조정)
#     "above_target_since": null,   ← null이면 목표가 미달. 값이 있으면 30분 카운트 시작
#     "turtle_s1_signal":  false,   ← 시스템1(20일 신고가) 돌파 여부
#     "turtle_s2_signal":  false,   ← 시스템2(55일 신고가) 돌파 여부
#     "last_updated": "2026-04-15 10:00:00"
#   }
# }
#
#
# 목표가 관리 규칙:
#   처음 등록 시    : 현재가로 목표가와 기준가를 함께 계산해서 저장
#   현재가 ≥ 기준가 : 목표가 고정 — 올라가지 않음, 30분 타이머 계속 진행 가능
#   현재가 < 기준가 : 가격 하락 → 목표가 하향 조정 + 기준가 갱신 + 타이머 초기화
#
# 사용법:
#   import target_manager
#   target_manager.initialize_unheld_record(watchlist)  ← 09:05 종목 확정 직후 호출
#   target_manager.run_update()                         ← 미보유·보유 종목 목표가 전체 갱신

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
        종목코드 → {pending_target, above_target_since, last_updated} 딕셔너리
        예: {"005930": {"pending_target": 75000, "above_target_since": null, ...}}
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
    """09:05 종목 확정 직후 호출 — 신규 종목의 목표가·기준가를 즉시 초기화한다.

    이미 unheld_stock_record.json에 있는 종목은 덮어쓰지 않는다 (기존 타이머 보존).
    감시 종목에서 빠진 종목은 삭제한다.

    목표가 초기값은 현재가 × 1.02를 사용한다.
    (240분봉 20MA 포함한 정확한 목표가는 이후 run_update()에서 재계산)

    Args:
        watchlist: stock_screener.py가 확정한 감시 종목 딕셔너리
                   {"종목코드": {"name": ..., "score": ..., "atr": ...}}
    """
    print("[target_manager] 신규 종목 초기 목표가 저장 시작")

    # 기존 상태 파일 불러오기
    unheld_record = load_unheld_record()

    # 신규 종목 목록 (watchlist에 있지만 unheld_record에 없는 종목)
    new_codes = [code for code in watchlist if code not in unheld_record]

    if new_codes:
        # 신규 종목 현재가 일괄 조회 (API 1번 호출로 전 종목)
        prices = ls_client.get_multi_price(new_codes)
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        for code in new_codes:
            current_price = prices.get(code, 0)
            if current_price <= 0:
                print(f"[target_manager] {code} 현재가 조회 실패 → 초기화 스킵")
                continue

            # 초기 목표가: 현재가 × 1.02 (run_update()에서 240MA 반영해 재계산됨)
            init_target = int(current_price * 1.02)
            name        = watchlist.get(code, {}).get("name", code)

            unheld_record[code] = {
                "pending_target":           init_target,
                "reference_price":          current_price,
                "above_target_since":       None,
                "turtle_s1_signal":         False,
                "turtle_s2_signal":         False,
                "turtle_s1_breakout_since": None,   # S1(20일 신고가) 돌파 발생 시각
                "turtle_s2_breakout_since": None,   # S2(55일 신고가) 돌파 발생 시각
                "last_updated":             now_str,
            }
            print(f"[target_manager] {name}({code}) 초기화 — "
                  f"현재가: {current_price:,}원 / 초기 목표가: {init_target:,}원")

    # 감시 종목에서 제외된 종목 삭제
    removed = [code for code in list(unheld_record.keys()) if code not in watchlist]
    for code in removed:
        print(f"[target_manager] {code} 감시 종목 제외 → unheld_record 삭제")
        del unheld_record[code]

    save_unheld_record(unheld_record)
    print(f"[target_manager] 초기화 완료 — 신규: {len(new_codes)}개, 제거: {len(removed)}개")


# ─────────────────────────────────────────
# 목표가 계산
# ─────────────────────────────────────────

def calc_pending_target(code: str, current_price: int, indicators: dict) -> int:
    """동적 목표가(pending_target)를 계산한다.

    공식: max(현재가 × 1.02, 240분봉 20MA × 1.005)

    두 값 중 더 높은 가격을 목표가로 설정한다.
    - 현재가 × 1.02: 현재 가격에서 2% 이상 올라야 함
    - 240분봉 20MA × 1.005: 4시간봉 이동평균선보다 0.5% 위에 있어야 함
    둘 다 충족할 때만 추세적 상승으로 판단한다.

    Args:
        code:          종목코드 6자리 (로그 출력용)
        current_price: 현재가 (원 단위 정수)
        indicators:    get_all_indicators()가 반환한 지표 딕셔너리
                       {"atr": ..., "ma5": ..., "ma20": ..., "day10_low": ..., "ma240_20": ...}

    Returns:
        동적 목표가 (원 단위 정수).
        240분봉 데이터 없으면 현재가 × 1.02만 사용.
    """
    # 조건 1: 현재가 × 1.02 (2% 상승 필요)
    target_by_price = int(current_price * 1.02)

    ma240 = indicators.get("ma240_20", 0.0)

    if ma240 > 0:
        # 조건 2: 240분봉 20MA × 1.005 (0.5% 위 필요)
        target_by_ma240 = int(ma240 * 1.005)
        # 둘 중 더 높은 값을 목표가로 설정 (더 엄격한 조건 적용)
        target = max(target_by_price, target_by_ma240)
    else:
        # 240분봉 데이터가 없으면 현재가 × 1.02만 사용
        print(f"[target_manager] {code} 240분봉 데이터 없음 → 현재가 2% 기준만 사용")
        target = target_by_price

    return target


# ─────────────────────────────────────────
# 타이머 상태 관리
# ─────────────────────────────────────────

def update_above_target_time(code: str, current_price: int, stock_record: dict) -> dict:
    """현재가와 목표가를 비교해서 타이머 상태를 업데이트한다.

    현재가 ≥ pending_target → 타이머 시작 또는 유지
      처음 넘은 경우:    above_target_since에 현재 KST 시각 기록 (타이머 시작)
      이미 넘고 있었던 경우: above_target_since 그대로 유지 (타이머 계속)

    현재가 < pending_target → 타이머 초기화
      above_target_since를 null로 리셋

    Args:
        code:          종목코드 6자리 (로그 출력용)
        current_price: 현재가 (원)
        stock_record:  해당 종목의 현재 상태 딕셔너리
                       {"pending_target": ..., "above_target_since": ..., "last_updated": ...}

    Returns:
        업데이트된 상태 딕셔너리
    """
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    pending_target = stock_record.get("pending_target", 0)

    if current_price >= pending_target:
        # 현재가가 목표가 이상인 경우
        if stock_record.get("above_target_since") is None:
            # 처음으로 목표가를 넘은 경우 → 타이머 시작
            stock_record["above_target_since"] = now_kst
            print(f"[target_manager] {code} 목표가 돌파! "
                  f"{current_price:,}원 ≥ {pending_target:,}원 → 30분 카운트 시작")
        # 이미 넘고 있었던 경우: above_target_since 그대로 유지 (타이머 계속)
    else:
        # 현재가가 목표가 미달인 경우
        if stock_record.get("above_target_since") is not None:
            # 타이머가 켜져 있었는데 다시 목표가 아래로 내려간 경우 → 초기화
            print(f"[target_manager] {code} 목표가 이탈 "
                  f"({current_price:,}원 < {pending_target:,}원) → 타이머 초기화")
        stock_record["above_target_since"] = None

    stock_record["last_updated"] = now_kst
    return stock_record


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────


def run_update(held_codes: Optional[Set[str]] = None):
    """미보유 종목 목표가 갱신 + 터틀 신호 체크 (메인 실행 함수).

    실행 순서:
    1. 현재 보유 중인 종목 파악
    2. 미보유 종목에 대해:
       a. 일봉 60개 + 240분봉 조회 (API 2회/종목)
       b. 동적 목표가(pending_target) 계산·잠금 로직 적용
       c. 터틀 시스템1(20일), 시스템2(55일) 신고가 돌파 여부 기록
       d. 30분 가드 타이머 상태 업데이트
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

        # ⑤ 종목별 목표가·터틀 신호 갱신
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

            # 240분봉: 캐시 우선 — 30분 TTL 만료·없으면 API 재조회 + 캐시 갱신
            # 캐시 만료 시 API 속도 제한 방지를 위해 1초 대기 후 조회
            minute_data = daily_chart_cache.get_minute240_cached(code)
            if not minute_data:
                time.sleep(1.0)
                minute_data = ls_client.get_minute_chart(code, minute=240, count=25)
                if minute_data:
                    daily_chart_cache.update_minute240_cache(code, minute_data)
            minute_close = [m["close"] for m in minute_data] if minute_data else []

            close_list = [d["close"] for d in daily_60]

            # 지표 딕셔너리 구성
            indicators = {
                "atr":       indicator_calc.calc_atr(daily_60, period=20),
                "ma5":       indicator_calc.calc_ma(close_list, period=5),
                "ma20":      indicator_calc.calc_ma(close_list, period=20),
                "day10_low": indicator_calc.calc_10day_low(daily_60),
                "ma240_20":  indicator_calc.calc_ma(minute_close, period=20),
            }

            # 터틀 신호 계산 (직전 N일 장중 고가 vs 현재가)
            s1_high   = indicator_calc.calc_n_day_high(daily_60, n=20)
            s2_high   = indicator_calc.calc_n_day_high(daily_60, n=55)
            turtle_s1 = s1_high > 0 and current_price > s1_high
            turtle_s2 = s2_high > 0 and current_price > s2_high

            # ─── 목표가 결정 (잠금 + 하향 조정 로직) ─────────────────────────
            # 처음 등록:       목표가와 기준가를 함께 계산해서 저장
            # 현재가 < 기준가: 가격 하락 → 목표가 하향 조정 + 타이머 초기화
            # 현재가 ≥ 기준가: 목표가 고정 (위로 올라가지 않음)
            # ──────────────────────────────────────────────────────────────
            if code not in unheld_record:
                # 처음 등록
                new_target  = calc_pending_target(code, current_price, indicators)
                now_kst_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
                unheld_record[code] = {
                    "pending_target":           new_target,
                    "reference_price":          current_price,
                    "above_target_since":       None,
                    "turtle_s1_signal":         turtle_s1,
                    "turtle_s2_signal":         turtle_s2,
                    # 처음 등록 시점에 이미 신호가 True이면 지금 시각을 돌파 시각으로 기록
                    "turtle_s1_breakout_since": now_kst_str if turtle_s1 else None,
                    "turtle_s2_breakout_since": now_kst_str if turtle_s2 else None,
                    "last_updated":             None,
                }
            else:
                reference_price = unheld_record[code].get("reference_price", 0)

                # 구버전 호환: reference_price 없으면 현재가로 초기화
                if reference_price <= 0:
                    unheld_record[code]["reference_price"] = current_price
                    reference_price = current_price

                if current_price < reference_price:
                    # 현재가가 기준가 아래로 내려온 경우 → 목표가 하향 조정
                    old_target = unheld_record[code].get("pending_target", 0)
                    new_target = calc_pending_target(code, current_price, indicators)
                    unheld_record[code]["pending_target"]     = new_target
                    unheld_record[code]["reference_price"]    = current_price
                    unheld_record[code]["above_target_since"] = None
                    name = watchlist.get(code, {}).get("name", code)
                    print(f"[target_manager] {name}({code}) 기준가 하락 "
                          f"({reference_price:,}원 → {current_price:,}원) "
                          f"목표가: {old_target:,}원 → {new_target:,}원 → 타이머 초기화")
                # 현재가 ≥ 기준가: 목표가 유지

                now_kst_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

                # 구버전 호환: breakout_since 필드가 없는 JSON 대비 None으로 초기화
                unheld_record[code].setdefault("turtle_s1_breakout_since", None)
                unheld_record[code].setdefault("turtle_s2_breakout_since", None)

                # S1 신호 업데이트 + 돌파 시각 관리
                unheld_record[code]["turtle_s1_signal"] = turtle_s1
                if turtle_s1:
                    # 신호 True인데 시각이 없으면 지금 시각 기록 (처음 돌파 또는 구버전 호환)
                    if unheld_record[code]["turtle_s1_breakout_since"] is None:
                        unheld_record[code]["turtle_s1_breakout_since"] = now_kst_str
                    # 이미 시각이 있으면 그대로 유지 (타이머 계속)
                else:
                    # 신호 사라지면 시각 초기화
                    unheld_record[code]["turtle_s1_breakout_since"] = None

                # S2 신호 업데이트 + 돌파 시각 관리 (S1과 동일 구조)
                unheld_record[code]["turtle_s2_signal"] = turtle_s2
                if turtle_s2:
                    if unheld_record[code]["turtle_s2_breakout_since"] is None:
                        unheld_record[code]["turtle_s2_breakout_since"] = now_kst_str
                else:
                    unheld_record[code]["turtle_s2_breakout_since"] = None

            # 30분 가드 타이머 상태 업데이트
            unheld_record[code] = update_above_target_time(
                code, current_price, unheld_record[code]
            )

            # 로그 출력 (터틀 신호 현황 한눈에 보기)
            name     = watchlist.get(code, {}).get("name", code)
            s1_str   = "✅ S1" if turtle_s1 else "S1미달"
            s2_str   = "✅ S2" if turtle_s2 else "S2미달"
            # 돌파 시각은 HH:MM 형식만 표시 (S2 우선)
            s2_since = unheld_record[code].get("turtle_s2_breakout_since", "")
            s1_since = unheld_record[code].get("turtle_s1_breakout_since", "")
            if turtle_s2 and s2_since:
                since_str = f" / S2돌파:{s2_since[11:16]}"
            elif turtle_s1 and s1_since:
                since_str = f" / S1돌파:{s1_since[11:16]}"
            else:
                since_str = ""
            print(f"[target_manager] {name}({code}) "
                  f"현재가:{current_price:,}원 / {s1_str} / {s2_str}{since_str}")

        # ⑥ 보유 종목은 unheld_record에서 제거
        for code in list(unheld_record.keys()):
            if code in held_codes:
                print(f"[target_manager] {code} 보유 중 → unheld_record에서 제거")
                del unheld_record[code]

        # ⑦ 저장
        save_unheld_record(unheld_record)

    print("[target_manager] 터틀 신호 갱신 완료")
