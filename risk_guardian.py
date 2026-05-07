# risk_guardian.py
# 손절·익절 감시 모듈
#
# 역할:
#   현재 보유 중인 종목들을 실시간으로 감시하면서,
#   손실이 너무 커지면 강제로 팔고 (하드 손절),
#   수익이 충분히 났을 때 추세가 꺾이면 파는 (트레일링 스탑) 기능을 실행한다.
#
# 두 가지 청산 조건:
#
#   [1] 하드 손절 (2N Stop — 최우선 처리)
#       현재가 ≤ stop_loss_price (마지막 매수가 - 2×ATR)
#       → 추세 예측이 완전히 틀린 것 → 즉시 전량 매도
#
#   [2] 트레일링 스탑 (익절)
#       ① 10일 신저가 경신: 최근 10일 중 가장 낮은 종가보다 현재가가 낮아짐
#          → 하락 추세로 전환 → 무조건 청산
#       ② 5MA 하향 돌파: 현재가가 5일 이동평균선 아래로 내려감
#          → 단, 평균 매입단가보다 현재가가 높을 때(수익권)만 적용
#          → 손해 보는 상태에서는 5MA 돌파로 팔지 않음
#
# 사용법:
#   import risk_guardian
#   risk_guardian.run_guardian()

import time
from datetime import datetime
from typing import Optional, Set

import pytz

import ls_client
import indicator_calc
import trade_ledger
import telegram_alert
from config import get_watchlist
from turtle_order_logic import load_position_state, save_position_state


def _get_stock_name(code: str) -> str:
    """종목명을 조회한다. watchlist → held_stock_record → 코드 순으로 찾는다.

    당일 watchlist 날짜가 맞지 않아 get_watchlist()가 빈 딕셔너리를 반환해도
    held_stock_record.json에 저장된 stock_name으로 이름을 찾는다.
    """
    name = get_watchlist().get(code, {}).get("name", "")
    if name:
        return name
    # watchlist에 없으면 held_stock_record에서 조회 (날짜 불일치 등으로 watchlist가 비어있을 때)
    held = load_position_state()
    return held.get(code, {}).get("stock_name", code)


# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")


# ─────────────────────────────────────────
# 시간 필터 헬퍼
# ─────────────────────────────────────────

def _is_stop_blackout() -> bool:
    """지금이 2N 손절 보류 시간대인지 확인한다.

    장 시작 직후(09:00~09:30)와 장 마감 직전(15:00~15:30)은
    일시적인 급락이 많아 손절을 보류한다.
    이 시간대가 지나면 다음 run_all.py 실행 때 자동으로 재확인한다.

    Returns:
        True:  보류 시간대 → 이번 회차 손절 스킵
        False: 정상 감시 시간 → 기존대로 즉시 손절
    """
    now   = datetime.now(_KST)
    hhmm  = now.hour * 100 + now.minute  # 예: 09:15 → 915, 15:20 → 1520

    # 09:00~09:30: 장 시작 직후 변동성 구간
    if 900 <= hhmm < 930:
        return True

    # 15:00~15:30: 장 마감 직전 변동성 구간
    if 1500 <= hhmm < 1530:
        return True

    return False


# ─────────────────────────────────────────
# 청산 조건 확인
# ─────────────────────────────────────────

