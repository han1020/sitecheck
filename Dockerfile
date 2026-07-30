FROM python:3.11-slim

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# easyocr가 끌어오는 torch는 기본 휠이 CUDA 포함(2GB+)이라 CPU 휠을 먼저 고정
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8000"]
