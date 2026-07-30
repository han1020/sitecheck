"""이미지 공지(포스터) OCR 유틸 — EasyOCR 기반.

본문이 텍스트가 아니라 이미지 한 장으로만 된 공지(예: 한국투자증권 점검 안내)에서
글자를 읽어 기존 파서(extract_window / is_own_institution_notice)에 넘길 텍스트를 만든다.

- EasyOCR 미설치 시 조용히 비활성화(빈 문자열 반환) → 해당 공지는 종전대로 '검토 필요'로 남는다.
- Reader 로딩(~10초)·모델 다운로드는 최초 1회. 이후 프로세스 내에서 재사용(싱글턴).
- python.org macOS Python은 CA 번들이 없어 모델 다운로드가 SSL 오류를 내므로,
  certifi 번들을 SSL_CERT_FILE로 물려준다(이미 받아둔 경우엔 영향 없음).
"""
from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

_reader = None
_reader_lock = threading.Lock()
_disabled = False  # 로딩 실패 시 재시도하지 않도록


def _ensure_ca_bundle() -> None:
    """EasyOCR 모델 다운로드(urllib)가 인증서 검증에 실패하지 않도록 certifi 번들 지정."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass


def _get_reader():
    """EasyOCR Reader 싱글턴. 사용 불가하면 None."""
    global _reader, _disabled
    if _disabled:
        return None
    if _reader is not None:
        return _reader
    with _reader_lock:
        if _reader is not None:
            return _reader
        try:
            _ensure_ca_bundle()
            import easyocr  # torch 로딩이 무거우므로 지연 임포트
            logger.info("[ocr] EasyOCR Reader 로딩 중(최초 1회, ~10초)...")
            _reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
            logger.info("[ocr] EasyOCR Reader 준비 완료")
        except Exception as e:
            _disabled = True
            logger.warning(f"[ocr] EasyOCR 사용 불가(비활성화): {e}")
            return None
    return _reader


# 'HH.MM' / 'HH.MM시' 처럼 OCR이 콜론을 마침표로 읽는 경우를 시각으로 되돌린다.
# YYYY.MM.DD 같은 점 구분 날짜는 건드리지 않도록 앞뒤 숫자/점을 배제.
_TIME_DOT_RE = re.compile(r"(?<![\d.])([01]?\d|2[0-3])\.([0-5]\d)(?![\d.])")


def normalize(text: str) -> str:
    """OCR 텍스트를 파서가 먹기 좋게 가볍게 정규화."""
    if not text:
        return ""
    text = _TIME_DOT_RE.sub(r"\1:\2", text)          # 08.00 → 08:00
    text = text.replace("～", "~").replace("∼", "~")   # 물결표 통일
    return text


def is_available() -> bool:
    """OCR 사용 가능 여부(설치+로딩)."""
    return _get_reader() is not None


def ocr_image(path: str) -> str:
    """이미지 파일에서 텍스트를 읽어 정규화 문자열로 반환. 실패/미설치 시 ''."""
    reader = _get_reader()
    if reader is None or not path:
        return ""
    try:
        lines = reader.readtext(path, detail=0, paragraph=True)
    except Exception as e:
        logger.warning(f"[ocr] 인식 실패({path}): {e}")
        return ""
    return normalize("\n".join(lines))
