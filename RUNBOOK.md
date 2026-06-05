# RUNBOOK — ADA 2026 포스터 Q&A 시스템

> 이 파일은 **나중 대화에서 Claude가 즉시 상황을 파악**하도록 만든 운영/상태 문서입니다.
> 문제가 생겨 도움을 요청할 때, Claude는 이 파일부터 읽으면 됩니다.
> ⚠️ 이 repo는 **공개(public)** 입니다 — 토큰/키/시크릿은 여기 적지 않습니다. (위치만 참조)

最終 업데이트: 2026-06-05

---

## 1. 한 줄 요약
포스터 방문자가 **웹 채팅(앱 설치 X)** 으로 질문 → **발표자가 텔레그램으로 받아 밀어서 답장** → 답이 방문자 웹에 실시간 표시. **모든 대화는 Google 시트에 영구 기록.** 전부 무료 운영.

## 2. 라이브 주소 / 빠른 상태 점검
- 방문자 채팅: https://ada2026-qa.onrender.com
- 헬스체크: https://ada2026-qa.onrender.com/healthz  (정상이면 `ok`)
- 웹훅 진단/등록(온디맨드): `GET /admin/setup?token=<TELEGRAM_TOKEN>` (token은 봇 토큰)

```bash
# 서버 살아있나
curl -s -o /dev/null -w "%{http_code}\n" https://ada2026-qa.onrender.com/healthz   # 200 기대
# 텔레그램 웹훅 상태 (url 등록됐는지, last_error 없는지)
curl -s "https://api.telegram.org/bot<TELEGRAM_TOKEN>/getWebhookInfo"
```

## 3. 데이터 흐름
```
방문자(브라우저)  ──POST /api/session, /api/message──▶  중계서버(Render, FastAPI)
        ▲                                                  │
        │ long-poll /api/poll (발표자 답장 실시간 수신)      ├─▶ 텔레그램(발표자): sendMessage
        └──────────────────────────────────────────────────┤   발표자가 "밀어서 답장" → 웹훅 POST /tg/<TG_SECRET>
                                                            └─▶ Google 시트(Apps Script): 모든 메시지 1행씩 기록
```

## 4. 파일 구조
- `app.py` — FastAPI 중계 서버 (전부 여기 있음)
- `static/index.html` — 방문자 채팅 페이지 (영어 UI, 앱 설치 X)
- `requirements.txt` — fastapi, uvicorn[standard], httpx
- `render.yaml` — Render Blueprint (배포 설정)
- `gen_qr.py` — 포스터 QR 생성 (바탕화면 저장)
- `gen_manual.py` — 운영 매뉴얼 .docx 생성 (바탕화면 저장)
- `.github/workflows/keepalive.yml` — 5분 핑 백업 (repo variable `PING_URL` 사용)
- `DEPLOY_CHECKLIST.md`, `POSTER.md`, `README.md` — 보조 문서

## 5. 인프라 / 어디에 뭐가 있나 (전부 무료)
| 항목 | 위치 / 식별자 |
|---|---|
| Render 웹서비스 | 이름 `ada2026-qa`, service id `srv-d8hfral8nd3s73cabqhg`, 대시보드 https://dashboard.render.com/web/srv-d8hfral8nd3s73cabqhg |
| GitHub repo (공개) | https://github.com/go2god4u-glitch/ada2026-poster-qa (기본 브랜치 `master`) |
| Google 시트 | "ADA2026 QnA Log", id `1w5EhKqwFg6XzOaKsJdR6XXcW4dF9WuRzEbwQtZ3fUyA`, 탭 `Q&A` |
| UptimeRobot | `/healthz` 5분 핑 (서버 슬립 방지). 학회 후 Pause 권장 |
| Telegram | 기존 알림봇 재사용 (발표자가 수신/답장) |

## 6. 배포 방법 (중요)
- **Render Auto-Deploy는 꺼져 있음** → `git push`만으론 재배포 안 됨.
- **Deploy Hook으로 배포**: `curl -X POST "<RENDER_DEPLOY_HOOK_URL>"`
  - Deploy Hook URL(키 포함)은 **공개 금지** → Claude 개인 메모리 `project_ada2026_chat.md` 에 보관. 없으면 사용자에게 요청(Render → Settings → Deploy Hook).
