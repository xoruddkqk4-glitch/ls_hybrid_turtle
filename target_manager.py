# target_manager.py
# 미보유 종목 터틀 신호 갱신 모듈
#
# 역할:
#   - 오리지널 터틀 트레이딩 신호를 체크한다 (20일 / 55일 신고가 돌파)
#   - 신고가 돌파 발생 시각을 기록해 timer_agent의 30분 가드 판정에 쓰이도록 한다
#
# unheld_stock_record.json 구조:
# {
#   "005930": {
#     "turtle_s1_signal":         false,    ← 시스템1(20일 신고가) 돌파 여부
#     "turtle_s2_signal":         false,    ← 시스템2(55일 신고가) 돌파 여부
#     "turtle_s1_breakout_since": null,     ← S1 돌파 발생 시각 (null=미돌파)
#     "turtle_s2_breakout_since": null      ← S2 돌파 발생 시각 (null=미돌파)
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
        종목코드 → {turtle_s1_signal, turtle_s2_signal, turtle_s1_breakout_since, turtle_s2_breakout_since} 딕셔너리
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
                "turtle_s1_signal":         False,
                "turtle_s2_signal":         False,
                "turtle_s1_breakout_since": None,   # S1(20일 신고가) 돌파 발생 시각
                "turtle_s2_breakout_since": None,   # S2(55일 신고가) 돌파 발생 시각
            }
            print(f"[target_manager] {name}({code}) 초기화")

    # 감시 종목에서 제외된 종목 삭제
    removed = [code for code in list(unheld_record.keys()) if code not in watchlist]
    for code in removed:
        print(f"[target_manager] {code} 감시 종목 제외 → unheld_record 삭제")
        del unheld_record[code]

    save_unheld_record(unheld_record)
    print(f"[target_manager] 초기화 완료 — 신규: {len(new_codes)}개, 제거: {len(removed)}개")


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────


def run_update(held_codes: Optional[Set[str]] = None):
    """미보유 종목 터틀 신호 갱신 (메인 실행 함수).

    실행 순서:
    1. 현재 보유 중인 종목 파악
    2. 미보유 종목에 대해:
       a. 일봉 60개 조회 (캐시 우선)
       b. 터틀 시스템1(20일), 시스템2(55일) 신고가 돌파 여부 + 돌파 시각 기록
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
            turtle_s1 = s1_high > 0 and current_price > s1_high
            turtle_s2 = s2_high > 0 and current_price > s2_high

            now_kst_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

            if code not in unheld_record:
                # 처음 등록
                unheld_record[code] = {
                    "turtle_s1_signal":         turtle_s1,
                    "turtle_s2_signal":         turtle_s2,
                    # 처음 등록 시점에 이미 신호가 True이면 지금 시각을 돌파 시각으로 기록
                    "turtle_s1_breakout_since": now_kst_str if turtle_s1 else None,
                    "turtle_s2_breakout_since": now_kst_str if turtle_s2 else None,
                }
            else:
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
