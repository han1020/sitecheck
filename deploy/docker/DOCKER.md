# Docker 배포 가이드 (CentOS 7 등 구형 서버용)

CentOS 7처럼 glibc가 낡아 Playwright Chromium이 직접 실행되지 않는 서버를 위한
컨테이너 배포 방법입니다. 구조는 기존 venv+systemd 방식과 동일합니다:

| 구성 | venv 방식 | Docker 방식 |
|------|-----------|-------------|
| 웹 대시보드 상주 | `sitecheck-web.service` | `docker compose up -d web` (restart 정책으로 상주) |
| 화/금 13:00 수집 | `sitecheck-collect.timer` | 동일한 timer가 `docker compose run --rm collect` 호출 |
| 데이터 | `output/`, `logs/` | 호스트 디렉토리를 볼륨 마운트 (동일 경로) |

## 0. 사전 확인

```bash
docker --version           # 20.10+ 이면 OK
docker compose version     # compose 플러그인 확인
```

`docker compose`가 없으면 (docker-ce 저장소 기준):
```bash
sudo yum install docker-compose-plugin
```
그래도 안 되면 standalone `docker-compose` 바이너리를 설치하고, 아래 명령과
`sitecheck-collect.service`의 `docker compose`를 `docker-compose`로 바꿔 읽으세요.

## 1. 코드 배치 + 이미지 빌드

```bash
sudo git clone https://github.com/han1020/sitecheck.git /opt/siteCheck
cd /opt/siteCheck

# 컨테이너가 쓸 데이터 디렉토리 (볼륨 마운트 대상)
mkdir -p output logs

sudo docker compose build     # 최초 빌드는 torch/Chromium 때문에 10분+ 소요
```

## 2. 웹 대시보드 시작

```bash
sudo docker compose up -d web
# http://<서버IP>:8000 접속 → '지금 수집' 한 번 눌러 동작 확인
```

`restart: unless-stopped`라 부팅 시 자동 시작됩니다 (docker.service가 enable 되어 있어야 함:
`sudo systemctl enable docker`).

## 3. 수집 1회 수동 실행 (테스트)

```bash
sudo docker compose run --rm collect
```

## 4. 자동 주기 수집 (매주 화/금 13:00)

```bash
# 서버 시간대 확인 (타이머는 로컬 시간 기준)
timedatectl
sudo timedatectl set-timezone Asia/Seoul

sudo cp deploy/docker/sitecheck-collect.service /etc/systemd/system/
sudo cp deploy/docker/sitecheck-collect.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sitecheck-collect.timer

# 확인
systemctl list-timers sitecheck-collect.timer
journalctl -u sitecheck-collect.service -f
```

## 5. 방화벽

```bash
sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
```

## 코드 업데이트 시

```bash
cd /opt/siteCheck
sudo git pull
sudo docker compose build
sudo docker compose up -d web   # 새 이미지로 재시작
```

## ⚠️ 보안 주의

기존 가이드와 동일합니다 — 대시보드에 **인증이 없으니** 사내망에서만 쓰고,
외부 노출이 필요하면 Nginx Basic Auth나 VPN을 앞단에 두세요 (deploy/DEPLOY.md 참고).
