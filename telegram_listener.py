# telegram_listener.py
# 텔레그램 봇 데몬 (메인 루프)
#
# 24시간 백그라운드로 실행되며 사용자가 봇에게 보낸 명령을 받아 처리한다.
#
# 동작 흐름:
#   1. getUpdates로 새 메시지 폴링 (long polling 10초)
#   2. 권한 검증 (chat.id == TELEGRAM_CHAT_ID)
#   3. telegram_commands.dispatch()로 명령 처리
#   4. send_reply로 답장
#   5. 처리한 update_id를 telegram_offset.json에 저장 (재시작 시 중복 처리 방지)
#
# 안전 장치:
#   - 텔레그램 API 즉시 실패 시 지수 백오프 (1·2·4·8·16·32·60초)
#   - 권한 없는 사용자: 거부 응답 + 관리자에게 침입 알림 (1분 dedup)
#   - SIGTERM/SIGINT: 종료 플래그 설정 → 다음 루프에서 깔끔히 종료
#   - 백오프 sleep은 1초씩 쪼개어 SIGTERM 빠른 응답
#   - 명령 처리 예외: telegram_commands.dispatch가 자체 try-except로 처리
#
# 실행 방법:
#   로컬:    python telegram_listener.py
#   AWS:    sudo systemctl start ls_telegram_listener
#           journalctl -u ls_telegram_listener -f

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime

import pytz
from dotenv import load_dotenv

from telegram_alert import get_updates, send_reply, SendMessage
from telegram_commands import dispatch

# .env 환경변수 로드
load_dotenv()

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR         = os.path.dirname(os.path.abspath(__file__))
_OFFSET_PATH = os.path.join(_DIR, "telegram_offset.json")
_LOG_PATH    = os.path.join(_DIR, "logs", "telegram_listener.log")

# 폴링 설정
_POLL_TIMEOUT = 10  # long polling 대기 시간 (초)

# 백오프 (텔레그램 API 즉시 실패 시 지수적 대기)
_BACKOFF_SECONDS = [1, 2, 4, 8, 16, 32, 60]

# 침입 알림 dedup (chat_id → 마지막 알림 unix timestamp)
_LAST_INTRUSION_ALERT: dict = {}
_INTRUSION_DEDUP_SECONDS = 60  # 같은 chat_id 1분 내 중복 알림 차단

# 종료 플래그 (SIGTERM/SIGINT 수신 시 True)
_SHUTDOWN = False


# ─────────────────────────────────────────
# offset 영속화
# ─────────────────────────────────────────

def _load_offset() -> int:
    """telegram_offset.json에서 다음 처리할 update_id를 로드.

    파일이 없거나 읽기 실패 시 0 반환 (모든 메시지 처리 — 첫 실행 시).
    """
    if not os.path.exists(_OFFSET_PATH):
        return 0
    try:
        with open(_OFFSET_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("offset", 0))
    except Exception as e:
        print(f"[listener] offset 파일 읽기 오류: {e} → 0부터 시작")
        return 0


def _save_offset(update_id: int):
    """다음 polling에 사용할 offset 저장 (update_id + 1).

    텔레그램 API 규칙: offset 보다 작은 update는 받지 않음.
    """
    try:
        with open(_OFFSET_PATH, "w", encoding="utf-8") as f:
            json.dump({"offset": update_id + 1}, f)
    except Exception as e:
        print(f"[listener] offset 저장 오류: {e}")


# ─────────────────────────────────────────
# 권한 검증 + 침입 알림
# ─────────────────────────────────────────

def _is_authorized(message: dict) -> tuple:
    """메시지의 chat.id가 .env의 TELEGRAM_CHAT_ID와 일치하는지 확인.

    Returns:
        (authorized, chat_id_str, username)
            authorized:  True/False
            chat_id_str: 메시지 보낸 채팅 ID (문자열)
            username:    보낸 사람 username (없으면 first_name → "unknown")
    """
    chat        = message.get("chat") or {}
    chat_id_str = str(chat.get("id", ""))

    user        = message.get("from") or {}
    username    = user.get("username") or user.get("first_name") or "unknown"

    expected = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    authorized = bool(expected) and chat_id_str == expected
    return authorized, chat_id_str, username


def _alert_intrusion(chat_id: str, username: str, text: str):
    """권한 없는 사용자가 명령 보냈을 때 관리자에게 알림.

    같은 chat_id는 1분 내 중복 알림 차단 (관리자 폭주 방지).
    텔레그램 발송 실패해도 데몬은 계속 동작.
    """
    now  = time.time()
    last = _LAST_INTRUSION_ALERT.get(chat_id, 0)
    if now - last < _INTRUSION_DEDUP_SECONDS:
        return  # dedup — 1분 내 같은 chat_id 재알림 안 함

    _LAST_INTRUSION_ALERT[chat_id] = now

    msg = (
        f"⚠️ 권한 없는 사용자 침입 시도\n"
        f"chat_id: {chat_id}\n"
        f"username: @{username}\n"
        f"메시지: {text[:200]}"
    )
    print(f"[listener] {msg}")
    try:
        SendMessage(msg)
    except Exception as e:
        print(f"[listener] 침입 알림 발송 오류: {e}")


# ─────────────────────────────────────────
# 명령 처리 로그
# ─────────────────────────────────────────

