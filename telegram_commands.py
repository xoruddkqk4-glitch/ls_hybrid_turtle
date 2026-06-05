# telegram_commands.py
# 텔레그램 봇 명령어 처리 모듈
#
# telegram_listener.py가 받은 메시지 텍스트를 dispatch()로 넘기면,
# 명령어를 파싱·라우팅해서 적절한 핸들러를 호출하고 응답 텍스트를 반환한다.
#
# 명령어 목록 (10개):
#   감시 종목 관리: /add  /remove  /block  /unblock
#   조회:          /list  /watch  /held  /balance  /status
#   기타:          /help
#
# LS API 로그인 정책:
#   /balance, /held 명령은 호출 시점에 매번 새로 로그인한다 (A안 — 안전).
#   다른 명령은 LS API를 사용하지 않는다.
#
# 사용법:
#   import telegram_commands
#   reply = telegram_commands.dispatch("/add 005930 삼성전자")

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime

import pytz

import watchlist_writer
from telegram_alert import SendMessage
import daily_chart_cache
import indicator_calc
import sector_cache
# 포트폴리오/테마 유닛 상한 상수 — turtle_order_logic과 같은 값을 재사용한다
from turtle_order_logic import (
    MAX_TOTAL_UNITS,
    MAX_SECTOR_UNITS,
    MAX_UNIT_PURCHASE_RATIO,
)

# ls_client는 LS API 호출 시점에만 lazy import
# (programgarden-finance 미설치 환경에서도 다른 명령들이 동작하도록)

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR          = os.path.dirname(os.path.abspath(__file__))
_HELD_PATH    = os.path.join(_DIR, "held_stock_record.json")
_DYNAMIC_PATH = os.path.join(_DIR, "dynamic_watchlist.json")
_CONFIG_PATH  = os.path.join(_DIR, "watchlist_config.json")
_UNHELD_PATH  = os.path.join(_DIR, "unheld_stock_record.json")


# ─────────────────────────────────────────
# 인자 파싱 헬퍼
# ─────────────────────────────────────────

def _parse_code_and_name(args: list) -> tuple:
    """텍스트 인자에서 종목코드와 종목명을 분리한다.

    종목명이 공백을 포함할 수 있으므로(예: "삼성 전자"), 첫 토큰을 코드로,
    나머지 모든 토큰을 공백으로 합쳐 이름으로 본다.

    Args:
        args: 명령어 다음 토큰 리스트
              예: ["005930", "삼성", "전자"]

    Returns:
        (code, name) 튜플.
        ([])              → ("", "")
        (["005930"])      → ("005930", "")
        (["005930","삼성"]) → ("005930", "삼성")
        (["005930","삼성","전자"]) → ("005930", "삼성 전자")
    """
    if not args:
        return "", ""
    code = args[0].strip()
    name = " ".join(args[1:]).strip() if len(args) > 1 else ""
    return code, name


# ─────────────────────────────────────────
# LS API 로그인 (A안: 매번 새로 로그인)
# ─────────────────────────────────────────

def _ls_login_or_none() -> bool:
    """LS API에 로그인을 시도한다 (실패 시 False 반환).

    /balance, /held 명령마다 매번 새로 로그인한다.
    이 방식은 토큰 만료/run_all.py와의 충돌 위험 없이 안전하다.
    응답이 1~2초 느려지지만 사용자 체감 차이 없음.

    Returns:
        True:  로그인 성공
        False: 로그인 실패 (호출자가 사용자에게 안내해야 함)
    """
    try:
        import ls_client
        return ls_client.login()
    except Exception as e:
        print(f"[telegram_commands] LS 로그인 오류: {e}")
        return False


# ─────────────────────────────────────────
# 핸들러: 단순 조회 (LS API 사용 안 함)
# ─────────────────────────────────────────

