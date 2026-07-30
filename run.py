#!/usr/bin/env python3
"""사이트 점검 공지 수집기 진입점.

사용법:
    python run.py                # 1회 실행
    python run.py --headed       # 브라우저 창 띄움 (디버깅)
    python run.py --schedule     # 주기 실행 (기본 09:00, 14:00)
    python run.py --schedule --times 08:00,12:00,17:00
"""
from src.main import main

if __name__ == "__main__":
    main()
