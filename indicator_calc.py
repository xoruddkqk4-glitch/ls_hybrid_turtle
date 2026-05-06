# indicator_calc.py
# 기술 지표 계산 모듈
#
# 터틀 트레이딩 전략에 필요한 지표들을 계산한다:
#   - ATR(N): 평균 실제 범위 → Unit 수량 계산, 손절가, 피라미딩 트리거에 사용
#   - 이동평균선(5MA, 20MA): 트레일링 스탑 판단에 사용
#   - 10일 신저가: 트레일링 스탑 판단에 사용
#
# 주의: 이 모듈은 daily_chart_cache(캐시 우선)를 통해 데이터를 가져오며,
#       캐시 미존재 시에는 ls_client를 통해 API를 직접 조회한다.

import daily_chart_cache
import ls_client
import json
import os
from config import get_watchlist

# run_all 1회 실행 주기 내 메모리 캐시.
# 파일 캐시가 비어 API 폴백이 발생한 경우 같은 주기에서 재호출을 줄인다.
_RUNTIME_DAILY_CACHE: dict[str, list] = {}


_DIR = os.path.dirname(os.path.abspath(__file__))
_HELD_RECORD_FILE = os.path.join(_DIR, "held_stock_record.json")


def _get_stock_name(code: str) -> str:
    """종목명을 조회한다. watchlist → held_stock_record → code 순으로 찾는다."""
    name = get_watchlist().get(code, {}).get("name", "")
    if name:
        return name
    if os.path.exists(_HELD_RECORD_FILE):
        try:
            with open(_HELD_RECORD_FILE, "r", encoding="utf-8") as f:
                held = json.load(f)
            if isinstance(held, dict):
                held_name = held.get(code, {}).get("stock_name", "")
                if held_name:
                    return held_name
        except Exception:
            pass
    return code


def calc_atr(ohlcv_list: list, period: int = 20) -> float:
    """ATR(Average True Range, 평균 실제 범위)을 계산한다.

    ATR은 주가의 하루 변동폭 평균이다.
    터틀 트레이딩에서 'N'이라고 부르며, Unit 수량 계산과 손절가 설정에 사용한다.

    True Range = max(고가 - 저가, |고가 - 전일종가|, |저가 - 전일종가|)
    ATR = 최근 period일 True Range의 평균

    Args:
        ohlcv_list: 날짜 오름차순 정렬된 OHLCV 딕셔너리 리스트
                    [{"open":..., "high":..., "low":..., "close":...}, ...]
                    최소 (period + 1)개 이상 필요
        period:     ATR 계산 기간 (기본 20일)

    Returns:
        ATR 값 (float). 데이터 부족 시 0.0.
    """
    # 최소 (period + 1)개가 필요: period일 TR 계산에는 period개의 전일종가가 필요
    if len(ohlcv_list) < period + 1:
        print(f"[indicator] ATR 계산 데이터 부족: {len(ohlcv_list)}개 (필요: {period + 1}개 이상)")
        return 0.0

    # 각 날짜의 True Range 계산
    true_ranges = []
    for i in range(1, len(ohlcv_list)):
        high       = ohlcv_list[i]["high"]
        low        = ohlcv_list[i]["low"]
        prev_close = ohlcv_list[i - 1]["close"]

        # 세 가지 중 가장 큰 값이 True Range
        tr = max(
            high - low,             # 당일 고저 차이
            abs(high - prev_close), # 전일종가 대비 당일 고가 차이
            abs(low  - prev_close), # 전일종가 대비 당일 저가 차이
        )
        true_ranges.append(tr)

    # 가장 최근 period개의 True Range 평균
    recent_trs = true_ranges[-period:]
    return sum(recent_trs) / len(recent_trs)


def calc_ma(close_list: list, period: int) -> float:
    """단순 이동평균선(SMA)을 계산한다.

    Args:
        close_list: 종가 리스트 (오름차순, 최신 값이 마지막)
                    예: [74000, 74500, 75000, ...]
        period:     이동평균 계산 기간 (일 수)

    Returns:
        period일 이동평균 값 (float). 데이터 부족 시 0.0.
    """
    if len(close_list) < period:
        print(f"[indicator] MA{period} 계산 데이터 부족: {len(close_list)}개 (필요: {period}개)")
        return 0.0

    # 최근 period개 종가의 평균
    return sum(close_list[-period:]) / period


def calc_10day_low(ohlcv_list: list) -> int:
    """최근 10일 중 가장 낮은 종가(10일 신저가)를 계산한다.

    트레일링 스탑 판단에 사용:
    현재가가 이 값 이하로 떨어지면 추세가 끝난 것으로 판단하여 익절 청산한다.

    Args:
        ohlcv_list: 날짜 오름차순 정렬된 OHLCV 딕셔너리 리스트
                    최소 10개 필요

    Returns:
        최근 10일 최저 종가 (정수). 데이터 부족 시 0.
    """
    if len(ohlcv_list) < 10:
        print(f"[indicator] 10일 신저가 데이터 부족: {len(ohlcv_list)}개 (필요: 10개 이상)")
        return 0

    # 가장 최근 10개 캔들의 종가 중 최솟값
    recent_10 = ohlcv_list[-10:]
    return min(item["close"] for item in recent_10)