def check_hard_stop(code: str, current_price: int, pos: dict) -> bool:
    """하드 손절 조건을 확인한다.

    현재가가 미리 계산된 손절가(stop_loss_price) 이하로 내려온 경우
    즉시 전량 매도 신호를 반환한다.

    stop_loss_price는 turtle_order_logic에서 진입/피라미딩 시마다
    "마지막 매수가 - 2×ATR"로 자동 계산되어 저장된다.

    Args:
        code:          종목코드 (로그 출력용)
        current_price: 현재가
        pos:           position_state의 해당 종목 딕셔너리

    Returns:
        True:  손절 조건 충족 → 즉시 전량 매도
        False: 아직 괜찮음
    """
    stop_loss_price = pos.get("stop_loss_price", 0)
    if stop_loss_price <= 0:
        return False

    if current_price <= stop_loss_price:
        name = _get_stock_name(code)

        # 장 변동성 시간대(09:00~09:30, 15:00~15:30)에는 손절을 보류한다
        # 이 시간대의 급락은 일시적인 경우가 많아 종가가 회복되기도 함
        if _is_stop_blackout():
            now_str = datetime.now(_KST).strftime("%H:%M")
            # 09:00~09:30이면 "장 시작 직후", 15:00~15:30이면 "장 마감 직전"으로 구분
            now_hhmm = datetime.now(_KST).hour * 100 + datetime.now(_KST).minute
            period   = "장 시작 직후(09:00~09:30)" if now_hhmm < 1000 else "장 마감 직전(15:00~15:30)"
            msg = (
                f"⚠️ 2N 손절 조건 발동 (보류 중)\n"
                f"종목: {name}({code})\n"
                f"현재가: {current_price:,}원 ≤ 손절가: {stop_loss_price:,}원\n"
                f"사유: {period} 변동성 시간대({now_str}) — 다음 회차에 재확인"
            )
            print(f"[risk_guardian] {msg}")
            return False  # 이번 회차는 손절하지 않음

        # 보류 시간대가 아니면 정상적으로 손절 발동
        print(f"[risk_guardian] {name}({code}) ❌ 하드 손절 발동! "
              f"현재가 {current_price:,}원 ≤ 손절가 {stop_loss_price:,}원")
        return True

    return False


def check_trailing_stop(code: str, current_price: int, pos: dict, indicators: dict):
    """트레일링 스탑 조건을 확인한다.

    두 가지 익절 조건 중 하나라도 충족되면 청산 이유를 반환한다.

    조건 ①: 10일 신저가 경신
      현재가 ≤ 최근 10일 중 가장 낮은 종가
      → 추세가 꺾인 것으로 판단, 수익·손실 여부와 관계없이 청산

    조건 ②: 5MA 하향 돌파 (수익권에서만)
      현재가 < 5일 이동평균선 AND 현재가 > 평균 매입단가
      → 수익이 난 상태에서 단기 추세가 꺾일 때 익절

    Args:
        code:          종목코드 (로그 출력용)
        current_price: 현재가
        pos:           position_state의 해당 종목 딕셔너리
        indicators:    get_all_indicators()가 반환한 지표 딕셔너리

    Returns:
        청산 이유 문자열 (예: "10일 신저가 경신") 또는 None (청산 조건 미충족)
    """
    name         = _get_stock_name(code)
    day10_low    = indicators.get("day10_low", 0)
    ma5          = indicators.get("ma5",       0.0)
    avg_buy_price = pos.get("avg_buy_price",   0)

    # 조건 ①: 10일 신저가 경신 확인 (수익·손실 여부와 관계없이 청산)
    if day10_low > 0 and current_price <= day10_low:
        print(f"[risk_guardian] {name}({code}) 📉 10일 신저가 경신! "
              f"현재가 {current_price:,}원 ≤ 10일 신저가 {day10_low:,}원")
        # 수익권이면 익절, 손실권이면 손절로 구분
        if avg_buy_price > 0 and current_price > avg_buy_price:
            return "10일 신저가 경신 익절"
        else:
            return "10일 신저가 경신 손절"

    # 조건 ②: 5MA 하향 돌파 (수익권일 때만)
    if ma5 > 0 and current_price < ma5:
        if avg_buy_price > 0 and current_price > avg_buy_price:
            # 수익권(현재가 > 평균 매입단가)에서 5MA 아래로 내려온 경우
            profit_pct = (current_price - avg_buy_price) / avg_buy_price * 100
            print(f"[risk_guardian] {name}({code}) 📉 5MA 하향 돌파 (수익권 익절)! "
                  f"현재가 {current_price:,}원 < 5MA {ma5:,.0f}원 "
                  f"(수익률 +{profit_pct:.1f}%)")
            return "5MA 하향 돌파 익절"
        elif avg_buy_price > 0 and current_price <= avg_buy_price:
            # 손해 보는 상태에서는 5MA 돌파로 팔지 않음
            print(f"[risk_guardian] {name}({code}) 5MA 아래지만 손실 구간 "
                  f"→ 5MA 스탑 미적용 (하드 손절 대기)")

    return None  # 청산 조건 없음


# ─────────────────────────────────────────
# 청산 주문 실행
# ─────────────────────────────────────────

