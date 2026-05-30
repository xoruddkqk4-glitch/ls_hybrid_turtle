# balance_sync.py
# 잔고 동기화 모듈
#
# 역할:
#   run_all.py 실행 시 가장 먼저 호출되어,
#   LS증권 실제 잔고와 held_stock_record.json을 비교하고 불일치를 수정한다.
#
#   수동 매매로 인해 시스템 기록과 실제 잔고가 어긋난 상태로
#   자동매매가 돌면 잘못된 손절 주문, 중복 매수 등 심각한 문제가 생길 수 있다.
#
# 동기화 규칙:
#   ① 기록엔 있는데 실제로 없는 종목  → 기록에서 삭제 + 텔레그램 알림
#   ② 실제로 있는데 기록에 없는 종목  → 최초 1회 알림 + held_stock_record에 수동 편입 (매도 전략만 감시)
#   ③ 둘 다 있는데 수량이 다른 종목   → 실제 수량으로 기록 수정 + 텔레그램 알림
#
# 사용법:
#   import balance_sync
#   ok = balance_sync.run_balance_sync()

import json
import os
import time
from typing import Optional

import indicator_calc
import ls_client
import trade_ledger
from telegram_alert import SendMessage

# held_stock_record.json 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR = os.path.dirname(os.path.abspath(__file__))
_HELD_FILE = os.path.join(_DIR, "held_stock_record.json")


# ─────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────

def _load_held() -> dict:
    """held_stock_record.json을 읽어서 반환한다.

    파일이 없거나 손상된 경우 빈 딕셔너리를 반환한다.
    """
    if os.path.exists(_HELD_FILE):
        try:
            with open(_HELD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, IOError):
            print(f"[balance_sync] {_HELD_FILE} 읽기 오류 → 빈 상태로 처리")
    return {}


