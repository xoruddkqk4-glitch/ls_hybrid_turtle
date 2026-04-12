# config.py
# 관심 종목 리스트 (lovely_stock_list) 상수 정의
# 진입·감시·주문 대상은 이 리스트에 포함된 종목만으로 한정한다.
# 리스트 밖 종목은 주문·상태 변경을 하지 않는다.

# 종목 식별자는 6자리 종목코드로 전역 통일한다.
# 예: 삼성전자 → "005930"

lovely_stock_list = {
    "005930": {"name": "삼성전자",       "market": "KOSPI"},
    "034020": {"name": "두산에너빌리티",  "market": "KOSPI"},
    "064350": {"name": "현대로템",        "market": "KOSPI"},
    "279570": {"name": "케이뱅크",        "market": "KOSPI"},
    "352820": {"name": "하이브",          "market": "KOSDAQ"},
    "373220": {"name": "LG에너지솔루션",  "market": "KOSPI"},
    "454910": {"name": "두산로보틱스",    "market": "KOSDAQ"},
}

# 종목 이름 빠른 조회용 함수
def get_stock_name(code: str) -> str:
    """종목코드로 종목 이름을 반환한다. 목록에 없으면 코드를 그대로 반환."""
    return lovely_stock_list.get(code, {}).get("name", code)