def calc_n_day_high(ohlcv_list: list, n: int) -> float:
    """최근 N일 장중 고가(high) 중 최고값을 계산한다.

    오리지널 터틀 트레이딩 진입 신호 판단에 사용한다.
      - 시스템1: n=20 (최근 20일 고가 최고값)
      - 시스템2: n=55 (최근 55일 고가 최고값)

    오늘 캔들은 제외하고 직전 N일만 본다.
    현재 장중가(current_price)와 비교해 "오늘 처음 신고가를 돌파했는가"를 판단하기 위해서다.

    Args:
        ohlcv_list: 날짜 오름차순 정렬된 OHLCV 딕셔너리 리스트
                    마지막 항목이 가장 최신(오늘 또는 직전 거래일)
        n:          기간 일 수 (20 또는 55)

    Returns:
        직전 N일 장중 고가 최고값 (float). 데이터 부족 시 0.0.
    """
    # 직전 N일 = 마지막(오늘) 캔들을 제외한 앞의 N개
    # 최소 (n + 1)개 필요: 직전 N일 + 오늘 1일
    if len(ohlcv_list) < n + 1:
        print(f"[indicator] {n}일 신고가 계산 데이터 부족: "
              f"{len(ohlcv_list)}개 (필요: {n + 1}개 이상)")
        return 0.0

    # 오늘(마지막) 제외 → 직전 N개 캔들의 장중 고가 중 최고값
    prev_n_candles = ohlcv_list[-(n + 1):-1]
    return float(max(d["high"] for d in prev_n_candles))


def get_screener_indicators(code: str) -> dict:
    """스크리닝(종목 선정)에 필요한 지표만 계산한다. 일봉만 사용해서 빠르다.

    수백 개 종목을 빠르게 처리할 때 적합하다.

    Args:
        code: 종목코드 6자리 (예: "005930")

    Returns:
        {
            "atr":       1200.0,   # 20일 ATR — 변동성 필터(1.5%)에 사용
            "atr_ratio":  0.016,   # ATR / 현재가 — 변동성 퍼센트
            "ma5":       74500.0,  # 5일 이동평균 — 정배열 스코어에 사용
            "ma20":      73000.0,  # 20일 이동평균 — 이격도 과열 컷에 사용
            "high_52w":  80000,    # 52주 최고가 근사값 (일봉 25개 중 최고값)
                                   #  ※ t1442 출신 종목은 prev_high가 더 정확하므로
                                   #    stock_screener에서 덮어쓴다
        }
        오류 또는 데이터 부족 시: 모든 값이 0인 딕셔너리
    """
    default = {"atr": 0.0, "atr_ratio": 0.0, "ma5": 0.0, "ma20": 0.0, "high_52w": 0}

    try:
        # 일봉 25개만 조회 (20일 ATR + 여유 5개)
        daily = ls_client.get_daily_chart(code, count=25)
        if not daily:
            return default

        close_list = [d["close"] for d in daily]
        current_price = close_list[-1]  # 가장 최근 종가

        atr = calc_atr(daily, period=20)
        if current_price <= 0:
            return default

        # 25개 일봉 중 고가(high)의 최댓값을 52주 최고가 근사값으로 사용
        high_52w = max(d["high"] for d in daily)

        return {
            "atr":       atr,
            "atr_ratio": atr / current_price,         # ATR 비율 (예: 0.016 = 1.6%)
            "ma5":       calc_ma(close_list, period=5),
            "ma20":      calc_ma(close_list, period=20),
            "high_52w":  high_52w,
        }

    except Exception as e:
        name = _get_stock_name(code)
        print(f"[indicator] {name}({code}) 스크리닝 지표 계산 오류: {e}")
        return default


def get_all_indicators(code: str) -> dict:
    """한 종목의 전략에 필요한 모든 지표를 한 번에 계산해서 반환한다.

    내부적으로 ls_client를 통해 일봉 데이터를 가져온다.

    Args:
        code: 종목코드 6자리 (예: "005930")

    Returns:
        {
            "atr":       1200.0,  # ATR(N): Unit 수량·손절가·피라미딩 트리거 계산에 사용
            "ma5":      74500.0,  # 5일 이동평균: 트레일링 스탑(수익권 체크)에 사용
            "ma20":     73000.0,  # 20일 이동평균 (일봉)
            "day10_low": 71000,   # 10일 신저가: 트레일링 스탑에 사용
        }
        데이터 부족 또는 오류 시 모든 값이 0인 딕셔너리 반환.
    """
    # 초기값 (데이터 부족 등 오류 시 반환)
    default = {"atr": 0.0, "ma5": 0.0, "ma20": 0.0, "day10_low": 0}

    try:
        name = _get_stock_name(code)
        # 일봉: 캐시 우선 — 오늘 날짜 캐시 없으면 API 직접 조회(폴백)
        daily = daily_chart_cache.get_daily_cached(code, count=60)
        if not daily:
            runtime_daily = _RUNTIME_DAILY_CACHE.get(code, [])
            if runtime_daily:
                daily = runtime_daily[-60:] if len(runtime_daily) >= 60 else runtime_daily
            else:
                print(f"[indicator] {name}({code}) 일봉 캐시 없음 → API 직접 조회")
                daily = ls_client.get_daily_chart(code, count=60)
                if daily:
                    _RUNTIME_DAILY_CACHE[code] = daily
                    # 파일 캐시에도 반영해 같은 실행 주기의 타 모듈 폴백을 줄인다.
                    daily_chart_cache.update_daily_cache(code, daily)
        if not daily:
            print(f"[indicator] {name}({code}) 일봉 데이터 없음")
            return default

        close_list = [d["close"] for d in daily]

        return {
            "atr":       calc_atr(daily, period=20),
            "ma5":       calc_ma(close_list, period=5),
            "ma20":      calc_ma(close_list, period=20),
            "day10_low": calc_10day_low(daily),
        }

    except Exception as e:
        name = _get_stock_name(code)
        print(f"[indicator] {name}({code}) 지표 계산 오류: {e}")
        return default