def handle_help(args: list) -> str:
    """명령어 안내."""
    return (
        "📖 텔레그램 봇 명령어 안내\n\n"
        "🔧 감시 종목 관리\n"
        "/add CODE NAME      화이트리스트 추가 (예: /add 005930 삼성전자)\n"
        "/remove CODE        화이트리스트 제거\n"
        "/block CODE NAME    블랙리스트 추가 (감시 제외)\n"
        "/unblock CODE       블랙리스트 제거 (차단 해제)\n\n"
        "📋 조회\n"
        "/list               화이트리스트 + 블랙리스트 전체\n"
        "/list white         화이트리스트만\n"
        "/list black         블랙리스트만\n"
        "/watch              오늘 실제 감시 중인 종목\n"
        "/held               현재 보유 종목 + 평균가/손절가/수익률\n"
        "/balance            계좌 잔고 (총자본/예수금/손익)\n"
        "/status             시스템 상태\n\n"
        "/help               이 안내\n\n"
        "※ 종목코드는 6자리 숫자 (예: 005930)\n"
        "※ 종목명은 공백 포함 가능"
    )


def handle_status(args: list) -> str:
    """시스템 상태 — 각 JSON 파일의 갱신 시각·종목 수."""
    now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    lines   = [f"🟢 시스템 상태\n현재 시각: {now_str}\n"]

    # dynamic_watchlist.json
    try:
        with open(_DYNAMIC_PATH, encoding="utf-8") as f:
            dyn = json.load(f)
        lines.append(f"감시 종목: {dyn.get('count', 0)}개 ({dyn.get('date', '?')} 기준)")
        lines.append(f"  갱신: {dyn.get('updated_at', '?')}")
    except FileNotFoundError:
        lines.append("감시 종목: 파일 없음 (스크리너 미실행)")
    except Exception as e:
        lines.append(f"감시 종목: 읽기 오류 ({e})")

    # held_stock_record.json
    try:
        with open(_HELD_PATH, encoding="utf-8") as f:
            held = json.load(f)
        lines.append(f"보유 종목: {len(held)}개")
    except FileNotFoundError:
        lines.append("보유 종목: 0개 (held_stock_record.json 없음)")
    except Exception as e:
        lines.append(f"보유 종목: 읽기 오류 ({e})")

    # watchlist_config.json
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        lines.append(f"화이트리스트: {len(cfg.get('whitelist', []))}개")
        lines.append(f"블랙리스트:   {len(cfg.get('blacklist', []))}개")
    except FileNotFoundError:
        lines.append("watchlist_config.json: 파일 없음")
    except Exception as e:
        lines.append(f"watchlist_config: 읽기 오류 ({e})")

    return "\n".join(lines)


def handle_list(args: list) -> str:
    """화이트리스트/블랙리스트 표시.

    /list           → 둘 다
    /list white     → 화이트리스트만
    /list black     → 블랙리스트만
    """
    filter_kind = args[0].lower() if args else ""
    if filter_kind not in ("", "white", "black"):
        return ("사용법: /list [white|black]\n"
                "예: /list, /list white, /list black")

    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return "📋 watchlist_config.json 없음 (설정된 화이트/블랙리스트 없음)"
    except Exception as e:
        return f"📋 watchlist_config.json 읽기 오류: {e}"

    whitelist = cfg.get("whitelist", [])
    blacklist = cfg.get("blacklist", [])

    def _fmt(item) -> str:
        # 문자열·딕셔너리 둘 다 지원 (구버전 호환)
        if isinstance(item, str):
            return f"  - {item}"
        return f"  - {item.get('name', '?')}({item.get('code', '?')})"

    parts = []

    if filter_kind in ("", "white"):
        parts.append(f"📋 화이트리스트 ({len(whitelist)}개)")
        if whitelist:
            parts.extend(_fmt(it) for it in whitelist)
        else:
            parts.append("  (없음)")
        if filter_kind == "":
            parts.append("")

    if filter_kind in ("", "black"):
        parts.append(f"🚫 블랙리스트 ({len(blacklist)}개)")
        if blacklist:
            parts.extend(_fmt(it) for it in blacklist)
        else:
            parts.append("  (없음)")

    return "\n".join(parts)


