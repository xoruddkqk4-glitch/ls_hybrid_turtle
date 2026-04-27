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
import time
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv
from programgarden_finance.ls import LS
from programgarden_finance.ls.korea_stock.market.t8407.blocks import T8407InBlock
from programgarden_finance.ls.korea_stock.chart.t8451.blocks import T8451InBlock
from programgarden_finance.ls.korea_stock.chart.t8452.blocks import T8452InBlock
from programgarden_finance.ls.korea_stock.order.CSPAT00601.blocks import CSPAT00601InBlock1
from programgarden_finance.ls.korea_stock.accno.t0424.blocks import T0424InBlock
from programgarden_finance.ls.korea_stock.sector.t1532.blocks import T1532InBlock
from programgarden_finance.ls.korea_stock.ranking.t1463.blocks import T1463InBlock
from programgarden_finance.ls.korea_stock.market.t1442.blocks import T1442InBlock

load_dotenv()

# 모듈 내부에서 사용하는 LS 인스턴스와 모의투자 여부 저장
_ls = None
_paper_trading = True  # 기본값: 모의투자

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")


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

    # 호출 제한(HTTP 500) 시 재시도 설정
    _MAX_RETRIES = 3    # 재시도 횟수
    _RETRY_WAIT  = 10.0 # 재시도 대기 시간 (초)

    for attempt in range(_MAX_RETRIES):
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
            err_str = str(e)
            # '호출 거래건수 초과(HTTP 500)' 오류이면 대기 후 재시도
            is_rate_limit = "500" in err_str or "호출 거래건수" in err_str
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                print(
                    f"[ls_client] t8451 호출 제한 오류 ({code}) → "
                    f"{_RETRY_WAIT:.0f}초 후 재시도 "
                    f"({attempt + 1}/{_MAX_RETRIES}): {err_str}"
                )
                time.sleep(_RETRY_WAIT)
            else:
                # 재시도 횟수 초과이거나 다른 종류의 오류
                print(f"[ls_client] 일봉 차트 조회 오류 ({code}): {e}")
                return []

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

    # 호출 제한(HTTP 500) 시 재시도 설정
    _MAX_RETRIES = 3    # 재시도 횟수
    _RETRY_WAIT  = 10.0 # 재시도 대기 시간 (초)

    # t8452는 sdate/edate 없이 호출하면 빈 결과를 반환하므로 명시적으로 지정
    # edate = 오늘, sdate = 오늘로부터 (count × minute / 390 + 5) 영업일 전
    # 390분(하루 장 시간) 기준으로 count개 캔들이 필요한 날수를 계산
    today     = datetime.now(_KST)
    days_back = max(int(count * minute / 390) + 10, 30)  # 여유있게 계산
    sdate     = (today - timedelta(days=days_back)).strftime("%Y%m%d")
    edate     = today.strftime("%Y%m%d")

    for attempt in range(_MAX_RETRIES):
        try:
            response = _ls.korea_stock().chart().t8452(
                T8452InBlock(
                    shcode=code,
                    ncnt=minute,    # 분 단위 (240 = 4시간봉)
                    qrycnt=count,
                    nday="0",       # 0: 날짜 범위 방식 사용
                    sdate=sdate,    # 시작일자 (YYYYMMDD)
                    edate=edate,    # 종료일자 (YYYYMMDD)
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
            err_str = str(e)
            # '호출 거래건수 초과(HTTP 500)' 오류이면 대기 후 재시도
            is_rate_limit = "500" in err_str or "호출 거래건수" in err_str
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                print(
                    f"[ls_client] t8452 호출 제한 오류 ({code}) → "
                    f"{_RETRY_WAIT:.0f}초 후 재시도 "
                    f"({attempt + 1}/{_MAX_RETRIES}): {err_str}"
                )
                time.sleep(_RETRY_WAIT)
            else:
                # 재시도 횟수 초과이거나 다른 종류의 오류
                print(f"[ls_client] 분봉 차트 조회 오류 ({code}, {minute}분): {e}")
                return []

    return []


# ─────────────────────────────────────────
# 계좌 조회
# ─────────────────────────────────────────

def get_total_capital() -> int:
    """계좌의 추정순자산(총 자본)을 조회한다 (t0424).

    터틀 트레이딩의 Unit 수량 계산 시 '총 자본' 값으로 사용한다.
    추정순자산 = 보유주식 평가금액 + 예수금(현금)

    Returns:
        추정순자산 (원 단위 정수). 조회 실패 시 0.
    """
    _check_login()

    try:
        response = _ls.korea_stock().accno().t0424(
            T0424InBlock(prcgb="1", chegb="2")
        ).req()

        if response.cont_block:
            return response.cont_block.sunamt
        return 0

    except Exception as e:
        print(f"[ls_client] 총자본 조회 오류: {e}")
        return 0


def get_balance() -> list:
    """보유 종목별 잔고를 조회한다 (t0424).

    risk_guardian.py에서 보유 종목 현황 확인에 사용한다.

    Returns:
        보유 종목 리스트
        [{"code": "005930", "name": "삼성전자",
          "qty": 10, "avg_price": 75000.0,
          "current_price": 76000.0, "sellable_qty": 10}, ...]
    """
    _check_login()

    try:
        response = _ls.korea_stock().accno().t0424(
            T0424InBlock(prcgb="1", chegb="2")
        ).req()

        result = []
        for item in response.block:
            # 종목코드 정규화: 모의투자 응답에서 "A005930" 형식으로 올 수 있음
            code = item.expcode.strip()
            if code.startswith("A"):
                code = code[1:]  # 앞의 "A" 제거

            result.append({
                "code":          code,
                "name":          item.hname.strip(),
                "qty":           item.janqty,
                "avg_price":     float(item.pamt),
                "current_price": float(item.price),
                "sellable_qty":  item.mdposqt,
            })
        return result

    except Exception as e:
        print(f"[ls_client] 잔고 조회 오류: {e}")
        return []


def get_holding_qty(code: str, balance: list = None) -> int:
    """특정 종목의 현재 보유 수량을 반환한다.

    Args:
        code:    종목코드 6자리
        balance: 미리 조회한 잔고 리스트(선택). None이면 내부에서 get_balance() 호출.

    Returns:
        해당 종목 보유 수량(정수). 없으면 0.
    """
    rows = balance if balance is not None else get_balance()
    for item in rows:
        if item.get("code") == code:
            return int(item.get("qty", 0))
    return 0


def wait_for_order_fill(
    code: str,
    side: str,
    before_qty: int,
    expected_qty: int,
    retries: int = 4,
    wait_sec: float = 1.0,
) -> dict:
    """주문 후 잔고 변화를 확인해 실제 체결 여부를 판단한다.

    BUY: after_qty - before_qty >= expected_qty 이면 체결로 간주
    SELL: before_qty - after_qty >= expected_qty 이면 체결로 간주

    Returns:
        {
            "filled": bool,       # 기대 수량 기준 체결 여부
            "after_qty": int,     # 확인 시점 최종 보유수량
            "filled_qty": int,    # 확인된 체결 수량 변화
            "partial": bool,      # 부분 체결 여부 (0 < filled_qty < expected_qty)
        }
    """
    _check_login()

    side_norm = side.upper()
    if side_norm not in ("BUY", "SELL"):
        return {"filled": False, "after_qty": before_qty, "filled_qty": 0, "partial": False}

    last_after = before_qty
    for i in range(max(retries, 1)):
        if i > 0:
            time.sleep(wait_sec)

        after_qty = get_holding_qty(code)
        last_after = after_qty

        if side_norm == "BUY":
            delta = max(after_qty - before_qty, 0)
        else:
            delta = max(before_qty - after_qty, 0)

        if delta >= expected_qty:
            return {"filled": True, "after_qty": after_qty, "filled_qty": delta, "partial": False}

    # 재시도 끝까지 기대 수량 체결이 확인되지 않은 경우
    if side_norm == "BUY":
        delta = max(last_after - before_qty, 0)
    else:
        delta = max(before_qty - last_after, 0)
    return {
        "filled": False,
        "after_qty": last_after,
        "filled_qty": delta,
        "partial": 0 < delta < expected_qty,
    }


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


def get_portfolio_summary() -> dict:
    """포트폴리오 전체 요약 정보를 조회한다 (t0424).

    record_daily_snapshot.py에서 일일 스냅샷 기록 시 사용한다.

    Returns:
        {
            "total_capital":    추정순자산 (주식+현금, 원),
            "stock_value":      주식평가액 (원),
            "cash":             예수금 (총자산 - 주식평가액, 원),
            "purchase_amount":  매입금액 (원),
            "unrealized_pnl":   평가손익 (원),
            "realized_pnl":     실현손익 (원),
            "holdings_count":   보유 종목 수,
            "holdings_names":   보유 종목명 (쉼표 구분 문자열),
        }
        조회 실패 시 빈 딕셔너리.
    """
    _check_login()

    try:
        response = _ls.korea_stock().accno().t0424(
            T0424InBlock(prcgb="1", chegb="2")
        ).req()

        if not response.cont_block:
            return {}

        cb = response.cont_block

        # 보유 종목명 + 수익률 목록 — 수익률 기준 내림차순 정렬
        items_with_rate = [
            (item.sunikrt, item.hname.strip())
            for item in response.block
            if item.hname.strip()
        ]
        items_with_rate.sort(key=lambda x: x[0], reverse=True)  # 수익률 높은 종목이 앞으로
        names = [f"{name}({rate:+.2f}%)" for rate, name in items_with_rate]

        return {
            "total_capital":   cb.sunamt,                  # 추정순자산
            "stock_value":     cb.tappamt,                 # 주식평가액
            "cash":            cb.sunamt - cb.tappamt,     # 예수금 (총자산 - 주식)
            "purchase_amount": cb.mamt,                    # 매입금액
            "unrealized_pnl":  cb.tdtsunik,                # 평가손익 (미실현)
            "realized_pnl":    cb.dtsunik,                 # 실현손익
            "holdings_count":  len(response.block),        # 보유 종목 수
            "holdings_names":  ", ".join(names),           # 보유 종목명
        }

    except Exception as e:
        print(f"[ls_client] 포트폴리오 요약 조회 오류: {e}")
        return {}


def get_stock_themes(code: str) -> list:
    """종목이 속한 테마 목록을 조회한다 (t1532 종목별테마).

    한 종목은 여러 테마에 동시에 속할 수 있다.
    예: 케이뱅크 → ["인터넷은행", "핀테크", "카카오뱅크관련"]

    Args:
        code: 종목코드 6자리 (예: "279570")

    Returns:
        [{"tmcode": "001", "tmname": "인터넷은행"}, ...] 형태의 리스트
        테마가 없거나 조회 실패 시 빈 리스트
    """
    _check_login()

    try:
        response = _ls.korea_stock().sector().t1532(
            T1532InBlock(shcode=code)
        ).req()

        return [
            {
                "tmcode": item.tmcode.strip(),
                "tmname": item.tmname.strip(),
            }
            for item in response.block
            if item.tmname.strip()  # 이름이 빈 항목은 제외
        ]

    except Exception as e:
        print(f"[ls_client] 종목별 테마 조회 오류 ({code}): {e}")
        return []


def is_paper_trading() -> bool:
    """현재 모의투자 모드 여부를 반환한다."""
    return _paper_trading


# ─────────────────────────────────────────
# 종목 선정 (스크리닝) 관련 조회
# ─────────────────────────────────────────

# t1463 jc_num 비트마스크: 관리종목(128) + 시장경보(256) + 거래정지(512) + 우선주(16384)
_JC_NUM  = 128 + 256 + 512 + 16384   # = 17280
# t1463 jc_num2 비트마스크: ETF(1) + ETN(8) + 투자주의(16) + 투자위험(32) + 위험예고(64)
_JC_NUM2 = 1 + 8 + 16 + 32 + 64      # = 121


def get_trading_value_ranking(n: int = 200, prev_day: bool = False) -> list:
    """거래대금 상위 종목 목록을 조회한다 (t1463).

    API 파라미터 레벨에서 ETF·ETN·관리종목·우선주·투자경고·위험 종목을
    미리 걸러주므로 별도 필터 없이 바로 사용할 수 있다.

    Args:
        n:        가져올 종목 수 (기본 200)
        prev_day: True → 전일 거래대금 기준 / False → 당일 거래대금 기준

    Returns:
        거래대금 내림차순으로 정렬된 종목 리스트.
        [
            {
                "code":       "005930",  # 종목코드 6자리
                "name":       "삼성전자", # 종목명
                "price":      75000,     # 현재가 (원)
                "value":      500000,    # 거래대금 (백만원)
                "jnil_value": 480000,    # 전일거래대금 (백만원)
                "total_cap":  4500000,   # 시가총액 (백만원)
                "volume_rank": 1         # 거래대금 순위 (1이 최고)
            },
            ...
        ]
        조회 실패 시 빈 리스트.
    """
    _check_login()

    # 전일 기준이면 "1", 당일 기준이면 "0"
    jnilgubun = "1" if prev_day else "0"

    result    = []
    idx       = 0      # 연속조회키 (최초 0)
    rank      = 1      # 거래대금 순위 (1부터 시작)

    # 무한 루프 방지: 최대 페이지 수 상한
    _MAX_PAGES    = 30   # 페이지당 보통 10~20개이므로 30페이지면 충분
    # 호출 제한(HTTP 500) 시 재시도 설정
    _MAX_RETRIES  = 3    # 재시도 횟수
    _RETRY_WAIT   = 10.0 # 재시도 대기 시간 (초)

    try:
        page = 0
        while len(result) < n:
            page += 1
            if page > _MAX_PAGES:
                # 비정상적으로 페이지가 너무 많으면 중단 (무한 루프 방지)
                print(f"[ls_client] t1463 최대 페이지({_MAX_PAGES}) 도달 → 조회 중단")
                break

            # ── 재시도 루프 ──────────────────────────────────────
            resp = None
            for attempt in range(_MAX_RETRIES):
                try:
                    resp = _ls.korea_stock().ranking().t1463(
                        T1463InBlock(
                            gubun="0",           # 전체 (KOSPI + KOSDAQ)
                            jnilgubun=jnilgubun,
                            jc_num=_JC_NUM,      # 관리·경보·정지·우선주 제거
                            jc_num2=_JC_NUM2,    # ETF·ETN·투자주의/위험 제거
                            sprice=0,            # 가격 하한 필터 없음 (stock_screener에서 별도 처리)
                            eprice=0,            # 가격 상한 필터 없음
                            volume=0,            # 거래량 필터 없음
                            idx=idx,
                        )
                    ).req()
                    break  # 성공 시 재시도 루프 탈출

                except Exception as e:
                    err_str = str(e)
                    # '호출 거래건수 초과(HTTP 500)' 오류이면 대기 후 재시도
                    is_rate_limit = "500" in err_str or "호출 거래건수" in err_str
                    if is_rate_limit and attempt < _MAX_RETRIES - 1:
                        print(
                            f"[ls_client] t1463 호출 제한 오류 → "
                            f"{_RETRY_WAIT:.0f}초 후 재시도 "
                            f"({attempt + 1}/{_MAX_RETRIES}): {err_str}"
                        )
                        time.sleep(_RETRY_WAIT)
                    else:
                        # 재시도 횟수 초과이거나 다른 종류의 오류 → 상위로 전파
                        raise
            # ─────────────────────────────────────────────────────

            if resp is None or not resp.block:
                # 더 이상 데이터 없음
                break

            for item in resp.block:
                code = item.shcode.strip()
                if not code:
                    continue

                result.append({
                    "code":        code,
                    "name":        item.hname.strip(),
                    "price":       item.price,
                    "value":       item.value,       # 거래대금 (백만원)
                    "jnil_value":  item.jnilvalue,   # 전일거래대금 (백만원)
                    "total_cap":   item.total,       # 시가총액 (백만원)
                    "volume_rank": rank,
                })
                rank += 1

                if len(result) >= n:
                    break

            # 연속조회: cont_block이 있으면 idx 갱신, 없으면 종료
            if resp.cont_block and resp.cont_block.idx:
                idx = resp.cont_block.idx
                time.sleep(1.0)  # 연속 페이지 조회 간 대기 (0.3 → 1.0초로 증가)
            else:
                break

        day_label = "전일" if prev_day else "당일"
        print(f"[ls_client] 거래대금상위 조회 완료: {len(result)}개 ({day_label} 기준, {page}페이지)")
        return result

    except Exception as e:
        print(f"[ls_client] 거래대금상위 조회 오류: {e}")
        return []


def get_52week_high_stocks() -> list:
    """52주 신고가를 오늘 돌파한 종목 목록을 조회한다 (t1442).

    ETF·관리종목·우선주 등은 거래대금 랭킹과 동일한 비트마스크로 제거한다.

    Returns:
        [
            {
                "code":      "005930",  # 종목코드 6자리
                "name":      "삼성전자", # 종목명
                "price":     75000,     # 현재가 (원)
                "prev_high": 74500,     # 직전 52주 최고가 (pastprice 필드)
            },
            ...
        ]
        조회 실패 시 빈 리스트.
    """
    _check_login()

    result     = []
    idx        = 0    # 연속조회키
    _MAX_PAGES = 30   # 무한 루프 방지 상한

    try:
        page = 0
        while True:
            page += 1
            if page > _MAX_PAGES:
                # 비정상적으로 페이지가 너무 많으면 중단 (무한 루프 방지)
                print(f"[ls_client] t1442 최대 페이지({_MAX_PAGES}) 도달 → 조회 중단")
                break

            resp = _ls.korea_stock().market().t1442(
                T1442InBlock(
                    gubun="0",    # 전체 (KOSPI + KOSDAQ)
                    type1="0",    # 신고가
                    type2="6",    # 52주
                    type3="0",    # 일시돌파 (오늘 처음 돌파한 종목)
                    jc_num=_JC_NUM,
                    jc_num2=_JC_NUM2,
                    sprice=0,
                    eprice=0,
                    volume=0,
                    idx=idx,
                )
            ).req()

            if not resp.block:
                break

            for item in resp.block:
                code = item.shcode.strip()
                if not code:
                    continue

                result.append({
                    "code":      code,
                    "name":      item.hname.strip(),
                    "price":     item.price,
                    "prev_high": item.pastprice,  # 직전 52주 최고가
                })

            # 연속조회
            if resp.cont_block and resp.cont_block.idx:
                idx = resp.cont_block.idx
                time.sleep(1.0)  # 연속 페이지 조회 간 대기 (0.3 → 1.0초로 증가)
            else:
                break

        print(f"[ls_client] 52주 신고가 돌파 종목 조회 완료: {len(result)}개 ({page}페이지)")
        return result

    except Exception as e:
        print(f"[ls_client] 52주 신고가 조회 오류: {e}")
        return []
