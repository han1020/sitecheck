# digest 고정: 태그 갱신으로 인한 전체 레이어 재빌드(+의존성 재다운로드) 방지
# 2026-08-31 기준 python:3.11-slim (3.11.16-slim-trixie). 의도적으로 올릴 때만 digest 갱신.
FROM python:3.11-slim@sha256:1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# easyocr가 끌어오는 torch는 기본 휠이 CUDA 포함(2GB+)이라 CPU 휠을 먼저 고정
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8000"]
