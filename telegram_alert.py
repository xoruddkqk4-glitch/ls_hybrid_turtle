# telegram_alert.py
# 텔레그램 알림 단일 모듈
#
# 모든 알림은 이 모듈의 SendMessage()를 통해서만 발송한다.
# .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 없으면
# 콘솔(화면)에만 출력하고 넘어간다.
#
# 함수 목록:
#   SendMessage(msg)              — 관리자(.env의 TELEGRAM_CHAT_ID)에게 알림 발송
#   send_reply(chat_id, text)     — 임의의 채팅방에 답장 발송 (긴 글 자동 분할)
#   get_updates(offset, timeout)  — 봇이 받은 새 메시지 목록 조회 (long polling)
#
# 사용법:
#   from telegram_alert import SendMessage
#   SendMessage("삼성전자 진입 완료: 10주 @75,000원")

import os
import requests
from dotenv import load_dotenv

load_dotenv()


def SendMessage(msg: str) -> bool:
    """텔레그램 봇으로 메시지를 발송한다.

    발송 실패 시 1회 재시도 후 포기한다 (프로그램이 멈추지 않음).
    토큰/채팅ID가 없으면 화면에 메시지를 출력하고 False를 반환한다.

    Args:
        msg: 보낼 메시지 내용 (문자열)

    Returns:
        True:  발송 성공
        False: 발송 실패 또는 설정 없음
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id   = os.getenv("TELEGRAM_CHAT_ID",   "").strip()

    # 설정이 없으면 화면 출력으로 대체
    if not bot_token or not chat_id:
        print(f"[텔레그램 설정 없음] {msg}")
        return False

    url     = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}

    # 최대 2번 시도 (실패 시 1회 재시도)
    for attempt in range(1, 3):
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                return True
            print(f"[텔레그램] 발송 실패 ({attempt}/2회): HTTP {response.status_code}")
        except requests.RequestException as e:
            print(f"[텔레그램] 발송 오류 ({attempt}/2회): {e}")

    # 2번 모두 실패
    print(f"[텔레그램] 최종 실패, 메시지 손실: {msg}")
    return False


# ─────────────────────────────────────────
# 텔레그램 봇 양방향 통신 (메시지 받기 / 답장 발송)
# ─────────────────────────────────────────

def get_updates(offset: int, timeout: int = 10) -> list:
    """텔레그램 봇이 받은 새 메시지 목록을 가져온다 (long polling 방식).

    텔레그램 Bot API의 getUpdates를 호출해 사용자가 봇에게 보낸 메시지를 받는다.
    long polling 방식이라 timeout 초 동안 새 메시지가 올 때까지 서버에서 대기한다.

    네트워크 오류·HTTP 오류·JSON 파싱 오류는 모두 빈 리스트로 반환한다.
    데몬이 죽지 않도록 예외를 삼키며, 호출자는 빈 리스트를 "메시지 없음"과
    동일하게 처리하면 된다.

    Args:
        offset:  마지막 처리한 update_id + 1 (이 값보다 작은 update는 받지 않음).
                 0을 넘기면 봇이 받은 모든 메시지를 가져온다.
        timeout: long polling 대기 시간 (초). 기본 10초.
                 SIGTERM 빠른 응답을 위해 너무 길게 잡지 않는다.

    Returns:
        update 딕셔너리 리스트.
        예: [{"update_id": 123,
              "message": {"chat": {"id": 12345}, "text": "/help",
                          "from": {"username": "..."}, ...}}, ...]
        오류 또는 메시지 없음 시 빈 리스트 [].
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        print("[텔레그램] getUpdates: TELEGRAM_BOT_TOKEN 미설정 → 빈 리스트 반환")
        return []

    url    = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "timeout": timeout}

    try:
        # requests timeout은 long polling timeout보다 살짝 길게 (네트워크 지연 여유)
        response = requests.get(url, params=params, timeout=timeout + 5)
        if response.status_code != 200:
            # 본문 일부만 잘라서 노출 (긴 HTML 응답 등)
            print(
                f"[텔레그램] getUpdates HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
            return []

        data = response.json()
        if not data.get("ok"):
            print(f"[텔레그램] getUpdates 응답 오류: {data}")
            return []

        return data.get("result", [])

    except requests.RequestException as e:
        # 네트워크 끊김·타임아웃 — 데몬 보호 위해 빈 리스트 반환
        print(f"[텔레그램] getUpdates 네트워크 오류: {e}")
        return []
    except Exception as e:
        # JSON 파싱 오류 등 예상 못한 예외
        print(f"[텔레그램] getUpdates 예외: {e}")
        return []


def send_reply(chat_id, text: str, max_chunk: int = 3500) -> bool:
    """특정 채팅방에 답장을 발송한다 (긴 글 자동 분할).

    SendMessage()와 달리 chat_id를 명시적으로 받아 임의의 사용자에게도 답장
    가능하다. 텔레그램 메시지 한도(4096자)를 넘는 긴 응답은 자연 줄바꿈에서
    분할해 여러 메시지로 나누어 보낸다.

    Args:
        chat_id:   텔레그램 채팅 ID (정수 또는 문자열 모두 허용)
        text:      보낼 메시지 본문
        max_chunk: 한 메시지당 최대 글자 수 (기본 3500자, 안전마진 포함)

    Returns:
        모든 청크가 성공적으로 발송되면 True. 하나라도 실패하면 False.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        # 토큰이 없으면 콘솔 출력으로 대체 (개발/테스트 환경 대비)
        preview = text[:80].replace("\n", " ")
        print(f"[텔레그램 설정 없음] 답장 → chat_id={chat_id}: {preview}...")
        return False

    chunks = _split_for_telegram(text, max_chunk)
    url    = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        sent = False
        # 발송 실패 시 1회 재시도 (총 2번 시도)
        for attempt in range(1, 3):
            try:
                response = requests.post(url, json=payload, timeout=5)
                if response.status_code == 200:
                    sent = True
                    break
                print(
                    f"[텔레그램] 답장 청크 {i}/{len(chunks)} 발송 실패 "
                    f"({attempt}/2회): HTTP {response.status_code}"
                )
            except requests.RequestException as e:
                print(
                    f"[텔레그램] 답장 청크 {i}/{len(chunks)} 오류 "
                    f"({attempt}/2회): {e}"
                )
        if not sent:
            all_ok = False

    return all_ok


def _split_for_telegram(text: str, max_chunk: int) -> list:
    """텔레그램 메시지 길이 한도를 맞추기 위해 텍스트를 청크로 분할한다.

    가능하면 줄바꿈(\\n)에서 자연스럽게 나누고, 한 줄 자체가 max_chunk보다
    길면 글자 단위로 강제 분할한다.

    Args:
        text:      원본 텍스트
        max_chunk: 한 청크당 최대 글자 수

    Returns:
        청크 문자열 리스트.
        텍스트가 max_chunk 이하이면 원본 그대로 1개만 반환.
    """
    if len(text) <= max_chunk:
        return [text]

    chunks = []
    buf    = ""
    for line in text.split("\n"):
        # 현재 buf에 이 line을 추가하면 한도를 넘는 경우 → buf를 확정해 청크로
        if len(buf) + len(line) + 1 > max_chunk:
            if buf:
                chunks.append(buf.rstrip("\n"))
                buf = ""
            # 한 줄 자체가 한도보다 길면 글자 단위로 강제 분할
            while len(line) > max_chunk:
                chunks.append(line[:max_chunk])
                line = line[max_chunk:]
        buf += line + "\n"

    # 마지막 buf가 남아있으면 추가
    if buf.strip():
        chunks.append(buf.rstrip("\n"))

    return chunks