def _save_held(state: dict):
    """포지션 상태를 held_stock_record.json에 저장한다."""
    try:
        with open(_HELD_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[balance_sync] held_stock_record.json 저장 오류: {e}")


# ─────────────────────────────────────────
# 수동 체결 원장 기록
# ─────────────────────────────────────────

def _record_manual_executions(code: str, name: str, avg_buy_price: float = 0):
    """잔고가 안 맞는 종목의 '당일 체결'을 조회해, 원장에 없는 것만 기록한다.

    수동으로 사고판(HTS·MTS) 체결을 체결 원장(구글시트)에 남기기 위한 함수.

    동작:
        ① ls_client.get_today_executions(code)로 그 종목의 당일 체결을 가져온다.
        ② 체결 하나하나에 대해, 같은 주문번호가 이미 원장에 있으면 건너뛴다.
           (봇이 직접 낸 주문·이전 실행에서 이미 기록한 수동 체결을 중복 방지)
        ③ 없는 체결만 MANUAL_SYNC로 원장에 기록한다.
           매도(SELL)이고 평균매입가를 알면 수익률·수익금도 함께 계산한다.

    Args:
        code:          종목코드 6자리
        name:          종목명 (체결 조회 API가 이름을 안 주므로 외부에서 전달)
        avg_buy_price: 평균 매입가 (매도 수익 계산용). 0이면 수익 계산 생략.

    이 함수는 조회·기록만 하며 어떤 주문도 내지 않는다.
    오류가 나도 동기화 전체가 멈추지 않도록 예외를 안에서 삼킨다.
    """
    try:
        executions = ls_client.get_today_executions(code)
    except Exception as e:
        print(f"[balance_sync] {name}({code}) 당일 체결 조회 실패: {e} → 원장 기록 건너뜀")
        return

    recorded = 0  # 이번에 새로 기록한 체결 수
    for ex in executions:
        order_no = ex.get("order_no", "")

        # 이미 기록된 주문이면 건너뜀 (봇 주문·중복 실행 방지)
        if trade_ledger.order_already_recorded(code, order_no):
            continue

        side  = ex.get("side", "BUY")
        qty   = int(ex.get("qty", 0))
        price = int(ex.get("price", 0))
        if qty <= 0 or price <= 0:
            continue  # 비정상 데이터 방어

        # 원장 기록용 record 구성 (trade_ledger가 record_id·수수료 등 자동 채움)
        record = {
            "side":       side,
            "stock_code": code,
            "stock_name": name,
            "qty":        qty,
            "unit_price": price,
            "order_no":   order_no,
            "order_type": "MANUAL",       # 수동 매매 표시
            "source":     "MANUAL_SYNC",  # 수동 동기화 구분값
            "note":       "수동 매매 자동 기록",
        }

        # 매도이고 평균매입가를 알면 수익률·수익금 계산
        if side == "SELL" and avg_buy_price and avg_buy_price > 0:
            record["profit_amount"] = int((price - avg_buy_price) * qty)
            record["profit_rate"]   = round((price - avg_buy_price) / avg_buy_price * 100, 2)

        trade_ledger.append_trade(record)
        recorded += 1

    if recorded:
        print(f"[balance_sync] {name}({code}) 수동 체결 {recorded}건을 원장에 기록했습니다.")


# ─────────────────────────────────────────
# 메인 함수
# ─────────────────────────────────────────

def run_balance_sync(actual_list: Optional[list] = None) -> bool:
    """실제 잔고와 held_stock_record.json을 비교해서 불일치를 수정한다.

    Args:
        actual_list: 이미 조회된 잔고 리스트.
                     None이면 내부에서 ls_client.get_balance()를 호출한다.

    Returns:
        True  — 동기화 성공 (불일치가 없거나 수정 완료)
        False — 잔고 조회 실패 (API 오류 → 실행 중단 신호)
    """
    if actual_list is None:
        print("[balance_sync] 실제 잔고 조회 중...")
        # ① 실제 잔고 조회
        actual_list = ls_client.get_balance()
    else:
        print("[balance_sync] 전달받은 잔고로 동기화 진행")

    # get_balance()가 빈 리스트를 반환하면 두 가지 경우:
    #   - API 오류 (토큰 만료, 네트워크 등)
    #   - 진짜로 보유 종목이 하나도 없음
    # 구분을 위해: held_stock_record에 종목이 있는데 잔고가 빈 경우만 오류로 판단
    held = _load_held()

    if not actual_list and held:
        # 기록엔 종목이 있는데 잔고 조회 결과가 빈 리스트 → API 오류 가능성
        print("[balance_sync] ⚠️ 잔고 조회 실패 (빈 응답). 자동매매 중단.")
        return False

    # 실제 잔고를 {종목코드: {name, qty, avg_price}} 형태의 딕셔너리로 변환
    # avg_price는 수동 편입 시 평균 매입가 계산에 사용
    actual = {
        item["code"]: {
            "name":      item["name"],
            "qty":       item["qty"],
            "avg_price": item.get("avg_price", 0),
        }
        for item in actual_list
    }

    print(f"[balance_sync] 실제 보유: {len(actual)}종목 / 기록 보유: {len(held)}종목")

    changed = False  # 변경사항 발생 여부

    # 잔고가 안 맞는 종목 → 나중에 당일 체결을 조회해 원장에 기록할 대상
    # (code, name, avg_buy_price) 형태. 평균매입가는 매도 수익 계산용.
    to_record = []

    # ─────────────────────────────────────
    # ① 기록엔 있는데 실제로 없는 종목 → 기록 삭제
    # ─────────────────────────────────────
    to_remove = [code for code in held if code not in actual]
    for code in to_remove:
        msg = (
            f"⚠️ [잔고동기화] {code} — 실제 잔고 없음. "
            f"수동 매도 또는 청산된 것으로 판단해 기록을 삭제합니다."
        )
        print(f"[balance_sync] {msg}")
        SendMessage(msg)
        # 기록 삭제 전에 종목명·평균매입가를 확보 (수동 매도 수익 계산용)
        to_record.append((
            code,
            held[code].get("stock_name", code),
            held[code].get("avg_buy_price", 0),
        ))
        del held[code]
        changed = True

    # ─────────────────────────────────────
    # ② 실제로 있는데 기록에 없는 종목 → 자동 편입 (1회 알림 + 매도 전략 감시 등록)
    # ─────────────────────────────────────
    for code, info in actual.items():
        if code not in held:
            name      = info["name"]
            qty       = info["qty"]
            avg_price = int(info.get("avg_price", 0))  # 평균 매입가 (get_balance() 제공)

            # ATR 지표 조회 — 손절가 계산용
            # 조회 실패하거나 ATR=0이면 stop_loss_price=0 으로 등록 (하드 손절 비적용)
            atr_n            = 0.0
            stop_loss_price  = 0
            try:
                time.sleep(2)  # API 호출 제한 방지
                indicators      = indicator_calc.get_all_indicators(code)
                atr_n           = indicators.get("atr", 0.0)
                if atr_n > 0 and avg_price > 0:
                    # 2N 손절가: 평균 매입가 - 2 × ATR
                    stop_loss_price = int(avg_price - 2 * atr_n)
            except Exception as e:
                print(f"[balance_sync] {name}({code}) 지표 조회 실패: {e} → 손절가 미설정")

            # held_stock_record.json에 수동 편입 등록
            # current_unit = max_unit = 1 → check_pyramid_trigger에서 추가 매수 자동 차단
            held[code] = {
                "stock_name":            name,
                "current_unit":          1,
                "last_buy_price":        avg_price,
                "avg_buy_price":         avg_price,
                "stop_loss_price":       stop_loss_price,
                "next_pyramid_price":    0,      # 피라미딩 없음
                "entry_type":            "MANUAL",
                "max_unit":              1,      # 추가 매수 상한 = 1 (현재와 같음 → 피라미딩 불가)
                "total_qty":             qty,
                "source":                "MANUAL_SYNC",  # 수동 편입 표시
                "effective_risk_factor": None,   # 수동 편입은 리스크팩터 없음 (피라미딩 불가)
                "high_since_entry":      avg_price,  # 평균매입가를 최고가 시작점으로 (트레일링 손절 기준)
            }
            changed = True

            # 수동 매수 종목 → 당일 매수 체결을 원장에 기록 (매수라 수익 계산 불필요 → avg=0)
            to_record.append((code, name, 0))

            # 알림은 최초 편입 시 1회만 전송 (다음 실행부터는 held에 있으므로 이 블록 진입 안 함)
            stop_msg = (
                f" | 손절가: {stop_loss_price:,}원" if stop_loss_price > 0
                else " | 손절가: 미설정(ATR 조회 실패)"
            )
            msg = (
                f"✅ [수동편입] {name}({code}) {qty:,}주 — "
                f"수동 매수 종목으로 자동 편입됨.\n"
                f"평균 매입가: {avg_price:,}원{stop_msg}\n"
                f"추가 매수(피라미딩) 없이 매도 전략(2N 손절·트레일링 스탑)만 감시합니다."
            )
            print(f"[balance_sync] {msg}")
            SendMessage(msg)

    # ─────────────────────────────────────
    # ③ 둘 다 있는데 수량이 다른 종목 → 실제 수량으로 수정
    # ─────────────────────────────────────
    for code in held:
        if code not in actual:
            continue  # 이미 ①에서 처리됨
        actual_qty = actual[code]["qty"]
        record_qty = held[code].get("total_qty", 0)
        if actual_qty != record_qty:
            msg = (
                f"⚠️ [잔고동기화] {actual[code]['name']}({code}) 수량 불일치: "
                f"기록 {record_qty}주 → 실제 {actual_qty}주로 수정합니다."
            )
            print(f"[balance_sync] {msg}")
            SendMessage(msg)
            # 수량이 바뀐 종목 → 당일 체결을 원장에 기록 (매도분이면 평균매입가로 수익 계산)
            to_record.append((code, actual[code]["name"], held[code].get("avg_buy_price", 0)))
            held[code]["total_qty"] = actual_qty
            changed = True

    # ─────────────────────────────────────
    # 변경사항이 있으면 파일 저장
    # ─────────────────────────────────────
    if changed:
        _save_held(held)
        print("[balance_sync] held_stock_record.json 저장 완료.")
    else:
        print("[balance_sync] 불일치 없음. 동기화 완료.")

    # ─────────────────────────────────────
    # 잔고가 안 맞았던 종목들의 당일 체결을 원장(구글시트)에 기록
    # (수동 매수·매도 체결을 남기기 위함. 이미 기록된 주문은 자동으로 건너뜀)
    # ─────────────────────────────────────
    for code, name, avg_buy_price in to_record:
        _record_manual_executions(code, name, avg_buy_price)

    return True
