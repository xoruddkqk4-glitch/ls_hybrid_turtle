# ls_client.py
# LS증권 API 전담 래퍼 모듈
#
# 규칙: 다른 전략 파일들은 LS증권 API를 직접 호출하지 말고,
#       반드시 이 파일(ls_client.py)의 함수를 통해서만 접근한다.
#
# 사용법:
#   import ls_client
#   ls_client.login()
#   prices = ls_client.get_multi_price(["005930", "034020"])

import os
from dotenv import load_dotenv
from programgarden_finance.ls import LS
from programgarden_finance.ls.korea_stock.market.t8407.blocks import T8407InBlock
from programgarden_finance.ls.korea_stock.chart.t8451.blocks import T8451InBlock
from programgarden_finance.ls.korea_stock.chart.t8452.blocks import T8452InBlock
from programgarden_finance.ls.korea_stock.order.CSPAT00601.blocks import CSPAT00601InBlock1
from programgarden_finance.ls.korea_stock.accno.CSPAQ12300.blocks import CSPAQ12300InBlock1

load_dotenv()

# 모듈 내부에서 사용하는 LS 인스턴스와 모의투자 여부 저장
_ls = None
_paper_trading = True  # 기본값: 모의투자


# ─────────────────────────────────────────
# 로그인
# ─────────────────────────────────────────

def login() -> bool:
    """LS증권에 로그인한다.

    .env 파일의 LS_PAPER_TRADING 값으로 모의투자/실계좌를 결정한다.
    LS_PAPER_TRADING=True  → 모의투자 (주문이 실제로 체결되지 않음, 안전)
    LS_PAPER_TRADING=False → 실계좌 (주문이 실제로 체결됨, 주의!)

    Returns:
        True: 로그인 성공
        False: 로그인 실패
    """
    global _ls, _paper_trading

    appkey       = os.getenv("LS_APP_KEY", "")
    appsecretkey = os.getenv("LS_APP_SECRET_KEY", "")
    paper_env    = os.getenv("LS_PAPER_TRADING", "True")

    # 문자열 "True"/"False"를 불리언(참/거짓)으로 변환
    _paper_trading = paper_env.strip().lower() == "true"

    if not appkey or not appsecretkey:
        print("[ls_client] 오류: .env 파일에 LS_APP_KEY 또는 LS_APP_SECRET_KEY가 없습니다.")
        return False

    try:
        _ls = LS()
        result = _ls.login(
            appkey=appkey,
            appsecretkey=appsecretkey,
            paper_trading=_paper_trading,
        )
        mode = "모의투자" if _paper_trading else "실계좌"
        print(f"[ls_client] 로그인 {'성공' if result else '실패'} ({mode})")
        return result
    except Exception as e:
        print(f"[ls_client] 로그인 오류: {e}")
        return False


def _check_login():
    """로그인 여부를 확인하고 로그인되지 않았으면 예외를 발생시킨다."""
    if _ls is None:
        raise RuntimeError("[ls_client] 로그인 먼저 하세요: ls_client.login()")


# ─────────────────────────────────────────
# 시세 조회
# ─────────────────────────────────────────

def get_multi_price(codes: list) -> dict:
    """여러 종목의 현재가를 한 번에 조회한다 (t8407 멀티현재가).

    7개 관심 종목을 API 1번 호출로 모두 조회할 수 있어 효율적이다.

    Args:
        codes: 종목코드 리스트 (예: ["005930", "034020", "064350"])

    Returns:
        종목코드 → 현재가 딕셔너리 (예: {"005930": 75000, "034020": 12000})
        조회 실패 종목은 결과에서 제외된다.
    """
    _check_login()

    if not codes:
        return {}

    try:
        # 종목코드들을 공백 없이 붙여서 전달 (LS API t8407 방식)
        # 예: ["005930", "034020"] → "005930034020"
        shcode = "".join(codes)
        nrec   = len(codes)

        response = _ls.korea_stock().market().t8407(
            T8407InBlock(nrec=nrec, shcode=shcode)
        ).req()

        result = {}
        for item in response.block:
            code = item.shcode.strip()
            if code:
                result[code] = item.price
        return result

    except Exception as e:
        print(f"[ls_client] 멀티현재가 조회 오류: {e}")
        return {}


