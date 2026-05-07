# turtle_order_logic.py
# 터틀 트레이딩 주문 실행 모듈
#
# 역할:
#   1. 얼마나 살지 계산한다 (리스크 기반 Unit 수량 계산)
#   2. 진입 주문을 실행하고 포지션 상태를 기록한다 (1차 진입)
#   3. 가격이 일정 이상 오르면 추가로 산다 (피라미딩)
#   4. 진입 신호가 온 종목과 기존 보유 종목의 피라미딩을 통합 처리한다
#
# held_stock_record.json 구조:
# {
#   "005930": {
#     "current_unit":      2,          ← 현재 몇 번 샀는지 (최대 4회)
#     "last_buy_price":    75000,      ← 가장 최근에 산 가격 (손절 기준)
#     "avg_buy_price":     74500,      ← 평균 매입 단가 (수익권 판단용)
#     "stop_loss_price":   72600,      ← 이 가격 이하로 내려오면 손절
#     "next_pyramid_price": 75600,     ← 이 가격 이상 오르면 추가 매수
#     "entry_type":        "NORMAL",   ← "NORMAL"(일반) 또는 "EXCEPTION"(예외)
#     "max_unit":          4,          ← 최대 추가 매수 횟수 (일반:4, 예외:2)
#     "total_qty":         20          ← 현재 보유 수량 합계 (평균가 계산용)
#   }
# }
#
# 사용법:
#   import turtle_order_logic
#   turtle_order_logic.run_orders(entry_signals)

import json
import os
import time

import ls_client
import indicator_calc
import trade_ledger
import telegram_alert
import sector_cache
from config import get_watchlist

# 보유 종목 상태 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR = os.path.dirname(os.path.abspath(__file__))
HELD_STOCK_RECORD_FILE = os.path.join(_DIR, "held_stock_record.json")


# ─────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────

def load_position_state() -> dict:
    """held_stock_record.json을 읽어서 반환한다.

    파일이 없거나 손상된 경우 빈 딕셔너리를 반환한다.

    Returns:
        종목코드 → 포지션 상태 딕셔너리
    """
    if os.path.exists(HELD_STOCK_RECORD_FILE):
        try:
            with open(HELD_STOCK_RECORD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, IOError):
            print(f"[turtle] {HELD_STOCK_RECORD_FILE} 읽기 오류 → 빈 상태로 시작")
    return {}