def handle_watch(args: list) -> str:
    """오늘 실제 감시 중인 종목 (dynamic_watchlist.json) — 분류 및 상세 조회."""
    try:
        with open(_DYNAMIC_PATH, encoding="utf-8") as f:
            dyn = json.load(f)
    except FileNotFoundError:
        return "👀 dynamic_watchlist.json 없음 (스크리너 미실행)"
    except Exception as e:
        return f"👀 읽기 오류: {e}"

    stocks = dyn.get("stocks", {})
    count  = len(stocks)
    date   = dyn.get("date", "?")

    if not stocks:
        return f"👀 오늘 감시 종목 (0개, {date} 기준)\n  (없음)"

    if not _ls_login_or_none():
        return "❌ LS증권 로그인 실패. 잠시 후 다시 시도해 주세요."

    try:
        import ls_client
        watchlist_codes = list(stocks.keys())
        prices = ls_client.get_multi_price(watchlist_codes)
    except Exception as e:
        return f"❌ 시세 조회 오류: {e}"

    try:
        if os.path.exists(_UNHELD_PATH):
            with open(_UNHELD_PATH, encoding="utf-8") as f:
                unheld = json.load(f)
        else:
            unheld = {}
    except Exception:
        unheld = {}

    try:
        if os.path.exists(_HELD_PATH):
            with open(_HELD_PATH, encoding="utf-8") as f:
                held = json.load(f)
            held_codes = set(held.keys())
        else:
            held_codes = set()
    except Exception:
        held_codes = set()

    group_a = [] # 재돌파 대기 중 (🔴)
    group_b = [] # 매수 조건 대기 중 (🔵)

    for code, info in stocks.items():
        if code in held_codes:
            continue
        unheld_info = unheld.get(code, {})
        s1_locked = unheld_info.get("turtle_s1_peak_locked", False) or unheld_info.get("turtle_s1_entry_ready", False)
        s2_locked = unheld_info.get("turtle_s2_peak_locked", False) or unheld_info.get("turtle_s2_entry_ready", False)

        item = {
            "code": code,
            "name": info.get("name", "?"),
            "score": info.get("score", 0.0),
            "s1_locked": s1_locked,
            "s2_locked": s2_locked,
            "unheld_info": unheld_info,
        }

        if s1_locked or s2_locked:
            group_a.append(item)
        else:
            group_b.append(item)

    # 각각 score 내림차순 정렬
    group_a.sort(key=lambda x: x["score"], reverse=True)
    group_b.sort(key=lambda x: x["score"], reverse=True)

    lines = [f"👀 오늘 감시 종목 ({len(group_a) + len(group_b)}개, {date} 기준)"]

    now_kst = datetime.now(_KST).replace(tzinfo=None)

    # 🔴 재돌파 대기 중 종목 출력
    for item in group_a:
        code = item["code"]
        name = item["name"]
        score = item["score"]
        s1_locked = item["s1_locked"]
        s2_locked = item["s2_locked"]
        unheld_info = item["unheld_info"]
        cur_price = prices.get(code, 0)

        # 재돌파 기준가 및 시스템 선택 (S2 우선)
        if s2_locked:
            peak_price = unheld_info.get("turtle_s2_peak_price")
            system_label = "S2"
            locked_at_str = unheld_info.get("turtle_s2_locked_at")
        else:
            peak_price = unheld_info.get("turtle_s1_peak_price")
            system_label = "S1"
            locked_at_str = unheld_info.get("turtle_s1_locked_at")

        peak_price_val = int(peak_price) if peak_price is not None else 0

        # 대기 시간 계산
        time_str = "정보 없음"
        if locked_at_str:
            try:
                locked_at = datetime.strptime(locked_at_str, "%Y-%m-%d %H:%M:%S")
                delta = now_kst - locked_at
                seconds = int(delta.total_seconds())
                if seconds < 0:
                    time_str = "0분째"
                elif seconds < 60:
                    time_str = "1분 미만"
                elif seconds < 3600:
                    time_str = f"{seconds // 60}분째"
                else:
                    hours = seconds // 3600
                    mins = (seconds % 3600) // 60
                    time_str = f"{hours}시간 {mins}분째"
            except Exception:
                pass

        lines.append(f"\n🔴 {name}({code}) [Score: {score:.2f}]")
        lines.append(f"  현재가: {cur_price:,}원")
        lines.append(f"  재돌파 기준가: {peak_price_val:,}원 ({system_label})")
        lines.append(f"  대기 시간: {time_str} 대기 중")

    # 🔵 매수 조건 대기 중 종목 출력
    for item in group_b:
        code = item["code"]
        name = item["name"]
        score = item["score"]
        cur_price = prices.get(code, 0)

        # S1/S2 돌파 기준가 계산
        daily = daily_chart_cache.get_daily_cached(code, count=60)
        s1_high = indicator_calc.calc_n_day_high(daily, n=20)
        s2_high = indicator_calc.calc_n_day_high(daily, n=55)

        s1_str = f"{int(s1_high):,}원" if s1_high > 0 else "정보 없음"
        s2_str = f"{int(s2_high):,}원" if s2_high > 0 else "정보 없음"

        lines.append(f"\n🔵 {name}({code}) [Score: {score:.2f}]")
        lines.append(f"  현재가: {cur_price:,}원")
        lines.append(f"  S1 20일고가 (돌파기준가): {s1_str}")
        lines.append(f"  S2 55일고가 (돌파기준가): {s2_str}")

    return "\n".join(lines)


