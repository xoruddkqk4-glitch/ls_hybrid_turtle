# target_manager.py
# 동적 목표가 산출 및 미보유 종목 상태 관리 모듈
#
# 역할:
#   1. 각 종목의 "동적 목표가(pending_target)"를 계산한다
#      공식: max(현재가 × 1.02, 240분봉 20MA × 1.005)
#   2. 현재가가 목표가를 넘었는지 확인하고, 언제부터 넘었는지 기록한다
#   3. 이 정보를 unheld_stock_record.json에 저장한다
#
# unheld_stock_record.json 구조:
# {
#   "005930": {
#     "pending_target": 75000,         ← 동적 목표가 (이 가격을 넘어야 진입 검토)
#     "above_target_since": null,      ← null이면 아직 목표가 미달
#                                         값이 있으면 그 시각부터 30분 카운트 시작
#     "last_updated": "2026-04-13 10:00:00"
#   }
# }
#
# 사용법:
#   import target_manager
#   target_manager.run_update()   ← 미보유 종목 목표가 전체 갱신

import json
import os
import time
from datetime import datetime

import pytz

import indicator_calc
import ls_client
from config import lovely_stock_list

# 상태 파일 경로
UNHELD_RECORD_FILE = "unheld_stock_record.json"

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

def run_update():
    """미보유 종목 전체의 동적 목표가를 갱신한다 (메인 실행 함수).

    실행 순서:
    1. 현재 보유 중인 종목을 잔고 조회로 파악
    2. lovely_stock_list 중 보유하지 않은 종목에 대해서만:
       a. 현재가와 지표 데이터 조회
       b. 동적 목표가(pending_target) 계산
       c. 타이머 상태 업데이트
       d. unheld_stock_record.json에 저장
    3. 보유 중인 종목은 unheld_record에서 제거 (이미 보유 중이므로 목표가 감시 불필요)
    """
    print("[target_manager] 미보유 종목 목표가 갱신 시작")

    # ① 현재 보유 중인 종목 코드 목록 파악
    try:
        balance = ls_client.get_balance()
        held_codes = {item["code"] for item in balance}
    except Exception as e:
        print(f"[target_manager] 잔고 조회 오류: {e}")
        held_codes = set()

    # ② 미보유 종목 목록 (lovely_stock_list 중 보유 안 한 것만)
    unheld_codes = [code for code in lovely_stock_list if code not in held_codes]

    if not unheld_codes:
        print("[target_manager] 미보유 종목 없음 (모두 보유 중)")
        return

    # ③ 현재가 한 번에 조회 (t8407 멀티현재가 — API 호출 1번으로 전종목 조회)
    prices = ls_client.get_multi_price(unheld_codes)

    # ④ 기존 상태 파일 불러오기
    unheld_record = load_unheld_record()

    # ⑤ 종목별 목표가 갱신
    for code in unheld_codes:
        current_price = prices.get(code, 0)
        if current_price <= 0:
            print(f"[target_manager] {code} 현재가 조회 실패 → 스킵")
            continue

        # 지표 계산 (ATR, MA, 240분봉 20MA)
        # LS API 호출 속도 제한 방지: 종목 간 1초 대기 (초당 1회 제한 준수)
        time.sleep(1.0)
        indicators = indicator_calc.get_all_indicators(code)

        # 동적 목표가 계산
        new_target = calc_pending_target(code, current_price, indicators)

        # 기존 상태 가져오기 (없으면 새로 생성)
        if code not in unheld_record:
            unheld_record[code] = {
                "pending_target":    new_target,
                "above_target_since": None,
                "last_updated":       None,
            }
        else:
            # 목표가가 바뀌면 타이머도 초기화 (새로운 목표가를 처음부터 넘어야 하므로)
            old_target = unheld_record[code].get("pending_target", 0)
            if new_target != old_target:
                print(f"[target_manager] {code} 목표가 변경: "
                      f"{old_target:,}원 → {new_target:,}원 → 타이머 초기화")
                unheld_record[code]["pending_target"]    = new_target
                unheld_record[code]["above_target_since"] = None

        # 타이머 상태 업데이트
        unheld_record[code] = update_above_target_time(code, current_price, unheld_record[code])

        # 로그 출력 (현황 한눈에 보기)
        name        = lovely_stock_list.get(code, {}).get("name", code)
        above_since = unheld_record[code]["above_target_since"]
        status      = f"타이머 중 ({above_since}~)" if above_since else "목표가 미달"
        print(f"[target_manager] {name}({code}) "
              f"현재가: {current_price:,}원 / 목표가: {new_target:,}원 / 상태: {status}")

    # ⑥ 보유 종목은 unheld_record에서 제거
    for code in list(unheld_record.keys()):
        if code in held_codes:
            print(f"[target_manager] {code} 보유 중 → unheld_record에서 제거")
            del unheld_record[code]

    # ⑦ 저장
    save_unheld_record(unheld_record)
    print("[target_manager] 목표가 갱신 완료")
