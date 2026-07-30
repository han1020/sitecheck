#!/usr/bin/env python3
"""수집 1회 실행 (엑셀 + JSON 저장).

systemd timer / cron 등에서 정해진 시각에 호출하는 진입점.
웹 대시보드의 '지금 수집'과 동일한 파이프라인을 사용하므로,
실행 결과가 웹의 '감지 목록'/'엑셀뷰' 양쪽에 그대로 반영된다.

사용법:
    python collect.py
"""
from __future__ import annotations

import logging
import sys

from src.webserver import collect_once_sync


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    try:
        collect_once_sync()
    except Exception:
        logging.getLogger("collect").exception("수집 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