# ─────────────────────────────────────────
# 핸들러: 감시 종목 관리 (watchlist_writer 호출)
# ─────────────────────────────────────────

def handle_add(args: list) -> str:
    """화이트리스트 추가."""
    code, name = _parse_code_and_name(args)
    if not code:
        return "사용법: /add CODE NAME\n예: /add 005930 삼성전자"
    if not name:
        return f"종목명을 입력해 주세요.\n예: /add {code} 종목명"

    r = watchlist_writer.add_to_whitelist(code, name)
    return ("✅ " if r["ok"] else "❌ ") + r["msg"]


def handle_remove(args: list) -> str:
    """화이트리스트 제거. 보유 중 종목이면 경고 추가."""
    code, _ = _parse_code_and_name(args)
    if not code:
        return "사용법: /remove CODE\n예: /remove 005930"

    r   = watchlist_writer.remove_from_whitelist(code)
    msg = ("✅ " if r["ok"] else "❌ ") + r["msg"]
    if r.get("warn_held"):
        msg += ("\n⚠️ 현재 보유 중인 종목입니다.\n"
                "감시 목록에서는 빠지지만 손절·익절은 자동으로 계속 작동합니다.")
    return msg


def handle_block(args: list) -> str:
    """블랙리스트 추가. 보유 중 종목이면 강한 경고 추가."""
    code, name = _parse_code_and_name(args)
    if not code:
        return "사용법: /block CODE NAME\n예: /block 000660 SK하이닉스"
    if not name:
        return f"종목명을 입력해 주세요.\n예: /block {code} 종목명"

    r   = watchlist_writer.add_to_blacklist(code, name)
    msg = ("🚫 " if r["ok"] else "❌ ") + r["msg"]
    if r.get("warn_held"):
        msg += ("\n⚠️ 현재 보유 중인 종목입니다!\n"
                "감시 목록에서 빠지면서 추가 매수(피라미딩)가 중단됩니다.\n"
                "손절·익절은 자동으로 계속 작동합니다.")
    return msg


def handle_unblock(args: list) -> str:
    """블랙리스트 제거 (차단 해제)."""
    code, _ = _parse_code_and_name(args)
    if not code:
        return "사용법: /unblock CODE\n예: /unblock 000660"

    r = watchlist_writer.remove_from_blacklist(code)
    return ("✅ " if r["ok"] else "❌ ") + r["msg"]


