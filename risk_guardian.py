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

import ls_client
import indicator_calc
import trade_ledger
import telegram_alert
from config import lovely_stock_list
from turtle_order_logic import load_position_state, save_position_state


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
        name = lovely_stock_list.get(code, {}).get("name", code)
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
    name         = lovely_stock_list.get(code, {}).get("name", code)
    day10_low    = indicators.get("day10_low", 0)
    ma5          = indicators.get("ma5",       0.0)
    avg_buy_price = pos.get("avg_buy_price",   0)

    # 조건 ①: 10일 신저가 경신 확인
    if day10_low > 0 and current_price <= day10_low:
        print(f"[risk_guardian] {name}({code}) 📉 10일 신저가 경신! "
              f"현재가 {current_price:,}원 ≤ 10일 신저가 {day10_low:,}원")
        return "10일 신저가 경신 익절"

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
    1. lovely_stock_list 포함 여부 확인 (안전장치)
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
    # ① 안전장치: lovely_stock_list에 없는 종목은 절대 주문하지 않음
    if code not in lovely_stock_list:
        print(f"[risk_guardian] {code} lovely_stock_list 외 종목 → 매도 주문 거부")
        return

    name = lovely_stock_list[code]["name"]

    if qty <= 0:
        print(f"[risk_guardian] {name}({code}) 매도 가능 수량=0 → 스킵")
        return

    # ② 전량 매도 주문 실행
    result = ls_client.place_order(code, qty, "SELL", "MARKET")
    if not result["success"]:
        msg = (f"⚠️ 청산 주문 실패\n"
               f"종목: {name}({code})\n"
               f"수량: {qty}주\n"
               f"사유: {reason}\n"
               f"오류: {result['message']}")
        print(f"[risk_guardian] {msg}")
        telegram_alert.SendMessage(msg)
        return

    order_no = result["order_no"]

    # ③ held_stock_record.json 내용에서 해당 종목 삭제 (포지션 청산 완료)
    position_state = load_position_state()
    removed_pos    = position_state.pop(code, {})
    save_position_state(position_state)

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

    # 매도 source: TURTLE_EXIT (손절/익절 구분은 note 필드로)
    trade_ledger.append_trade({
        "side":        "SELL",
        "stock_code":  code,
        "stock_name":  name,
        "qty":         qty,
        "unit_price":  sell_price,
        "order_no":    order_no,
        "order_type":  "MARKET",
        "source":      "TURTLE_EXIT",
        "profit_rate": profit_rate,     # 수익률 (%) — Google Sheets에 기록됨
        "note":        reason,
    })

    # ⑤ 텔레그램 알림 (손절과 익절 이모지 구분)
    is_stop_loss = "손절" in reason
    emoji = "🔴" if is_stop_loss else "💰"
    profit_sign  = "+" if profit_rate >= 0 else ""

    telegram_alert.SendMessage(
        f"{emoji} 포지션 청산\n"
        f"종목: {name}({code})\n"
        f"수량: {qty:,}주\n"
        f"평균 매입가: {avg_buy_price:,}원 → 매도가: {sell_price:,}원\n"
        f"수익률: {profit_sign}{profit_rate:.2f}%\n"
        f"사유: {reason}"
    )


# ─────────────────────────────────────────
# 메인 실행 함수
# ─────────────────────────────────────────

def run_guardian():
    """전체 보유 종목의 손절·익절 조건을 감시하고 청산을 실행한다 (메인 실행 함수).

    실행 순서:
    1. 잔고 조회로 현재 보유 종목 파악
    2. held_stock_record.json으로 손절/익절 기준가 파악
    3. 각 종목마다 순서대로 확인:
       ① 하드 손절 조건 (최우선)
       ② 트레일링 스탑 조건 (차순위)
    4. 조건 충족 시 즉시 전량 매도

    주의: run_all.py에서 가장 먼저 실행한다 (기존 포지션 보호가 최우선).
    """
    print("[risk_guardian] 손절·익절 감시 시작")

    # ① 현재 잔고 조회 (실제 보유 수량 기준)
    try:
        balance = ls_client.get_balance()
    except Exception as e:
        print(f"[risk_guardian] 잔고 조회 오류: {e}")
        return

    if not balance:
        print("[risk_guardian] 보유 종목 없음")
        return

    # ② held_stock_record 조회 (손절가·평균단가 등 기준값)
    position_state = load_position_state()

    # ③ 각 보유 종목 순회
    for item in balance:
        code         = item["code"]
        current_price = int(item["current_price"])
        sellable_qty  = item["sellable_qty"]

        # lovely_stock_list 외 종목은 감시하지 않음 (수동 보유 종목 등)
        if code not in lovely_stock_list:
            print(f"[risk_guardian] {code} lovely_stock_list 외 종목 → 감시 스킵")
            continue

        # held_stock_record에 기준값이 없으면 감시 불가
        if code not in position_state:
            name = lovely_stock_list.get(code, {}).get("name", code)
            print(f"[risk_guardian] {name}({code}) held_stock_record.json에 기록 없음 "
                  f"→ 수동 보유 종목으로 판단, 자동 청산 스킵")
            continue

        pos  = position_state[code]
        name = lovely_stock_list[code]["name"]

        print(f"[risk_guardian] {name}({code}) 감시 중 — 현재가: {current_price:,}원 "
              f"| 손절가: {pos.get('stop_loss_price', 0):,}원")

        # ④ 하드 손절 먼저 확인 (최우선)
        if check_hard_stop(code, current_price, pos):
            place_exit_order(code, sellable_qty, "2N 하드 손절", current_price)
            continue  # 이미 청산됐으므로 트레일링 스탑은 확인 불필요

        # ⑤ 트레일링 스탑 확인 (지표 계산 필요) — API 속도 제한 방지
        try:
            time.sleep(1.0)
            indicators   = indicator_calc.get_all_indicators(code)
            exit_reason  = check_trailing_stop(code, current_price, pos, indicators)
            if exit_reason:
                place_exit_order(code, sellable_qty, exit_reason, current_price)
        except Exception as e:
            print(f"[risk_guardian] {code} 지표 계산 오류: {e}")

    print("[risk_guardian] 손절·익절 감시 완료")