# ─────────────────────────────────────────
# 차트 조회
# ─────────────────────────────────────────

def get_daily_chart(code: str, count: int = 25) -> list:
    """일봉 OHLCV 데이터를 조회한다 (t8451).

    ATR(N), 이동평균선(5MA/20MA), 10일 신저가 계산에 사용한다.
    최소 21개(20일 ATR + 1일 여유) 이상 요청을 권장한다.

    Args:
        code:  종목코드 6자리 (예: "005930")
        count: 요청 건수 (기본 25개, 최대 500)

    Returns:
        날짜 오름차순(오래된 것 먼저) 정렬된 OHLCV 딕셔너리 리스트
        [{"date": "20260413", "open": 74000, "high": 75500,
          "low": 73500, "close": 75000, "volume": 123456}, ...]
    """
    _check_login()

    try:
        response = _ls.korea_stock().chart().t8451(
            T8451InBlock(
                shcode=code,
                gubun="2",      # 2 = 일봉
                qrycnt=count,
                sujung="Y",     # 수정주가 적용 (액면분할 등 반영)
            )
        ).req()

        # 날짜 기준 오름차순(오래된 것 → 최신 순)으로 정렬
        items = sorted(response.block1, key=lambda x: x.date)
        result = []
        for item in items:
            result.append({
                "date":   item.date,
                "open":   item.open,
                "high":   item.high,
                "low":    item.low,
                "close":  item.close,
                "volume": item.jdiff_vol,
            })
        return result

    except Exception as e:
        print(f"[ls_client] 일봉 차트 조회 오류 ({code}): {e}")
        return []


def get_minute_chart(code: str, minute: int = 240, count: int = 25) -> list:
    """분봉 OHLCV 데이터를 조회한다 (t8452).

    240분봉(4시간봉) 20MA 계산에 사용한다. 기본값은 240분봉.

    Args:
        code:   종목코드 6자리
        minute: 분봉 단위 (기본 240 = 4시간봉)
        count:  요청 건수 (기본 25개)

    Returns:
        날짜+시간 오름차순 정렬된 OHLCV 딕셔너리 리스트
        [{"date": "20260413", "time": "093000", "open": 74000,
          "high": 75500, "low": 73500, "close": 75000, "volume": 12345}, ...]
    """
    _check_login()

    try:
        response = _ls.korea_stock().chart().t8452(
            T8452InBlock(
                shcode=code,
                ncnt=minute,    # 분 단위 (240 = 4시간봉)
                qrycnt=count,
            )
        ).req()

        # 날짜+시간 기준 오름차순 정렬
        items = sorted(response.block, key=lambda x: (x.date, x.time))
        result = []
        for item in items:
            result.append({
                "date":   item.date,
                "time":   item.time,
                "open":   item.open,
                "high":   item.high,
                "low":    item.low,
                "close":  item.close,
                "volume": item.jdiff_vol,
            })
        return result

    except Exception as e:
        print(f"[ls_client] 분봉 차트 조회 오류 ({code}, {minute}분): {e}")
        return []


# ─────────────────────────────────────────
# 계좌 조회
# ─────────────────────────────────────────

def get_total_capital() -> int:
    """계좌의 예탁자산총액(총 자본)을 조회한다 (CSPAQ12300).

    터틀 트레이딩의 Unit 수량 계산 시 '총 자본' 값으로 사용한다.
    예탁자산총액 = 보유주식 평가금액 + 예수금(현금)

    Returns:
        예탁자산총액 (원 단위 정수). 조회 실패 시 0.
    """
    _check_login()

    try:
        response = _ls.korea_stock().accno().cspaq12300(
            CSPAQ12300InBlock1()
        ).req()

        if response.block2:
            return response.block2.DpsastTotamt
        return 0

    except Exception as e:
        print(f"[ls_client] 총자본 조회 오류: {e}")
        return 0


