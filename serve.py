#!/usr/bin/env python3
"""사이트 점검 수집 결과 웹 대시보드 진입점.

사용법:
    python serve.py                       # http://127.0.0.1:8000
    python serve.py --port 9000
    python serve.py --host 0.0.0.0 --port 8000   # 외부 접속 허용
"""
from __future__ import annotations

import argparse

from src.webserver import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="사이트 점검 수집 웹 대시보드")
    parser.add_argument("--host", default="127.0.0.1", help="바인딩 호스트 (기본 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="포트 (기본 8000)")
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
