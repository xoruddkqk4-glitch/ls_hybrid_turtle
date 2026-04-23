# sector_cache.py
# 종목별 테마 캐시 관리 모듈
#
# 역할:
#   LS증권 t1532(종목별테마) API를 통해 lovely_stock_list 각 종목이
#   어떤 테마에 속하는지 조회하고, sector_cache.json 파일에 저장한다.
#
#   turtle_order_logic.py에서 업종 대신 "테마"를 기준으로
#   포트폴리오 유닛 한도를 관리할 때 이 파일을 참조한다.
#
# sector_cache.json 구조:
# {
#   "279570": {
#     "name": "케이뱅크",
#     "primary_theme": "인터넷은행",   ← 유닛 한도 계산에 사용하는 대표 테마
#     "themes": [                       ← t1532가 반환한 전체 테마 목록
#       {"tmcode": "001", "tmname": "인터넷은행"},
#       {"tmcode": "042", "tmname": "핀테크"}
#     ],
#     "fetched_at": "2026-04-13 09:00:00"  ← 마지막 API 조회 시각
#   }
# }
#
# 사용법:
#   import sector_cache
#   sector_cache.update_sector_cache()   ← 로그인 후 1회 호출 (당일 최초)
#   theme = sector_cache.get_stock_sector("279570")  ← "인터넷은행"

import json
import os
import time
from datetime import datetime

import pytz

import ls_client
from config import get_watchlist

# 캐시 파일 경로
SECTOR_CACHE_FILE = "sector_cache.json"

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")


# ─────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────

def load_sector_cache() -> dict:
    """sector_cache.json을 읽어서 반환한다.

    파일이 없거나 손상된 경우 빈 딕셔너리를 반환한다.

    Returns:
        종목코드 → {name, primary_theme, themes, fetched_at} 딕셔너리
    """
    if os.path.exists(SECTOR_CACHE_FILE):
        try:
            with open(SECTOR_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, IOError):
            print(f"[sector_cache] {SECTOR_CACHE_FILE} 읽기 오류 → 빈 캐시로 시작")
    return {}


def save_sector_cache(cache: dict):
    """테마 캐시를 sector_cache.json에 저장한다.

    Args:
        cache: 종목코드 → 테마 정보 딕셔너리
    """
    try:
        with open(SECTOR_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[sector_cache] 파일 저장 오류: {e}")


# ─────────────────────────────────────────
# 캐시 갱신
# ─────────────────────────────────────────

def _is_cache_fresh(cache: dict) -> bool:
    """캐시가 오늘 이미 갱신됐는지 확인한다.

    감시 종목(get_watchlist())의 모든 종목이 오늘 날짜로 캐시돼 있으면 True를 반환한다.
    하나라도 빠져 있거나 어제 날짜이면 False(재갱신 필요)를 반환한다.

    Returns:
        True:  오늘 이미 갱신 완료 → 재갱신 불필요
        False: 갱신 필요
    """
    today     = datetime.now(KST).strftime("%Y-%m-%d")
    watchlist = get_watchlist()

    if not watchlist:
        return True  # 감시 목록이 비어 있으면 갱신할 것 없음

    for code in watchlist:
        entry = cache.get(code)
        if not entry:
            return False  # 해당 종목 캐시 없음
        fetched_at = entry.get("fetched_at", "")
        if not fetched_at.startswith(today):
            return False  # 오늘 날짜가 아님

    return True


def update_sector_cache():
    """감시 종목(get_watchlist()) 전체의 테마를 t1532 API로 조회하고 캐시에 저장한다.

    오늘 이미 갱신된 캐시가 있으면 API 호출을 건너뛴다.
    (t1532 속도 제한: 1회/초 → 종목 50개 기준 약 50초 소요)

    실행 조건: ls_client.login() 완료 후에 호출해야 한다.
    """
    print("[sector_cache] 테마 캐시 갱신 확인 중")

    watchlist = get_watchlist()
    if not watchlist:
        print("[sector_cache] 감시 종목 없음 → 테마 캐시 갱신 스킵")
        return

    cache = load_sector_cache()

    # 오늘 이미 갱신됐으면 스킵
    if _is_cache_fresh(cache):
        names = [watchlist[c]["name"] for c in watchlist]
        print(f"[sector_cache] 오늘 이미 갱신된 캐시 있음 → 스킵 ({len(names)}개)")
        return

    total = len(watchlist)
    print(f"[sector_cache] 테마 캐시 갱신 시작 ({total}개, t1532 API 호출 — 약 {total}초 소요)")
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    for i, (code, stock_info) in enumerate(watchlist.items(), 1):
        name = stock_info["name"]

        # 현재 몇 번째 종목을 처리 중인지 표시 (파워셀에서 진행 상황 확인용)
        print(f"[sector_cache] 진행 중 {i}/{total}: {name}({code}) — 테마 조회 중...")

        # API 속도 제한 방지 (t1532: 1회/초)
        time.sleep(1.0)

        themes = ls_client.get_stock_themes(code)

        if themes:
            # 첫 번째 테마를 대표 테마(primary_theme)로 사용
            primary = themes[0]["tmname"]
            theme_names = [t["tmname"] for t in themes]
            print(f"[sector_cache]   └ 테마 {len(themes)}개: "
                  f"{', '.join(theme_names[:5])}"
                  f"{'...' if len(themes) > 5 else ''}")
        else:
            primary = ""
            print(f"[sector_cache]   └ 테마 없음")

        cache[code] = {
            "name":          name,
            "primary_theme": primary,
            "themes":        themes,
            "fetched_at":    now_str,
        }

    save_sector_cache(cache)
    print(f"[sector_cache] 테마 캐시 저장 완료 ({SECTOR_CACHE_FILE})")


# ─────────────────────────────────────────
# 조회
# ─────────────────────────────────────────

def get_stock_sector(code: str) -> str:
    """종목의 대표 테마(primary_theme)를 반환한다.

    turtle_order_logic.py의 업종별 유닛 한도 계산에 사용한다.
    캐시에 데이터가 없으면 빈 문자열을 반환해 유닛 제한을 건너뛴다.

    Args:
        code: 종목코드 6자리 (예: "279570")

    Returns:
        대표 테마명 (예: "인터넷은행"). 캐시 없으면 "".
    """
    cache = load_sector_cache()
    return cache.get(code, {}).get("primary_theme", "")


def get_stock_themes_cached(code: str) -> list:
    """캐시에 저장된 종목의 전체 테마 목록을 반환한다.

    Args:
        code: 종목코드 6자리

    Returns:
        [{"tmcode": "...", "tmname": "..."}, ...]. 캐시 없으면 [].
    """
    cache = load_sector_cache()
    return cache.get(code, {}).get("themes", [])