def get_balance() -> list:
    """보유 종목별 잔고를 조회한다 (CSPAQ12300).

    risk_guardian.py에서 보유 종목 현황 확인에 사용한다.

    Returns:
        보유 종목 리스트
        [{"code": "005930", "name": "삼성전자",
          "qty": 10, "avg_price": 75000.0,
          "current_price": 76000.0, "sellable_qty": 10}, ...]
    """
    _check_login()

    try:
        response = _ls.korea_stock().accno().cspaq12300(
            CSPAQ12300InBlock1()
        ).req()

        result = []
        for item in response.block3:
            # 종목코드 정규화: 모의투자 응답에서 "A005930" 형식으로 올 수 있음
            code = item.IsuNo.strip()
            if code.startswith("A"):
                code = code[1:]  # 앞의 "A" 제거

            result.append({
                "code":          code,
                "name":          item.IsuNm.strip(),
                "qty":           item.BalQty,
                "avg_price":     float(item.AvrUprc),
                "current_price": float(item.NowPrc),
                "sellable_qty":  item.SellAbleQty,
            })
        return result

    except Exception as e:
        print(f"[ls_client] 잔고 조회 오류: {e}")
        return []


# ─────────────────────────────────────────
# 주문
# ─────────────────────────────────────────

def place_order(code: str, qty: int, side: str, order_type: str = "MARKET") -> dict:
    """매수 또는 매도 주문을 실행한다 (CSPAT00601).

    모의투자 모드에서는 종목코드 앞에 'A'를 자동으로 붙인다.
    실계좌 모드에서는 종목코드를 그대로 사용한다.

    Args:
        code:       종목코드 6자리 (예: "005930")
        qty:        주문 수량 (주 단위 정수)
        side:       "BUY" (매수) 또는 "SELL" (매도)
        order_type: "MARKET" (시장가, 기본값) 또는 "LIMIT" (지정가 - 미구현)

    Returns:
        {"success": True,  "order_no": "12345", "message": "매수주문완료"}
        {"success": False, "order_no": "",       "message": "오류 메시지"}
    """
    _check_login()

    if qty <= 0:
        return {"success": False, "order_no": "", "message": "주문 수량은 1 이상이어야 합니다."}

    try:
        # 모의투자: "A" + 종목코드 / 실계좌: 종목코드 그대로
        isu_no = f"A{code}" if _paper_trading else code

        # 매매구분: "2" = 매수, "1" = 매도
        bns_tp = "2" if side == "BUY" else "1"

        # 호가유형: "03" = 시장가
        ord_prc_ptn = "03"

        response = _ls.korea_stock().order().cspat00601(
            CSPAT00601InBlock1(
                IsuNo=isu_no,
                OrdQty=qty,
                BnsTpCode=bns_tp,
                OrdprcPtnCode=ord_prc_ptn,
                OrdPrc=0,  # 시장가 주문 시 가격은 0
            )
        ).req()

        # 응답코드: "00040" = 매수주문완료, "00039" = 매도주문완료
        if response.rsp_cd in ("00040", "00039"):
            order_no = str(response.block2.OrdNo) if response.block2 else ""
            mode_str = "모의" if _paper_trading else "실계좌"
            print(f"[ls_client] {mode_str} {side} 주문 성공: {code} {qty}주 (주문번호: {order_no})")
            return {"success": True, "order_no": order_no, "message": response.rsp_msg}
        else:
            print(f"[ls_client] 주문 실패: {code} {side} {qty}주 → {response.rsp_cd}: {response.rsp_msg}")
            return {"success": False, "order_no": "", "message": response.rsp_msg}

    except Exception as e:
        print(f"[ls_client] 주문 오류 ({code} {side} {qty}주): {e}")
        return {"success": False, "order_no": "", "message": str(e)}


def is_paper_trading() -> bool:
    """현재 모의투자 모드 여부를 반환한다."""
    return _paper_trading