def save_position_state(state: dict):
    """포지션 상태를 held_stock_record.json에 저장한다.

    Args:
        state: 종목코드 → 포지션 상태 딕셔너리
    """
    try:
        with open(HELD_STOCK_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[turtle] 포지션 상태 저장 오류: {e}")


def get_total_units(state: dict) -> int:
    """포트폴리오 전체 유닛 합계를 반환한다.

    held_stock_record.json에 기록된 모든 종목의 current_unit을 더한 값이다.
    신규 진입·피라미딩 허용 여부를 판단하는 데 사용한다 (상한: 15 Unit).

    Args:
        state: load_position_state()가 반환한 포지션 딕셔너리

    Returns:
        현재 보유 중인 전체 유닛 수 (정수)
    """
    return sum(pos.get("current_unit", 0) for pos in state.values())


def get_sector_units(state: dict, sector: str) -> int:
    """특정 테마에 속한 종목들의 유닛 합계를 반환한다.

    같은 테마 종목에 유닛이 집중되는 것을 막기 위해 사용한다 (상한: 6 Unit).
    테마 정보는 sector_cache.get_stock_sector()를 통해 sector_cache.json에서 읽는다.

    Args:
        state:  load_position_state()가 반환한 포지션 딕셔너리
        sector: 확인할 테마 이름 (예: "인터넷은행", "제약바이오")
                빈 문자열이면 아무 종목도 집계되지 않음 (제한 없음)

    Returns:
        해당 테마에 현재 보유 중인 유닛 수 합계 (정수)
    """
    if not sector:
        return 0

    return sum(
        pos.get("current_unit", 0)
        for code, pos in state.items()
        if sector_cache.get_stock_sector(code) == sector
    )


# 포트폴리오 전체 유닛 상한
MAX_TOTAL_UNITS = 15

# 업종별 유닛 상한
MAX_SECTOR_UNITS = 6

# 1 Unit당 최대 매수금액 비율 (총자본 × 이 값)
MAX_UNIT_PURCHASE_RATIO = 0.1


# ─────────────────────────────────────────
# Unit 수량 계산
# ─────────────────────────────────────────

def calc_unit_size(code: str, price: int, atr_n: float, total_capital: int):
    """리스크 기반 Unit 수량을 계산한다.

    터틀 트레이딩의 핵심 공식:
    1 Unit 수량 = (총 자본 × 리스크팩터) / ATR(N)

    1 Unit 매수금이 총자본 × 10% 이하이면 리스크팩터 = 0.02 (기본값).
    초과하면 리스크팩터를 낮춰서 매수금을 상한에 맞춘다.
    종목마다 리스크팩터가 달라지며, held_stock_record.json에 저장된다.

    예외 진입 (1주 가격이 총자본의 2% 초과):
      → 1주 매수, max_unit=2, effective_risk_factor=None

    Args:
        code:          종목코드 6자리 (로그 출력용)
        price:         현재 1주 가격 (원)
        atr_n:         ATR(N) 값 (20일 평균 변동폭)
        total_capital: 총 자본 (원)

    Returns:
        (수량, 진입유형, 최대Unit, 유효리스크팩터) 튜플 또는 None(스킵)
        예: (200, "NORMAL", 3, 0.004) 또는 (1, "EXCEPTION", 2, None) 또는 None
    """
    name = get_watchlist().get(code, {}).get("name", code)

    # 1주 가격이 총자본에서 차지하는 비율
    one_share_ratio = price / total_capital if total_capital > 0 else 1.0

    # 1주 가격이 총자본 2% 초과 → 예외 진입 (1주, 최대 2 Unit)
    if one_share_ratio > 0.02:
        allow_exception = (
            os.getenv("LS_ALLOW_EXCEPTION_ENTRY", "True").strip().lower() == "true"
        )
        if not allow_exception:
            print(f"[turtle] {name}({code}) 예외 진입 비활성화(LS_ALLOW_EXCEPTION_ENTRY=False) → 스킵")
            return None
        print(f"[turtle] {name}({code}) 예외 진입: 1주 가격({price:,}원)이 "
              f"총자본 {one_share_ratio*100:.1f}% → 1주 매수 (max_unit=2)")
        return (1, "EXCEPTION", 2, None)

    # 일반 진입: ATR 기반 수량 계산
    if atr_n <= 0:
        print(f"[turtle] {name}({code}) ATR(N)=0 → 수량 계산 불가, 스킵")
        return None

    # 기본 리스크팩터 0.02로 수량 계산
    # 공식: qty = (총자본 × 리스크팩터) / ATR
    qty = int((total_capital * 0.02) / atr_n)
    if qty <= 0:
        print(f"[turtle] {name}({code}) 계산된 수량=0 (ATR={atr_n:,.0f}) → 스킵")
        return None

    # 1 Unit 최대 매수금액 상한 확인 (총자본 × 10%)
    max_unit_amount = total_capital * MAX_UNIT_PURCHASE_RATIO
    purchase_amount = qty * price

    if purchase_amount <= max_unit_amount:
        # 상한 이내 → 리스크팩터 그대로 0.02
        effective_risk_factor = 0.02
    else:
        # 상한 초과 → 리스크팩터를 줄여서 매수금을 상한에 맞춤
        # 공식: rf = (상한금액 × ATR) / (총자본 × 현재가)
        effective_risk_factor = (max_unit_amount * atr_n) / (total_capital * price)
        qty = int((total_capital * effective_risk_factor) / atr_n)
        print(f"[turtle] {name}({code}) 매수금 상한 초과 → "
              f"리스크팩터 0.02 → {effective_risk_factor:.4f}로 조정 "
              f"({qty}주, 매수금: {qty * price:,.0f}원 / 상한: {max_unit_amount:,.0f}원)")

    return (qty, "NORMAL", 3, effective_risk_factor)


# ─────────────────────────────────────────
# 피라미딩 트리거 확인
# ─────────────────────────────────────────

def check_pyramid_trigger(code: str, current_price: int, pos: dict, atr_n: float) -> bool:
    """피라미딩(추가 매수) 조건이 충족됐는지 확인한다.

    조건:
      ① 현재가 ≥ next_pyramid_price (마지막 매수가 + 0.5×ATR)
      ② 아직 최대 Unit에 도달하지 않음 (current_unit < max_unit)

    Args:
        code:          종목코드 (로그 출력용)
        current_price: 현재가
        pos:           position_state의 해당 종목 상태
        atr_n:         ATR(N) 값 (현재 시점 기준 — 실제로는 pos에서 가져온 값 사용)

    Returns:
        True:  피라미딩 조건 충족 → 추가 매수 실행
        False: 조건 미충족
    """
    current_unit    = pos.get("current_unit",      0)
    max_unit        = pos.get("max_unit",           4)
    next_pyramid_price = pos.get("next_pyramid_price", 0)

    # 이미 최대 Unit에 도달한 경우
    if current_unit >= max_unit:
        return False

    # 현재가가 피라미딩 기준가 미달인 경우
    if current_price < next_pyramid_price:
        return False

    name = get_watchlist().get(code, {}).get("name", code)
    print(f"[turtle] {name}({code}) 피라미딩 조건 충족! "
          f"현재가 {current_price:,}원 ≥ 피라미딩 기준가 {next_pyramid_price:,}원 "
          f"(현재 {current_unit}/{max_unit} Unit)")
    return True


# ─────────────────────────────────────────
# 주문 실행
# ─────────────────────────────────────────

def place_entry_order(
    code: str, qty: int, price: int, atr_n: float,
    entry_type: str, max_unit: int,
    entry_source: str = "TURTLE_S1",
    effective_risk_factor=None
):
    """1차 진입 주문을 실행하고 포지션 상태를 기록한다.

    실행 순서:
    1. 감시 종목(get_watchlist()) 포함 여부 확인 (안전장치)
    2. 매수 주문 실행
    3. held_stock_record.json에 포지션 상태 저장
    4. 체결 원장(trade_ledger)에 기록
    5. 텔레그램으로 알림 발송

    저장되는 포지션 상태:
      - stop_loss_price    = price - 2 × atr_n  (손절가)
      - next_pyramid_price = price + 0.5 × atr_n (다음 피라미딩 기준가)
      - entry_source       = 진입 경로 ("TURTLE_S1" / "TURTLE_S2")

    Args:
        code:         종목코드 6자리
        qty:          매수 수량 (주)
        price:        현재가 (시장가 주문 기준 예상 가격)
        atr_n:        ATR(N) 값
        entry_type:   "NORMAL" 또는 "EXCEPTION"
        max_unit:     최대 Unit 횟수 (4 또는 2)
        entry_source: 진입 경로 ("TURTLE_S1" / "TURTLE_S2")
    """
    # ① 안전장치: 감시 종목이 아니면 절대 주문하지 않음
    watchlist = get_watchlist()
    if code not in watchlist:
        print(f"[turtle] {code} 감시 종목 외 → 진입 주문 거부")
        return

    name = watchlist[code]["name"]
    before_qty = ls_client.get_holding_qty(code)

    # ② 매수 주문 실행
    result = ls_client.place_order(code, qty, "BUY", "MARKET")
    if not result["success"]:
        print(f"[turtle] 진입 주문 실패: {name}({code}) {qty}주 | 오류: {result['message']}")
        return

    fill = ls_client.wait_for_order_fill(code, "BUY", before_qty, qty)
    if not fill["filled"]:
        print(
            f"[turtle] 진입 체결 미확정 → 기록/알림 스킵: {name}({code}) "
            f"(요청 {qty}주, 확인 {fill['filled_qty']}주)"
        )
        return

    order_no = result["order_no"]

    # ③ 포지션 상태 계산 및 저장
    stop_loss_price     = int(price - 2 * atr_n)     # 2N 하락 시 손절 기준가
    next_pyramid_price  = int(price + 0.5 * atr_n)   # 0.5N 상승 시 피라미딩 기준가

    position_state = load_position_state()
    position_state[code] = {
        "stock_name":            name,                   # 종목명 저장 — 매도 시 watchlist 날짜 불일치 대비
        "current_unit":          1,
        "last_buy_price":        price,
        "avg_buy_price":         price,                  # 1차 진입이므로 평균가 = 진입가
        "stop_loss_price":       stop_loss_price,
        "next_pyramid_price":    next_pyramid_price,
        "entry_type":            entry_type,
        "max_unit":              max_unit,
        "total_qty":             qty,                    # 피라미딩 시 평균가 계산에 사용
        "entry_source":          entry_source,           # 진입 경로 (TURTLE_S1 / TURTLE_S2)
        "effective_risk_factor": effective_risk_factor,  # 종목별 유효 리스크팩터 (피라미딩 수량 계산에 재사용)
    }
    save_position_state(position_state)

    # ④ 체결 원장 기록
    # 진입 경로(entry_source)에 따라 매매구분(source) 세분화
    source_map = {
        "TURTLE_S1": "ENTRY_S1",   # 20일 신고가 돌파 + 30분 가드 진입
        "TURTLE_S2": "ENTRY_S2",   # 55일 신고가 돌파 + 30분 가드 진입
    }
    ledger_source = source_map.get(entry_source, "ENTRY_S1")

    trade_ledger.append_trade({
        "side":        "BUY",
        "stock_code":  code,
        "stock_name":  name,
        "qty":         qty,
        "unit_price":  price,
        "order_no":    order_no,
        "order_type":  "MARKET",
        "source":      ledger_source,
        "note":        f"1차 진입({entry_type}) | 손절가: {stop_loss_price:,}원 | "
                       f"피라미딩: {next_pyramid_price:,}원",
    })

    # ⑤ 텔레그램 알림
    source_label = {
        "TURTLE_S2": "터틀S2(55일신고가)",
        "TURTLE_S1": "터틀S1(20일신고가)",
    }.get(entry_source, entry_source)
    telegram_alert.SendMessage(
        f"✅ 터틀 진입\n"
        f"종목: {name}({code})\n"
        f"수량: {qty:,}주 @{price:,}원\n"
        f"진입 경로: {source_label} / 유형: {entry_type} (최대 {max_unit} Unit)\n"
        f"손절가: {stop_loss_price:,}원 | 다음 피라미딩: {next_pyramid_price:,}원"
    )


def place_pyramid_order(code: str, qty: int, price: int, atr_n: float):
    """피라미딩(추가 매수) 주문을 실행하고 포지션 상태를 업데이트한다.

    피라미딩 시 업데이트 내용:
      - current_unit: +1
      - last_buy_price: 이번 매수가로 갱신
      - avg_buy_price: 가중 평균으로 재계산
      - stop_loss_price: 새 last_buy_price - 2×atr_n 으로 갱신 (손절가도 올라감)
      - next_pyramid_price: 새 last_buy_price + 0.5×atr_n 으로 갱신
      - total_qty: 기존 + qty

    Args:
        code:   종목코드 6자리
        qty:    추가 매수 수량 (주)
        price:  현재가
        atr_n:  ATR(N) 값
    """
    # ① 안전장치: 감시 종목이 아니면 절대 주문하지 않음
    watchlist = get_watchlist()
    if code not in watchlist:
        print(f"[turtle] {code} 감시 종목 외 → 피라미딩 주문 거부")
        return

    name = watchlist[code]["name"]
    before_qty = ls_client.get_holding_qty(code)

    # ② 기존 포지션 상태 확인
    position_state = load_position_state()
    if code not in position_state:
        print(f"[turtle] {code} held_stock_record.json에 기록 없음 → 피라미딩 불가")
        return

    pos         = position_state[code]
    current_unit = pos.get("current_unit", 0)
    max_unit     = pos.get("max_unit", 4)

    # 최대 Unit 도달 여부 재확인 (혹시 모를 중복 실행 방지)
    if current_unit >= max_unit:
        print(f"[turtle] {name}({code}) 이미 최대 Unit ({current_unit}/{max_unit}) → 피라미딩 중단")
        return

    # ③ 피라미딩 매수 주문 실행
    result = ls_client.place_order(code, qty, "BUY", "MARKET")
    if not result["success"]:
        print(
            f"[turtle] 피라미딩 주문 실패: {name}({code}) {qty}주 "
            f"({current_unit+1}차) | 오류: {result['message']}"
        )
        return

    fill = ls_client.wait_for_order_fill(code, "BUY", before_qty, qty)
    if not fill["filled"]:
        print(
            f"[turtle] 피라미딩 체결 미확정 → 기록/알림 스킵: {name}({code}) "
            f"(요청 {qty}주, 확인 {fill['filled_qty']}주)"
        )
        return

    order_no = result["order_no"]

    # ④ 평균 매입단가 재계산 (가중 평균)
    old_total_qty = pos.get("total_qty", 0)
    old_avg_price = pos.get("avg_buy_price", price)
    new_total_qty = old_total_qty + qty
    # 새 평균 = (기존 총매입금 + 이번 매입금) / 새 총수량
    new_avg_price = int(
        (old_avg_price * old_total_qty + price * qty) / new_total_qty
    ) if new_total_qty > 0 else price

    # ⑤ 포지션 상태 업데이트
    new_unit             = current_unit + 1
    new_stop_loss_price  = int(price - 2 * atr_n)      # 새 매수가 기준으로 손절가 올라감
    new_next_pyramid     = int(price + 0.5 * atr_n)    # 새 매수가 기준으로 피라미딩 기준도 올라감

    position_state[code].update({
        "current_unit":       new_unit,
        "last_buy_price":     price,
        "avg_buy_price":      new_avg_price,
        "stop_loss_price":    new_stop_loss_price,
        "next_pyramid_price": new_next_pyramid,
        "total_qty":          new_total_qty,
    })
    save_position_state(position_state)

    # ⑥ 체결 원장 기록
    trade_ledger.append_trade({
        "side":        "BUY",
        "stock_code":  code,
        "stock_name":  name,
        "qty":         qty,
        "unit_price":  price,
        "order_no":    order_no,
        "order_type":  "MARKET",
        "source":      "PYRAMID",
        "note":        f"{new_unit}차 피라미딩 | 손절가: {new_stop_loss_price:,}원",
    })

    # ⑦ 텔레그램 알림
    telegram_alert.SendMessage(
        f"📈 피라미딩\n"
        f"종목: {name}({code})\n"
        f"추가 수량: {qty:,}주 @{price:,}원 ({new_unit}/{max_unit} Unit)\n"
        f"평균 단가: {new_avg_price:,}원\n"
        f"새 손절가: {new_stop_loss_price:,}원 | 다음 피라미딩: {new_next_pyramid:,}원"
    )


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_orders(entry_signals: list):
    """진입 신호 처리 + 기존 포지션 피라미딩 체크 (메인 실행 함수).

    실행 순서:
    1. 총 자본 조회
    2. 진입 신호 종목 처리 (새 포지션 진입)
    3. 기존 보유 종목 피라미딩 체크 및 실행

    Args:
        entry_signals: timer_agent.run_timer_check()가 반환한 진입 신호 목록
                       예: [{"code": "005930", "entry_source": "TURTLE_S1"}, ...]
    """
    print("[turtle] 주문 처리 시작")

    # ① 총 자본 조회 (Unit 수량 계산 기준)
    total_capital = ls_client.get_total_capital()
    if total_capital <= 0:
        print("[turtle] 총자본이 0원 → 주문 중단 (모의투자 계좌에 가상 자금이 없거나 조회 실패)")
        return

    print(f"[turtle] 총 자본: {total_capital:,}원")

    # ② 포지션 상태 불러오기
    position_state = load_position_state()
    held_codes     = list(position_state.keys())

    # ③ 진입 신호 딕셔너리 변환 (종목코드 → entry_source)
    entry_signal_map = {s["code"]: s["entry_source"] for s in entry_signals}
    signal_codes     = list(entry_signal_map.keys())

    # ④ 현재가 조회 대상: 진입 신호 종목 + 기존 보유 종목
    watchlist = get_watchlist()
    price_query_codes = list({
        c for c in (signal_codes + held_codes)
        if c in watchlist
    })

    if not price_query_codes:
        print("[turtle] 처리할 종목 없음")
        return

    # 현재가 한 번에 조회
    prices = ls_client.get_multi_price(price_query_codes)

    # ─────────────────────────────────────
    # [A] 신규 진입 처리
    # ─────────────────────────────────────
    for signal in entry_signals:
        code         = signal["code"]
        entry_source = signal["entry_source"]

        # 안전장치: 감시 종목 외 종목 스킵
        if code not in watchlist:
            print(f"[turtle] {code} 감시 종목 외 → 진입 스킵")
            continue

        # 이미 보유 중인 종목은 진입 스킵 (피라미딩 구간에서 처리)
        if code in position_state:
            print(f"[turtle] {watchlist[code]['name']}({code}) 이미 보유 중 → 신규 진입 스킵")
            continue

        current_price = prices.get(code, 0)
        if current_price <= 0:
            print(f"[turtle] {code} 현재가 조회 실패 → 진입 스킵")
            continue

        # 지표 계산 (ATR 등) — API 속도 제한 방지 (종목 간 5초 대기)
        time.sleep(5.0)
        indicators = indicator_calc.get_all_indicators(code)
        atr_n      = indicators.get("atr", 0.0)
        if atr_n <= 0:
            print(f"[turtle] {code} ATR(N)=0 → 진입 불가")
            continue

        # Unit 수량 계산
        result = calc_unit_size(code, current_price, atr_n, total_capital)
        if result is None:
            continue

        qty, entry_type, max_unit, effective_rf = result

        # 포트폴리오 전체·업종별 유닛 한도 확인
        # 직전 진입으로 파일이 갱신됐을 수 있으므로 파일을 다시 읽어서 정확한 합계를 구함
        fresh_state         = load_position_state()
        current_total_units = get_total_units(fresh_state)
        entry_name          = watchlist.get(code, {}).get("name", code)

        if current_total_units >= MAX_TOTAL_UNITS:
            print(f"[turtle] 포트폴리오 유닛 한도({MAX_TOTAL_UNITS} Unit) 도달 → "
                  f"{entry_name}({code}) 신규 진입 스킵 (현재 {current_total_units} Unit)")
            continue

        # 테마별 한도 확인
        entry_sector = sector_cache.get_stock_sector(code)
        sector_units = get_sector_units(fresh_state, entry_sector)
        if entry_sector and sector_units >= MAX_SECTOR_UNITS:
            print(f"[turtle] 테마 유닛 한도({MAX_SECTOR_UNITS} Unit) 도달 → "
                  f"{entry_name}({code}) 신규 진입 스킵 "
                  f"(테마: {entry_sector}, 현재 {sector_units} Unit)")
            continue

        # 진입 주문 실행 (진입 경로 전달)
        place_entry_order(code, qty, current_price, atr_n, entry_type, max_unit, entry_source, effective_rf)

    # ─────────────────────────────────────
    # [B] 기존 포지션 피라미딩 처리
    #    진입 주문 후 position_state를 다시 읽어야 함
    # ─────────────────────────────────────
    position_state = load_position_state()

    for code, pos in list(position_state.items()):
        # 안전장치: 감시 종목 외 종목 스킵 (수동 보유 종목 등)
        if code not in watchlist:
            continue

        current_price = prices.get(code, 0)
        if current_price <= 0:
            print(f"[turtle] {code} 현재가 조회 실패 → 피라미딩 스킵")
            continue

        # 피라미딩 가격 조건 사전 체크 (파일에 저장된 값으로 먼저 판단)
        # → 조건이 안 되면 API 호출·대기 없이 즉시 넘어감
        if pos.get("current_unit", 0) >= pos.get("max_unit", 4):
            continue  # 이미 최대 Unit
        if current_price < pos.get("next_pyramid_price", 0):
            continue  # 피라미딩 기준가 미달

        # 가격 조건 충족 종목만 API 호출 (종목 간 5초 대기)
        time.sleep(5.0)
        indicators = indicator_calc.get_all_indicators(code)
        atr_n      = indicators.get("atr", 0.0)
        if atr_n <= 0:
            continue

        # ATR 포함 정밀 피라미딩 트리거 확인
        if not check_pyramid_trigger(code, current_price, pos, atr_n):
            continue

        # 피라미딩 수량: 진입 시 저장한 리스크팩터 재사용 (종목별 고정값)
        effective_rf = pos.get("effective_risk_factor")
        if effective_rf is None:
            entry_type_pos = pos.get("entry_type", "NORMAL")
            if entry_type_pos == "NORMAL":
                # 업데이트 전 진입 종목(필드 없음) → 기본값 0.02로 대체
                effective_rf = 0.02
            else:
                # EXCEPTION / MANUAL 진입 종목 → 1주 고정
                qty = 1
                effective_rf = None  # qty 계산 건너뜀
        if effective_rf is not None:
            qty = int((total_capital * effective_rf) / atr_n)
            if qty <= 0:
                continue

        # 종목 이름 조회 (로그 출력용)
        pyramid_name = get_watchlist().get(code, {}).get("name", code)

        # 1 Unit 매수금 상한 재검사 (총자본 × 10%)
        # 폴백 경로(effective_risk_factor 필드 없는 옛 종목)나 시장가 변동으로
        # qty × 현재가가 상한을 넘으면 cap에 맞춰 자동으로 줄임
        max_unit_amount = total_capital * MAX_UNIT_PURCHASE_RATIO
        purchase_amount = qty * current_price
        if purchase_amount > max_unit_amount:
            capped_qty = int(max_unit_amount / current_price)
            if capped_qty <= 0:
                # 1주 가격이 이미 상한을 넘는 경우 → 이번 사이클 스킵
                print(f"[turtle] {pyramid_name}({code}) 1주 가격({current_price:,}원)이 "
                      f"1 Unit 상한({max_unit_amount:,.0f}원) 초과 → 피라미딩 스킵")
                continue
            print(f"[turtle] {pyramid_name}({code}) 피라미딩 매수금 상한 초과 → "
                  f"{qty}주 → {capped_qty}주로 조정 "
                  f"(매수금: {capped_qty * current_price:,}원 / 상한: {max_unit_amount:,.0f}원)")
            qty = capped_qty

        # 포트폴리오 전체·업종별 유닛 한도 확인 (피라미딩 직전 재확인)
        fresh_state         = load_position_state()
        current_total_units = get_total_units(fresh_state)

        if current_total_units >= MAX_TOTAL_UNITS:
            print(f"[turtle] 포트폴리오 유닛 한도({MAX_TOTAL_UNITS} Unit) 도달 → "
                  f"{pyramid_name}({code}) 피라미딩 스킵 (현재 {current_total_units} Unit)")
            continue

        # 테마별 한도 확인
        pyramid_sector = sector_cache.get_stock_sector(code)
        sector_units   = get_sector_units(fresh_state, pyramid_sector)
        if pyramid_sector and sector_units >= MAX_SECTOR_UNITS:
            print(f"[turtle] 테마 유닛 한도({MAX_SECTOR_UNITS} Unit) 도달 → "
                  f"{pyramid_name}({code}) 피라미딩 스킵 "
                  f"(테마: {pyramid_sector}, 현재 {sector_units} Unit)")
            continue

        # 피라미딩 주문 실행
        place_pyramid_order(code, qty, current_price, atr_n)

    print("[turtle] 주문 처리 완료")