# ─────────────────────────────────────────
# 피라미딩 지연 사유 판단 헬퍼
# ─────────────────────────────────────────

def _diagnose_pyramid_block(code: str, current_price: float, held: dict,
                            total_capital: float) -> str:
    """현재가가 피라미딩가를 넘었는데도 추가 매수가 안 된 이유를 찾아 문장으로 돌려준다.

    turtle_order_logic.run_orders()의 피라미딩 [B] 블록과 같은 순서로 점검한다.
    (블랙리스트 → 전체 유닛 한도 → 테마 한도 → 1주 가격이 매수금 상한 초과)

    Args:
        code:          종목코드 6자리
        current_price: 현재가 (원)
        held:          held_stock_record.json 전체 딕셔너리
        total_capital: 총자본 (원, 0이면 매수금 상한 점검은 건너뜀)

    Returns:
        사유 문자열 (예: "블랙리스트 차단됨"). 막힌 이유를 못 찾으면 빈 문자열.
    """
    # ① 블랙리스트(/block) 차단 — 추가 매수·매도 감시 모두 중단된 상태
    try:
        if watchlist_writer.is_blacklisted(code):
            return "🚫 블랙리스트(/block) 차단 — 추가 매수 중단"
    except Exception:
        pass

    # ② 포트폴리오 전체 유닛 한도(15) 도달
    total_units = sum(pos.get("current_unit", 0) for pos in held.values())
    if total_units >= MAX_TOTAL_UNITS:
        return f"포트폴리오 유닛 한도 도달 ({total_units}/{MAX_TOTAL_UNITS} Unit)"

    # ③ 같은 테마 유닛 한도(6) 도달
    sector = _safe_sector(code)
    if sector:
        sector_units = sum(
            pos.get("current_unit", 0)
            for c, pos in held.items()
            if _safe_sector(c) == sector
        )
        if sector_units >= MAX_SECTOR_UNITS:
            return (f"테마 유닛 한도 도달 "
                    f"(테마: {sector}, {sector_units}/{MAX_SECTOR_UNITS} Unit)")

    # ④ 1주 가격이 1 Unit 매수금 상한(총자본 10%)을 초과 → 1주도 못 삼
    if total_capital > 0 and current_price > 0:
        max_unit_amount = total_capital * MAX_UNIT_PURCHASE_RATIO
        if current_price > max_unit_amount:
            return (f"1주 가격({int(current_price):,}원)이 "
                    f"1 Unit 상한({int(max_unit_amount):,}원) 초과")

    # 막힌 이유를 못 찾음 — 다음 정규 실행(10분 주기) 때 매수될 가능성이 큼
    return ""


def _safe_sector(code: str) -> str:
    """sector_cache 조회를 예외 없이 안전하게 감싼다 (오류 시 빈 문자열)."""
    try:
        return sector_cache.get_stock_sector(code)
    except Exception:
        return ""


# ─────────────────────────────────────────
# 핸들러: LS API 사용 (매번 새 로그인 — A안)
# ─────────────────────────────────────────