def _log_command(chat_id: str, username: str, text: str, result: str, success: bool):
    """명령 처리 결과를 logs/telegram_listener.log에 기록한다.

    형식:
        시각 | OK/FAIL | chat_id | @username | 명령 | 결과(100자)
    """
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        ts     = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
        status = "OK" if success else "FAIL"
        # 결과는 100자로 자르고 줄바꿈은 공백으로 (한 줄 로그)
        result_short = (result or "").replace("\n", " ")[:100]
        line = f"{ts} | {status} | {chat_id} | @{username} | {text} | {result_short}\n"
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[listener] 로그 기록 오류: {e}")


# ─────────────────────────────────────────
# 시그널 처리 (SIGTERM/SIGINT)
# ─────────────────────────────────────────

def _signal_handler(signum, frame):
    """SIGTERM/SIGINT 수신 시 종료 플래그를 설정.

    long polling 중이라도 다음 루프 진입 시 즉시 종료된다.
    """
    global _SHUTDOWN
    print(f"\n[listener] 시그널 {signum} 수신 → 종료 준비")
    _SHUTDOWN = True


# ─────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────

def _interruptible_sleep(seconds: int):
    """SIGTERM에 빠르게 반응하기 위해 1초씩 쪼개어 sleep."""
    for _ in range(seconds):
        if _SHUTDOWN:
            return
        time.sleep(1)


def _process_update(update: dict, offset: int) -> int:
    """update 1건을 처리하고 새 offset을 반환한다.

    Returns:
        새 offset (다음 polling에 사용할 값)
    """
    update_id = update.get("update_id", 0)
    message   = update.get("message") or update.get("edited_message")

    if not message:
        # 메시지 없는 update (예: callback query, channel post 등) — 그냥 offset만 갱신
        _save_offset(update_id)
        return update_id + 1

    text = (message.get("text") or "").strip()
    authorized, chat_id_str, username = _is_authorized(message)

    if not authorized:
        # 권한 없는 사용자 — 거부 응답 + 관리자 알림
        send_reply(
            chat_id_str,
            "❌ 권한이 없습니다. 등록되지 않은 사용자입니다."
        )
        _alert_intrusion(chat_id_str, username, text)
        _log_command(chat_id_str, username, text, "권한 없음", success=False)
        _save_offset(update_id)
        return update_id + 1

    # 권한 OK — 명령 처리
    try:
        reply = dispatch(text)
        send_reply(chat_id_str, reply)
        _log_command(chat_id_str, username, text, reply, success=True)
    except Exception as e:
        # dispatch 자체에 try-except가 있지만 만약을 위한 이중 안전장치
        err_msg = f"❌ 처리 오류: {e}"
        try:
            send_reply(chat_id_str, err_msg)
        except Exception:
            pass
        _log_command(chat_id_str, username, text, str(e), success=False)

    # offset은 처리 직후 항상 저장 (성공/실패 무관 — 같은 명령 무한 재처리 방지)
    _save_offset(update_id)
    return update_id + 1


def main():
    """데몬 메인 루프.

    종료 조건:
      - SIGTERM/SIGINT 수신 (_SHUTDOWN=True)
      - KeyboardInterrupt
    """
    global _SHUTDOWN

    # 시그널 핸들러 등록
    signal.signal(signal.SIGINT,  _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (AttributeError, ValueError):
        # Windows에서는 SIGTERM 없을 수도 있음 — 무시
        pass

    # 환경변수 검증
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id   = os.getenv("TELEGRAM_CHAT_ID",   "").strip()

    if not bot_token:
        print("[listener] ⚠️ TELEGRAM_BOT_TOKEN 미설정 — 데몬 종료")
        return
    if not chat_id:
        print("[listener] ⚠️ TELEGRAM_CHAT_ID 미설정 — 데몬 종료")
        return

    print(f"[listener] ✅ 텔레그램 봇 데몬 시작 (관리자 chat_id={chat_id})")

    # offset 로드
    offset = _load_offset()
    print(f"[listener] 시작 offset={offset}")

    # 백오프 인덱스 (텔레그램 API 즉시 실패 시 사용)
    backoff_idx = 0

    while not _SHUTDOWN:
        # long polling — 정상 시 timeout 동안 대기 후 반환
        start    = time.monotonic()
        updates  = get_updates(offset, timeout=_POLL_TIMEOUT)
        elapsed  = time.monotonic() - start

        # 응답이 너무 빨리 빈 채로 왔으면 텔레그램 API 즉시 실패로 추정
        # (정상 long polling이면 timeout 근처에서 반환됨)
        if not updates and elapsed < 2:
            wait_sec = _BACKOFF_SECONDS[min(backoff_idx, len(_BACKOFF_SECONDS) - 1)]
            print(f"[listener] 텔레그램 API 오류 추정 (응답 {elapsed:.1f}초) "
                  f"→ {wait_sec}초 후 재시도")
            _interruptible_sleep(wait_sec)
            backoff_idx = min(backoff_idx + 1, len(_BACKOFF_SECONDS) - 1)
            continue
        else:
            # 정상 응답 — 백오프 리셋
            backoff_idx = 0

        # 메시지 처리
        for update in updates:
            if _SHUTDOWN:
                break
            offset = _process_update(update, offset)

    print("[listener] 데몬 종료 완료")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[listener] KeyboardInterrupt — 종료")
