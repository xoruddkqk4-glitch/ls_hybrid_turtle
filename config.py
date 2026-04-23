# config.py
# 감시 종목 로드 모듈
#
# 동적 종목 선정 시스템 도입 이후:
#   - lovely_stock_list(수동 고정 목록)는 제거되었다.
#   - 감시 종목은 매일 stock_screener.py가 생성하는
#     dynamic_watchlist.json 에서 자동으로 읽어온다.
#   - 수동으로 꼭 포함시킬 종목은 watchlist_config.json 의 whitelist 에,
#     제외할 종목은 blacklist 에 기재한다.
#     (stock_screener.py가 최종 50개 확정 시 이미 반영해서 저장함)
#
# 사용법:
#   from config import get_watchlist
#   watchlist = get_watchlist()
#   if code in watchlist: ...
#   name = watchlist.get(code, {}).get("name", code)

from __future__ import annotations  # 파이썬 3.9 이하에서도 dict | None 형식 허용

import json
import os
from datetime import datetime

import pytz

_KST = pytz.timezone("Asia/Seoul")

# dynamic_watchlist.json 경로 (스크립트 위치 기준 절대 경로)
_DIR = os.path.dirname(os.path.abspath(__file__))
_WATCHLIST_PATH = os.path.join(_DIR, "dynamic_watchlist.json")

# 모듈 수준 캐시 — 같은 프로세스 안에서 파일을 두 번 읽지 않도록 저장
_watchlist_cache: dict | None = None


def get_watchlist() -> dict:
    """오늘 날짜의 dynamic_watchlist.json을 읽어서 감시 종목 딕셔너리를 반환한다.

    반환 형식:
        {"종목코드": {"name": "종목명", "market": "KOSPI", "score": 0.82, "atr": 1200.0}, ...}

    동작 순서:
        1. 모듈 캐시에 오늘 데이터가 있으면 바로 반환 (파일 재읽기 없음)
        2. dynamic_watchlist.json이 존재하고 "date" 필드가 오늘이면 → 로드 후 반환
        3. 파일 없음 / 날짜 불일치 / 읽기 오류 → 빈 딕셔너리 {} 반환
           ← 스크리너가 아직 실행되지 않은 경우, 매매 모듈이 자동으로 아무것도 안 함

    수동 화이트리스트/블랙리스트:
        watchlist_config.json 에 기재하면 stock_screener가 최종 50개 선정 시
        이미 반영해서 저장한다. 이 함수에서 별도 필터를 적용하지 않는다.
    """
    global _watchlist_cache

    # 캐시가 있으면 바로 반환
    if _watchlist_cache is not None:
        return _watchlist_cache

    if not os.path.exists(_WATCHLIST_PATH):
        print("[config] dynamic_watchlist.json 없음 → 빈 감시 목록 반환")
        return {}

    try:
        with open(_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 오늘 날짜 확인
        today_str = datetime.now(_KST).strftime("%Y%m%d")
        file_date = data.get("date", "")

        if file_date != today_str:
            print(
                f"[config] dynamic_watchlist.json 날짜 불일치 "
                f"(파일:{file_date}, 오늘:{today_str}) → 빈 감시 목록 반환"
            )
            return {}

        stocks = data.get("stocks", {})
        _watchlist_cache = stocks
        print(f"[config] 감시 종목 {len(stocks)}개 로드 완료")
        return stocks

    except Exception as e:
        print(f"[config] dynamic_watchlist.json 읽기 오류: {e} → 빈 감시 목록 반환")
        return {}


def get_stock_name(code: str) -> str:
    """종목코드로 종목 이름을 반환한다. 감시 목록에 없으면 코드를 그대로 반환한다."""
    return get_watchlist().get(code, {}).get("name", code)