- 빌드 1~3분. 배포 확인: `/admin/setup?token=...` 또는 `/healthz`.
- 코드 수정 흐름: 로컬 수정 → `git commit && git push` → Deploy Hook POST → 빌드 대기 → 헬스/웹훅 확인.

## 7. 환경변수 (값은 Render 대시보드 Environment 탭에 있음, 여기 적지 않음)
- `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` — 텔레그램 봇/수신 채팅
- `WEBHOOK_SECRET` — Render 자동생성. 코드에서 **sha256 hex(`TG_SECRET`)로 변환**해 텔레그램 secret_token/URL 경로에 사용 (자동생성 값에 특수문자 섞여 거부되는 문제 회피)
- `SHEETS_WEBHOOK_URL` — Google Apps Script 웹앱 `/exec` URL (시트 기록용)
- `RENDER_EXTERNAL_URL` — Render 자동 주입 (BASE_URL로 사용, 웹훅 자동 등록)

## 8. 그동안 겪은 문제 & 해결 (재발 시 참고)
- **첫 접속 50초 지연** = Render 무료 슬립. UptimeRobot 5분 핑으로 해결. 부스 중엔 방문자 트래픽이 깨움.
- **웹훅 등록 실패 "secret token contains unallowed characters"** = Render 자동생성 WEBHOOK_SECRET에 특수문자. → `TG_SECRET = sha256(WEBHOOK_SECRET)` hex로 변환해 해결 (app.py에 반영됨).
- **한글 입력 시 500 (UnicodeDecodeError 0xc7)** = 서버 버그 아님. Windows `curl`이 cp949로 보낸 탓. **실제 브라우저/`httpx`/Python(UTF-8)은 정상.** 테스트는 Python으로 UTF-8 인코딩해서 보낼 것.
- **재배포해도 옛 코드** = Auto-Deploy 꺼져 있어서. Deploy Hook으로 트리거할 것.
- **재시작 시 답장 라우팅** = sqlite(`data.db`)는 휘발성. 그래서 전달 메시지 본문에 `#s=CODE`를 박고, 웹훅에서 매핑 없으면 코드로 세션 복구(self-heal). `_ensure_session()` 참고.

## 9. 데이터 보기 / 정리
- 보기: Google Drive → "ADA2026 QnA Log" → `Q&A` 탭. 열 = Time, Session, Name, Company, Email, Sender(Visitor/Presenter), Message.
- 사람별 정리: 데이터 → 정렬을 `Name` 또는 `Session` 기준으로. 리드는 Email 채워진 행 필터.
- 백업: 파일 → 다운로드 → Excel/CSV.
- 시트 기록 안 되면: `SHEETS_WEBHOOK_URL` 설정 확인 + Apps Script 웹앱 배포 상태(액세스 "모든 사용자") 확인.

## 10. 현재 상태 / 남은 일
- [x] 웹챗 ↔ 텔레그램 왕복 검증 완료
- [x] Google 시트 자동 기록 검증 완료 (한글 정상)
- [x] UptimeRobot 5분 핑 설정 완료 + GH Actions 백업
- [ ] (선택) 학회(6/5~8) 종료 후 UptimeRobot Pause — 자동화 원하면 `winddown` 워크플로 추가 가능(UptimeRobot API 키 필요)
- [ ] (정리) Google 시트의 테스트 행 삭제 (TEST00 등)

## 11. 빠른 트러블슈팅 순서
1. `/healthz` 200인가? → 아니면 콜드스타트, 잠깐 기다리거나 Deploy Hook로 재배포.
2. `getWebhookInfo`에 url 있고 last_error 없나? → 없으면 `/admin/setup?token=...` 호출해 재등록.
3. 시트에 안 쌓이나? → `SHEETS_WEBHOOK_URL` env 확인.
4. 코드 고쳤는데 반영 안 됨? → Deploy Hook POST 했는지 확인.
5. 한글 테스트가 500? → 테스트 클라이언트 인코딩 문제. Python UTF-8로 보낼 것.
