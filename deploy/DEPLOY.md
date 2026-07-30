# 리눅스 서버 배포 가이드 (웹 대시보드)

사이트 점검 수집 웹 대시보드(`serve.py`)를 리눅스 서버에 상주 실행하는 방법입니다.
경로/계정은 예시(`/opt/siteCheck`, `sitecheck`)이니 환경에 맞게 바꾸세요.

---

## 1. 코드 + 가상환경 준비

```bash
# 전용 계정 (선택, root 비권장)
sudo useradd -r -m -d /opt/siteCheck sitecheck

# 코드 배치 (git clone 또는 복사)
sudo -u sitecheck git clone <repo-url> /opt/siteCheck
cd /opt/siteCheck

# 가상환경 + 의존성
sudo -u sitecheck python3 -m venv .venv
sudo -u sitecheck .venv/bin/pip install -U pip
sudo -u sitecheck .venv/bin/pip install -r requirements.txt
```

## 2. Playwright 브라우저 설치 (★ 빠지면 '지금 수집' 에러)

서비스 파일과 **같은 경로 변수**로 설치해야 합니다.

```bash
# 브라우저 바이너리를 프로젝트 안(.playwright)에 고정 설치
sudo -u sitecheck PLAYWRIGHT_BROWSERS_PATH=/opt/siteCheck/.playwright \
    .venv/bin/playwright install chromium

# OS 시스템 라이브러리 (root 권한 필요)
sudo .venv/bin/playwright install-deps
# 위 명령이 배포판에서 안 되면, 안내되는 apt/dnf 패키지를 직접 설치
```

## 3. 동작 확인 (서비스 등록 전 1회 수동 실행)

```bash
sudo -u sitecheck PLAYWRIGHT_BROWSERS_PATH=/opt/siteCheck/.playwright \
    .venv/bin/python serve.py --host 0.0.0.0 --port 8000
# 브라우저에서 http://<서버IP>:8000 접속 → '지금 수집' 한 번 눌러 확인 후 Ctrl+C
```

## 4. systemd 서비스 등록

```bash
# 서비스 파일 복사 (먼저 User/경로/포트를 환경에 맞게 편집)
sudo cp deploy/sitecheck-web.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now sitecheck-web    # 등록 + 즉시 시작 + 부팅 자동시작

# 상태 / 로그
systemctl status sitecheck-web
journalctl -u sitecheck-web -f
```

재시작/중지:
```bash
sudo systemctl restart sitecheck-web
sudo systemctl stop sitecheck-web
```

## 5. 방화벽 (포트 열기)

```bash
# ufw (Ubuntu)
sudo ufw allow 8000/tcp
# firewalld (RHEL/CentOS)
sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
```

---

## ⚠️ 보안 주의

- 이 대시보드는 **인증이 없습니다.** 접속한 누구나 `지금 수집` 실행·결과 열람이 가능합니다.
- **사내망/신뢰 네트워크에서만** 사용하세요. 공인 IP로 인터넷에 직접 노출 금지.
- 외부 접근이 필요하면 앞단에 **Nginx 리버스 프록시 + Basic Auth** 또는 **VPN**을 두세요.

### (선택) Nginx Basic Auth 예시

```nginx
server {
    listen 80;
    server_name sitecheck.example.com;
    location / {
        auth_basic "SiteCheck";
        auth_basic_user_file /etc/nginx/.htpasswd;   # htpasswd 로 생성
        proxy_pass http://127.0.0.1:8000;
    }
}
```
이 경우 `serve.py`는 `--host 127.0.0.1`로 띄워 Nginx만 접근하게 하세요.

---

## 6. 자동 주기 수집 (매주 화/금 13:00) — systemd timer

웹의 `지금 수집` 버튼을 사람이 누르지 않아도, 정해진 시각에 자동 수집되게 합니다.
`collect.py`는 웹의 '지금 수집'과 동일 파이프라인이라 결과가 **감지 목록 + 엑셀뷰** 양쪽에
바로 반영됩니다.

### 6-1. 서버 시간대부터 확인 (★ 중요)

타이머의 `13:00`은 **서버 로컬 시간** 기준입니다. 한국 시간으로 돌리려면:

```bash
timedatectl                              # 현재 시간대 확인
sudo timedatectl set-timezone Asia/Seoul # UTC 등으로 되어 있으면 변경
```

### 6-2. 서비스 + 타이머 등록

```bash
# (서비스 파일의 User/경로를 환경에 맞게 편집 후)
sudo cp deploy/sitecheck-collect.service /etc/systemd/system/
sudo cp deploy/sitecheck-collect.timer   /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now sitecheck-collect.timer   # 타이머만 enable (서비스 X)
```

> 등록하는 건 **`.timer`** 입니다. `.service`는 타이머가 시각이 되면 자동 호출하므로
> 직접 enable/start 하지 않습니다.

### 6-3. 확인

```bash
systemctl list-timers sitecheck-collect.timer   # 다음 실행 예정 시각(NEXT) 확인
journalctl -u sitecheck-collect.service -f       # 수집 로그

# 지금 한 번 강제로 돌려보기 (테스트)
sudo systemctl start sitecheck-collect.service
```

스케줄 변경은 `sitecheck-collect.timer`의 `OnCalendar=` 한 줄만 고치고
`sudo systemctl daemon-reload && sudo systemctl restart sitecheck-collect.timer` 하면 됩니다.
예) 매일 오전 9시·오후 2시 → `OnCalendar=*-*-* 09,14:00:00`

---

## 정리: 등록되는 systemd 유닛

| 유닛 | 역할 | enable 대상 |
|------|------|-------------|
| `sitecheck-web.service`     | 웹 대시보드 상주 | ✅ `enable --now` |
| `sitecheck-collect.timer`   | 화/금 13:00 트리거 | ✅ `enable --now` |
| `sitecheck-collect.service` | 수집 1회 실행 | ❌ (타이머가 호출) |
