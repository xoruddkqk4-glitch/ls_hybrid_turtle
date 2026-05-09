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

# ls_client는 LS API 호출 시점에만 lazy import
# (programgarden-finance 미설치 환경에서도 다른 명령들이 동작하도록)

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR          = os.path.dirname(os.path.abspath(__file__))
_HELD_PATH    = os.path.join(_DIR, "held_stock_record.json")
_DYNAMIC_PATH = os.path.join(_DIR, "dynamic_watchlist.json")
_CONFIG_PATH  = os.path.join(_DIR, "watchlist_config.json")


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
    """오늘 실제 감시 중인 종목 (dynamic_watchlist.json) — 점수 내림차순."""
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

    lines = [f"👀 오늘 감시 종목 ({count}개, {date} 기준)"]

    if not stocks:
        lines.append("  (없음)")
        return "\n".join(lines)

    # 점수 내림차순 정렬
    sorted_items = sorted(
        stocks.items(),
        key=lambda x: x[1].get("score", 0),
        reverse=True,
    )
    for code, info in sorted_items:
        name  = info.get("name", "?")
        score = info.get("score", 0)
        lines.append(f"  {name}({code}) score={score:.2f}")

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
# 핸들러: LS API 사용 (매번 새 로그인 — A안)
# ─────────────────────────────────────────

def handle_balance(args: list) -> str:
    """계좌 잔고 — 총자본·주식평가액·예수금·손익."""
    if not _ls_login_or_none():
        return "❌ LS증권 로그인 실패. 잠시 후 다시 시도해 주세요."

    try:
        import ls_client
        summary = ls_client.get_portfolio_summary()
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
    names      = summary.get("holdings_names", "")

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
    if names:
        lines.append(f"  {names}")
    return "\n".join(lines)


def handle_held(args: list) -> str:
    """보유 종목 + 평균가/손절가/수익률 (held_stock_record와 결합)."""
    if not _ls_login_or_none():
        return "❌ LS증권 로그인 실패. 잠시 후 다시 시도해 주세요."

    try:
        import ls_client
        balance = ls_client.get_balance()
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
        stop = held.get(code, {}).get("stop_loss_price", 0)

        lines.append(f"\n{emoji} {name}({code}) {unit}U {qty:,}주")
        lines.append(f"  평균가: {int(avg):,}원 → 현재: {int(cur):,}원")
        lines.append(f"  수익률: {sign}{pnl_pct:.2f}% ({sign}{pnl_amt:,}원)")
        if stop > 0:
            lines.append(f"  손절가: {stop:,}원")

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