def handle_balance(args: list) -> str:
    """계좌 잔고 — 총자본·주식평가액·예수금·손익."""
    if not _ls_login_or_none():
        return "❌ LS증권 로그인 실패. 잠시 후 다시 시도해 주세요."

    try:
        import ls_client
        summary = ls_client.get_portfolio_summary()
        balance_list = ls_client.get_balance()
    except Exception as e:
        return f"❌ 잔고 조회 오류: {e}"

    if not summary:
        return "❌ 잔고 정보를 가져오지 못했습니다."

    total      = summary.get("total_capital", 0)
    stock_val  = summary.get("stock_value", 0)
    cash       = summary.get("cash", 0)
    purchase   = summary.get("purchase_amount", 0)
    unrealized = summary.get("unrealized_pnl", 0)
    realized   = summary.get("realized_pnl", 0)
    n_holding  = summary.get("holdings_count", 0)

    pnl_pct = (unrealized / purchase * 100) if purchase > 0 else 0.0
    pnl_sgn = "+" if unrealized >= 0 else ""

    lines = [
        "💰 계좌 잔고",
        f"총자본:     {total:,}원",
        f"  주식평가: {stock_val:,}원",
        f"  예수금:   {cash:,}원",
        f"매입금액:   {purchase:,}원",
        f"평가손익:   {pnl_sgn}{unrealized:,}원 ({pnl_sgn}{pnl_pct:.2f}%)",
        f"실현손익:   {realized:+,}원",
        f"보유종목:   {n_holding}개",
    ]

    # 보유 종목 상세 정보 로드
    try:
        with open(_HELD_PATH, encoding="utf-8") as f:
            held = json.load(f)
    except Exception:
        held = {}

    if balance_list:
        lines.append("")
        lines.append("📈 보유 종목:")
        for item in balance_list:
            code = item["code"]
            name = item.get("name") or held.get(code, {}).get("stock_name", code)
            qty  = int(item.get("qty", 0))
            avg  = float(item.get("avg_price", 0))
            cur  = float(item.get("current_price", 0))
            pnl_p = (cur - avg) / avg * 100 if avg > 0 else 0.0
            pnl_a = int((cur - avg) * qty)
            pnl_sgn_item = "+" if pnl_p >= 0 else ""
            unit = held.get(code, {}).get("current_unit", 0)
            lines.append(f"  - {name}({code}) {unit}U {qty:,}주 ({pnl_sgn_item}{pnl_p:.2f}%, {pnl_sgn_item}{pnl_a:,}원)")

    # 리스크 및 예산 요약 정보 추가
    total_units = sum(pos.get("current_unit", 0) for pos in held.values())
    lines.append("")
    lines.append(f"📊 포트폴리오 리스크: {total_units} / 15 Units")
    lines.append(f"⚠️ 1 Unit 예산 가이드:")
    lines.append(f"  위험 한도액(2%): {int(total * 0.02):,}원")
    lines.append(f"  최대 투자액(10%): {int(total * 0.10):,}원")

    return "\n".join(lines)


