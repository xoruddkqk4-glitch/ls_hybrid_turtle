# indicator_calc.py
# 기술 지표 계산 모듈
#
# 터틀 트레이딩 전략에 필요한 지표들을 계산한다:
#   - ATR(N): 평균 실제 범위 → Unit 수량 계산, 손절가, 피라미딩 트리거에 사용
#   - 이동평균선(5MA, 20MA): 트레일링 스탑 판단에 사용
#   - 10일 신저가: 트레일링 스탑 판단에 사용
#   - 240분봉 20MA: 동적 목표가(pending_target) 계산에 사용
#
# 주의: 이 모듈은 ls_client를 통해서만 API 데이터를 가져온다.

import ls_client


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


def get_all_indicators(code: str) -> dict:
    """한 종목의 전략에 필요한 모든 지표를 한 번에 계산해서 반환한다.

    내부적으로 ls_client를 통해 일봉과 240분봉 데이터를 가져온다.

    Args:
        code: 종목코드 6자리 (예: "005930")

    Returns:
        {
            "atr":       1200.0,  # ATR(N): Unit 수량·손절가·피라미딩 트리거 계산에 사용
            "ma5":      74500.0,  # 5일 이동평균: 트레일링 스탑(수익권 체크)에 사용
            "ma20":     73000.0,  # 20일 이동평균 (일봉)
            "day10_low": 71000,   # 10일 신저가: 트레일링 스탑에 사용
            "ma240_20": 76000.0,  # 240분봉 20MA: 동적 목표가 계산에 사용
        }
        데이터 부족 또는 오류 시 모든 값이 0인 딕셔너리 반환.
    """
    # 초기값 (데이터 부족 등 오류 시 반환)
    default = {"atr": 0.0, "ma5": 0.0, "ma20": 0.0, "day10_low": 0, "ma240_20": 0.0}

    try:
        # 일봉 데이터 조회 (25개: 20일 ATR + 여유 5일)
        daily = ls_client.get_daily_chart(code, count=25)
        if not daily:
            print(f"[indicator] {code} 일봉 데이터 없음")
            return default

        close_list = [d["close"] for d in daily]

        # 240분봉 데이터 조회 (25개: 20MA 계산 + 여유)
        minute_data = ls_client.get_minute_chart(code, minute=240, count=25)
        minute_close = [m["close"] for m in minute_data] if minute_data else []

        return {
            "atr":       calc_atr(daily, period=20),
            "ma5":       calc_ma(close_list, period=5),
            "ma20":      calc_ma(close_list, period=20),
            "day10_low": calc_10day_low(daily),
            "ma240_20":  calc_ma(minute_close, period=20),
        }

    except Exception as e:
        print(f"[indicator] {code} 지표 계산 오류: {e}")
        return default