def place_exit_order(code: str, qty: int, reason: str, current_price: int = 0):
    """전량 매도 주문을 실행하고 포지션을 청산한다.

    실행 순서:
    1. 안전장치 확인 — 감시 목록에도 없고 보유 기록에도 없으면 주문 거부
       (감시 목록에서 빠진 종목도 보유 기록이 있으면 청산 허용 — 보유 우선 원칙)
    2. 전량 매도 주문 실행
    3. held_stock_record.json에서 해당 종목 삭제
    4. 체결 원장(trade_ledger)에 기록 (수익률 포함)
    5. 텔레그램으로 알림 발송

    Args:
        code:          종목코드 6자리
        qty:           매도 수량 (잔고 조회의 sellable_qty 사용)
        reason:        청산 이유 (예: "2N 하드 손절", "10일 신저가 경신 익절")
        current_price: 현재가 (수익률 계산용, 0이면 last_buy_price로 근사)
    """
    # ① 안전장치: 감시 목록에도 없고 보유 기록에도 없는 종목은 매도 주문 거부
    #    감시 목록에서 빠진 종목도 held_stock_record에 있으면 청산 허용 (보유 우선 원칙)
    watchlist = get_watchlist()
    if code not in watchlist:
        held = load_position_state()
        if code not in held:
            print(f"[risk_guardian] {code} 감시 종목 외 + 보유 기록 없음 → 매도 주문 거부")
            return

    # 종목명: watchlist → held_stock_record 순으로 조회
    name = _get_stock_name(code)
    before_qty = ls_client.get_holding_qty(code)

    if qty <= 0:
        print(f"[risk_guardian] {name}({code}) 매도 가능 수량=0 → 스킵")
        return

    # ② 전량 매도 주문 실행
    result = ls_client.place_order(code, qty, "SELL", "MARKET")
    if not result["success"]:
        print(
            f"[risk_guardian] 청산 주문 실패: {name}({code}) {qty}주 "
            f"| 사유: {reason} | 오류: {result['message']}"
        )
        return

    fill = ls_client.wait_for_order_fill(code, "SELL", before_qty, qty)
    if not fill["filled"]:
        print(
            f"[risk_guardian] 청산 체결 미확정 → 기록/알림 스킵: {name}({code}) "
            f"(요청 {qty}주, 확인 {fill['filled_qty']}주)"
        )
        return

    order_no = result["order_no"]

    # ③ held_stock_record.json 내용에서 해당 종목 삭제 (포지션 청산 완료)
    position_state = load_position_state()
    removed_pos    = position_state.pop(code, {})
    save_position_state(position_state)

    # held_stock_record에 저장된 종목명으로 이름을 보완 (watchlist 날짜 불일치 대비)
    if name == code:
        name = removed_pos.get("stock_name", code)

    # ④ 체결 원장 기록
    avg_buy_price  = removed_pos.get("avg_buy_price", 0)
    last_buy_price = removed_pos.get("last_buy_price", 0)

    # 매도가: current_price가 있으면 사용, 없으면 last_buy_price로 근사
    sell_price = current_price if current_price > 0 else last_buy_price

    # 수익률 계산: (매도가 - 평균매입가) / 평균매입가 × 100
    if avg_buy_price > 0 and sell_price > 0:
        profit_rate = round((sell_price - avg_buy_price) / avg_buy_price * 100, 2)
    else:
        profit_rate = 0.0

    # 수익금 계산: (매도가 - 평균매입가) × 수량
    profit_amount = int((sell_price - avg_buy_price) * qty) if avg_buy_price > 0 else 0

    # 청산 사유(reason)에 따라 매매구분(source) 세분화
    exit_source_map = {
        "2N 하드 손절":       "EXIT_STOP",    # 손절
        "10일 신저가 경신 익절": "EXIT_10LOW", # 10일 신저가 익절
        "5MA 하향 돌파 익절":  "EXIT_5MA",    # 5MA 익절
    }
    exit_source = exit_source_map.get(reason, "EXIT_STOP")

    trade_ledger.append_trade({
        "side":          "SELL",
        "stock_code":    code,
        "stock_name":    name,
        "qty":           qty,
        "unit_price":    sell_price,
        "order_no":      order_no,
        "order_type":    "MARKET",
        "source":        exit_source,
        "profit_rate":   profit_rate,    # 수익률 (%)
        "profit_amount": profit_amount,  # 수익금 (원) — (매도가 - 평균매입가) × 수량
        "note":          reason,
    })

    # ⑤ 텔레그램 알림 (손절과 익절 이모지 구분 — 실제 수익률 기준)
    is_loss = profit_rate < 0
    emoji = "🔴" if is_loss else "💰"
    profit_sign  = "+" if profit_rate >= 0 else ""
    amount_sign  = "+" if profit_amount >= 0 else ""

    # 실수령금액 계산: 거래금액 - (위탁수수료 + 거래세)
    gross_amount = sell_price * qty
    fee_total    = int(gross_amount * (trade_ledger.LS_BROKER_FEE_RATE + trade_ledger.SELL_TAX_RATE))
    net_amount   = gross_amount - fee_total

    telegram_alert.SendMessage(
        f"{emoji} 포지션 청산\n"
        f"종목: {name}({code})\n"
        f"수량: {qty:,}주\n"
        f"평균 매입가: {avg_buy_price:,}원 → 매도가: {sell_price:,}원\n"
        f"수익률: {profit_sign}{profit_rate:.2f}%\n"
        f"수익금: {amount_sign}{profit_amount:,}원\n"
        f"실수령금액: {net_amount:,}원\n"
        f"사유: {reason}"
    )


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_guardian(balance: Optional[list] = None) -> Set[str]:
    """전체 보유 종목의 손절·익절 조건을 감시하고 청산을 실행한다 (메인 실행 함수).

    실행 순서:
    1. 잔고 조회로 현재 보유 종목 파악
    2. held_stock_record.json으로 손절/익절 기준가 파악
    3. 각 종목마다 순서대로 확인:
       ① 하드 손절 조건 (최우선)
       ② 트레일링 스탑 조건 (차순위)
    4. 조건 충족 시 즉시 전량 매도

    주의: run_all.py에서 가장 먼저 실행한다 (기존 포지션 보호가 최우선).

    Args:
        balance: 이미 조회된 잔고 리스트.
                 None이면 내부에서 ls_client.get_balance()를 호출한다.

    Returns:
        감시 종료 시점 기준 보유 종목 코드 집합.
        (손절/익절로 청산된 종목은 제외됨)
    """
    print("[risk_guardian] 손절·익절 감시 시작")

    # ① 현재 잔고 조회 (실제 보유 수량 기준)
    if balance is None:
        try:
            balance = ls_client.get_balance()
        except Exception as e:
            print(f"[risk_guardian] 잔고 조회 오류: {e}")
            return set()
    else:
        print("[risk_guardian] 전달받은 잔고로 감시 진행")

    if not balance:
        print("[risk_guardian] 보유 종목 없음")
        return set()

    # ② held_stock_record 조회 (손절가·평균단가 등 기준값)
    position_state = load_position_state()
    held_codes_after_guard = {
        item["code"] for item in balance if int(item.get("sellable_qty", 0)) > 0
    }

    # ③ 각 보유 종목 순회
    for item in balance:
        code         = item["code"]
        current_price = int(item["current_price"])
        sellable_qty  = int(item["sellable_qty"])

        watchlist = get_watchlist()

        # held_stock_record에 없으면 수동 보유 종목으로 판단 → 스킵
        # 감시 목록에 있더라도 보유 기록이 없으면 기준값을 알 수 없으므로 감시 불가
        if code not in position_state:
            name = watchlist.get(code, {}).get("name", code)
            print(f"[risk_guardian] {name}({code}) held_stock_record.json에 기록 없음 "
                  f"→ 수동 보유 종목으로 판단, 자동 청산 스킵")
            continue

        # held_stock_record에 있으면 감시 목록 여부와 무관하게 손절·익절 감시 계속
        # (09:05 이후 목록에서 빠진 종목도 보유 중이면 보호한다)
        pos  = position_state[code]
        # 종목명 우선순위: 잔고 응답(name) → watchlist → held_stock_record → code
        name = str(item.get("name") or "").strip() or _get_stock_name(code)

        if code not in watchlist:
            print(f"[risk_guardian] {name}({code}) ⚠️ 감시 목록에서 제외됐지만 보유 중 "
                  f"→ 손절·익절 감시 계속")

        # 평균가/피라미딩가/수익률/수익금을 함께 표시 (한눈에 포지션 상태 파악)
        avg_buy_price       = pos.get("avg_buy_price", 0)
        next_pyramid_price  = pos.get("next_pyramid_price", 0)
        stop_loss_price_val = pos.get("stop_loss_price", 0)
        # 보유 수량: 잔고의 qty(전체 보유) 사용, 없으면 sellable_qty로 폴백
        holding_qty = int(item.get("qty", sellable_qty))

        # 지표 계산 (10일 신저가·5MA) — 매도 기준 비교 + 트레일링 스탑 체크에 모두 사용
        # API 속도 제한 방지를 위해 종목당 1초 대기
        try:
            time.sleep(1.0)
            indicators = indicator_calc.get_all_indicators(code)
        except Exception as e:
            print(f"[risk_guardian] {code} 지표 계산 오류: {e}")
            indicators = {}

        day10_low = indicators.get("day10_low", 0)
        ma5       = indicators.get("ma5", 0.0)

        # 매도 기준 후보 3가지를 모은 뒤 "가장 높은 가격"을 가장 먼저 닿는 매도 신호로 표시한다
        # ① 2N 손절: stop_loss_price (손실·수익 무관 적용)
        # ② 10일 신저가: day10_low (손실·수익 무관 적용)
        # ③ 5MA: ma5 (수익권 = 현재가 > 평균가일 때만 매도 신호로 작동)
        sell_trigger_candidates = []
        if stop_loss_price_val > 0:
            sell_trigger_candidates.append((int(stop_loss_price_val), "2N 손절"))
        if day10_low > 0:
            sell_trigger_candidates.append((int(day10_low), "10일 신저가"))
        if ma5 > 0 and avg_buy_price > 0 and current_price > avg_buy_price:
            sell_trigger_candidates.append((int(ma5), "5MA"))

        if sell_trigger_candidates:
            trigger_price, trigger_name = max(sell_trigger_candidates, key=lambda x: x[0])
            sell_trigger_str = f"{trigger_price:,}원 ({trigger_name})"
        else:
            sell_trigger_str = "N/A"

        # 수익률·수익금: 평균가가 있을 때만 계산, 없으면 'N/A'
        if avg_buy_price > 0:
            profit_pct    = (current_price - avg_buy_price) / avg_buy_price * 100
            profit_amount = int((current_price - avg_buy_price) * holding_qty)
            profit_sign   = "+" if profit_pct >= 0 else ""
            amount_sign   = "+" if profit_amount >= 0 else ""
            profit_str    = f"({profit_sign}{profit_pct:.2f}%, {amount_sign}{profit_amount:,}원)"
        else:
            profit_str    = "(N/A)"

        avg_str     = f"{avg_buy_price:,}원" if avg_buy_price > 0 else "N/A"
        pyramid_str = f"{next_pyramid_price:,}원" if next_pyramid_price > 0 else "N/A"

        print(f"[risk_guardian] {name}({code}) 감시 중 — "
              f"현재가: {current_price:,}원 {profit_str} "
              f"| 평균가: {avg_str} "
              f"| 매도기준: {sell_trigger_str} "
              f"| 피라미딩가: {pyramid_str}")

        # ④ 하드 손절 먼저 확인 (최우선)
        if check_hard_stop(code, current_price, pos):
            place_exit_order(code, sellable_qty, "2N 하드 손절", current_price)
            held_codes_after_guard.discard(code)
            continue  # 이미 청산됐으므로 트레일링 스탑은 확인 불필요

        # ⑤ 트레일링 스탑 확인 (위에서 이미 계산한 indicators 재사용)
        if indicators:
            try:
                exit_reason = check_trailing_stop(code, current_price, pos, indicators)
                if exit_reason:
                    place_exit_order(code, sellable_qty, exit_reason, current_price)
                    held_codes_after_guard.discard(code)
            except Exception as e:
                print(f"[risk_guardian] {code} 트레일링 스탑 확인 오류: {e}")

    print("[risk_guardian] 손절·익절 감시 완료")
    return held_codes_after_guard