def handle_held(args: list) -> str:
    """보유 종목 + 평균가/손절가/수익률 (held_stock_record와 결합)."""
    if not _ls_login_or_none():
        return "❌ LS증권 로그인 실패. 잠시 후 다시 시도해 주세요."

    try:
        import ls_client
        balance = ls_client.get_balance()
        # 1 Unit 매수금 상한 점검에 쓸 총자본도 함께 조회 (실패해도 진행)
        try:
            total_capital = ls_client.get_total_capital()
        except Exception:
            total_capital = 0
    except Exception as e:
        return f"❌ 잔고 조회 오류: {e}"

    if not balance:
        return "📊 보유 종목 없음"

    # held_stock_record로 손절가·Unit 차수 등 보강
    try:
        with open(_HELD_PATH, encoding="utf-8") as f:
            held = json.load(f)
    except Exception:
        held = {}

    lines = [f"📊 보유 종목 ({len(balance)}개)"]
    for item in balance:
        code     = item["code"]
        name     = item.get("name") or held.get(code, {}).get("stock_name", code)
        qty      = int(item.get("qty", 0))
        avg      = float(item.get("avg_price", 0))
        cur      = float(item.get("current_price", 0))
        pnl_pct  = (cur - avg) / avg * 100 if avg > 0 else 0.0
        pnl_amt  = int((cur - avg) * qty)
        sign     = "+" if pnl_pct >= 0 else ""
        emoji    = "🔴" if pnl_pct < 0 else "🟢"

        unit = held.get(code, {}).get("current_unit", 0)
        max_unit = held.get(code, {}).get("max_unit", 4)
        stop = held.get(code, {}).get("stop_loss_price", 0)
        next_pyramid = held.get(code, {}).get("next_pyramid_price", 0)

        lines.append(f"\n{emoji} {name}({code}) {unit}U {qty:,}주")
        lines.append(f"  평균가: {int(avg):,}원")
        lines.append(f"  수익률: {sign}{pnl_pct:.2f}% ({sign}{pnl_amt:,}원)")
        if stop > 0:
            # 손절가에 도달해 전량 매도되면 실현되는 손익 = (손절가 - 평균가) × 수량
            # 트레일링 손절로 손절가가 평균가 위로 올라가면 +수익이 될 수 있음
            stop_pnl  = int((stop - avg) * qty) if avg > 0 else 0
            stop_sign = "+" if stop_pnl >= 0 else ""
            stop_label = "이익 확정" if stop_pnl >= 0 else "손실"
            lines.append(f"  손절가: {stop:,}원 (도달 시 {stop_sign}{stop_pnl:,}원 {stop_label})")
        lines.append(f"  현재가: {int(cur):,}원")
        if next_pyramid > 0:
            if unit >= max_unit:
                lines.append("  피라미딩: 완료")
            elif cur >= next_pyramid:
                # 현재가가 피라미딩가보다 높은데도 추가 매수가 아직 안 일어난 경우
                # → 막힌 이유를 찾아 함께 안내한다 (사용자 혼란 방지)
                reason = _diagnose_pyramid_block(code, cur, held, total_capital)
                lines.append(f"  피라미딩가: {next_pyramid:,}원 (현재가가 이미 넘음 → 매수 대기)")
                if reason:
                    lines.append(f"  └ 추가 매수 대기 사유: {reason}")
                else:
                    lines.append("  └ 다음 정규 실행(10분 주기) 때 추가 매수 예정")
            else:
                lines.append(f"  피라미딩가: {next_pyramid:,}원")

    return "\n".join(lines)


# ─────────────────────────────────────────
# 명령어 라우팅 (dispatch)
# ─────────────────────────────────────────

COMMAND_MAP = {
    "/add":      handle_add,
    "/remove":   handle_remove,
    "/block":    handle_block,
    "/unblock":  handle_unblock,
    "/list":     handle_list,
    "/watch":    handle_watch,
    "/held":     handle_held,
    "/balance":  handle_balance,
    "/status":   handle_status,
    "/help":     handle_help,
}


def dispatch(text: str) -> str:
    """명령 텍스트를 받아 적절한 핸들러를 호출하고 응답을 반환한다.

    핸들러에서 예외가 나도 데몬은 죽지 않는다 (try-except로 감싼다).
    예외 시 사용자에겐 친절 메시지, 관리자에겐 트레이스백 알림.

    Args:
        text: 사용자가 보낸 메시지 본문 (예: "/add 005930 삼성전자")

    Returns:
        텔레그램으로 답장할 응답 텍스트
    """
    if not text or not text.strip():
        return "빈 메시지입니다. /help 로 사용법을 확인하세요."

    text = text.strip()
    if not text.startswith("/"):
        return "명령어는 /로 시작합니다. /help 로 사용법을 확인하세요."

    tokens = text.split()
    cmd    = tokens[0].lower()
    args   = tokens[1:]

    handler = COMMAND_MAP.get(cmd)
    if handler is None:
        return f"알 수 없는 명령어: {cmd}\n/help 로 사용법을 확인하세요."

    try:
        return handler(args)
    except Exception as e:
        # 사용자에게는 친절 메시지, 관리자에게는 트레이스백 알림
        tb = traceback.format_exc()
        print(f"[telegram_commands] 명령 처리 오류: {cmd}\n{tb}")
        try:
            SendMessage(
                f"⚠️ 텔레그램 봇 명령 오류\n"
                f"명령: {cmd} {' '.join(args)}\n\n{tb[-1500:]}"
            )
        except Exception:
            pass
        return (f"❌ 명령 처리 중 오류가 발생했습니다: {e}\n"
                f"관리자에게 알림이 전송됐습니다.")
