# ADA 2026 Poster Q&A - 실시간 양방향 채팅 중계 서버
# ------------------------------------------------------------------
# 구조: 관람객(웹 채팅, 앱설치 X)  <->  중계 서버(이 파일)  <->  발표자(텔레그램)
#   - 관람객: QR/링크로 웹페이지 접속 -> 이름/회사/이메일 입력 -> 질문 채팅
#   - 발표자: 텔레그램으로 질문 수신 -> 해당 메시지에 "답장(swipe-reply)" -> 관람객 화면에 실시간 표시
#
# 운영 정보 (배포/실행 환경):
#   - 배포: 무료 클라우드(Render 등). 시작 명령: uvicorn app:app --host 0.0.0.0 --port $PORT
#   - 필요 환경변수: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_SECRET
#     (RENDER_EXTERNAL_URL 은 Render가 자동 주입 / 다른 호스트면 BASE_URL 직접 설정)
#   - 텔레그램 웹훅은 앱 시작 시 자동 등록(setWebhook). 공개 URL이 있어야 동작.
#   - Render 무료는 약 15분 무접속 시 슬립 -> UptimeRobot/cron-job.org 로 /healthz 주기 핑(킵얼라이브) 권장.
#   - 디스크가 휘발성이라 재시작 시 매핑 DB가 날아갈 수 있음 -> 전달 메시지 본문에 세션코드(#s=XXXX)를
#     박아두고, 매핑이 없으면 답장 원문에서 코드를 복구해 라우팅이 self-heal 되도록 설계함.
# ------------------------------------------------------------------

import os
import re
import time
import hashlib
import sqlite3
import asyncio
import secrets
import html
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

# ---- 설정 --------------------------------------------------------
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.environ.get("WEBHOOK_SECRET", "change-me-please")
BASE_URL         = (os.environ.get("RENDER_EXTERNAL_URL")
                    or os.environ.get("BASE_URL", "")).rstrip("/")
DB_PATH          = os.environ.get("DB_PATH", "data.db")
TG_API           = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# 시크릿이 비었거나 placeholder면 웹훅을 fail-closed 처리(가짜 답장 주입 차단)
SECRET_OK = bool(WEBHOOK_SECRET) and WEBHOOK_SECRET != "change-me-please"

# 텔레그램 secret_token / URL 경로는 [A-Za-z0-9_-]만 허용.
# Render 자동생성 시크릿엔 특수문자가 섞일 수 있어, 해시(hex)로 변환해 항상 안전한 값 사용.
TG_SECRET = hashlib.sha256((WEBHOOK_SECRET or "").encode()).hexdigest()

HERE       = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "static", "index.html")

# 세션코드 패턴: #s=AB12CD
CODE_RE = re.compile(r"#s=([A-Z0-9]{6})")

# ---- DB ----------------------------------------------------------
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.executescript("""
CREATE TABLE IF NOT EXISTS sessions(
    id      TEXT PRIMARY KEY,      -- 세션코드 (6자 대문자/숫자)
    name    TEXT,
    company TEXT,
    email   TEXT,
    created REAL
);
CREATE TABLE IF NOT EXISTS messages(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    sender     TEXT,                -- 'visitor' | 'presenter'
    text       TEXT,
    ts         REAL
);
CREATE TABLE IF NOT EXISTS tgmap(
    tg_message_id INTEGER PRIMARY KEY,  -- 발표자에게 전달한 텔레그램 메시지 id
    session_id    TEXT
);
""")
_conn.commit()

_db_lock = asyncio.Lock()
# 세션별 long-poll 깨우기 이벤트
_pollers: dict[str, asyncio.Event] = {}

# 간단한 in-memory 레이트리밋 (IP별 토큰버킷) - 공개 QR 엔드포인트 남용 방지
_rate: dict[str, list[float]] = {}


def _rate_ok(key: str, limit: int, window: float) -> bool:
    now = time.time()
    bucket = [t for t in _rate.get(key, []) if now - t < window]
    if len(bucket) >= limit:
        _rate[key] = bucket
        return False
    bucket.append(now)
    _rate[key] = bucket
    return True


SID_RE = re.compile(r"^[A-Z0-9]{6}$")


def _ensure_session(sid: str, name: str = "(reconnected visitor)"):
    """세션 row 보장. 서버 재시작으로 DB가 비었어도 유효 코드면 복구."""
    _conn.execute(
        "INSERT OR IGNORE INTO sessions(id,name,company,email,created) VALUES(?,?,?,?,?)",
        (sid, name, "", "", time.time()))
    _conn.commit()


def _wake(session_id: str):
    ev = _pollers.get(session_id)
    if ev:
        ev.set()


async def db(fn):
    """sqlite 작업을 락으로 보호해 스레드풀에서 실행."""
    async with _db_lock:
        return await asyncio.to_thread(fn)


# ---- 텔레그램 ----------------------------------------------------
async def tg_send(text: str, reply_markup=None) -> int | None:
    """발표자 채팅으로 메시지 전송. 전송된 message_id 반환."""
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[warn] TELEGRAM_TOKEN/CHAT_ID 미설정 - 전송 생략")
        return None
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{TG_API}/sendMessage", json=payload)
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            print("[tg] sendMessage 실패:", data)
    except Exception as e:
        print("[tg] 전송 예외:", e)
    return None


# ---- 라이프사이클: 웹훅 등록 -------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not SECRET_OK:
        print("[startup] [WARN] WEBHOOK_SECRET not set / placeholder - webhook disabled (fail-closed). "
              "Set a unique WEBHOOK_SECRET in production.")
    if TELEGRAM_TOKEN and BASE_URL and SECRET_OK:
        hook = f"{BASE_URL}/tg/{TG_SECRET}"
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{TG_API}/setWebhook", json={
                    "url": hook,
                    "secret_token": TG_SECRET,
                    "allowed_updates": ["message"],
                    "drop_pending_updates": False,
                })
                print("[startup] setWebhook:", r.json())
        except Exception as e:
            print("[startup] setWebhook 예외:", e)
    else:
        print("[startup] BASE_URL 또는 TOKEN 없음 - 웹훅 등록 생략(로컬 테스트 모드)")
    yield


app = FastAPI(lifespan=lifespan)


# ---- 관람객용 API ------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(INDEX_HTML)


@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")


@app.get("/admin/setup")
async def admin_setup(token: str):
    """온디맨드 웹훅 등록 + 진단. 봇 토큰을 아는 사람만 호출 가능."""
    if not TELEGRAM_TOKEN or token != TELEGRAM_TOKEN:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    info: dict = {
        "base_url": BASE_URL or "(missing)",
        "secret_ok": SECRET_OK,
        "chat_id_set": bool(TELEGRAM_CHAT_ID),
    }
    if not (BASE_URL and SECRET_OK):
        info["registered"] = False
        info["reason"] = "BASE_URL 또는 WEBHOOK_SECRET 미설정"
        return info
    hook = f"{BASE_URL}/tg/{TG_SECRET}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{TG_API}/setWebhook", json={
                "url": hook,
                "secret_token": TG_SECRET,
                "allowed_updates": ["message"],
            })
            info["setWebhook"] = r.json()
            info["registered"] = bool(r.json().get("ok"))
    except Exception as e:
        info["registered"] = False
        info["error"] = str(e)
    return info


@app.post("/api/session")
async def create_session(req: Request):
    ip = req.client.host if req.client else "?"
    if not _rate_ok(f"sess:{ip}", limit=5, window=60):
        return JSONResponse({"error": "too many requests, please wait a moment"}, status_code=429)

    body = await req.json()
    name    = (body.get("name") or "").strip()[:80]
    company = (body.get("company") or "").strip()[:120]
    email   = (body.get("email") or "").strip()[:120]
    if not name or not company:
        return JSONResponse({"error": "name and company are required"}, status_code=400)

    now = time.time()

    def _ins():
        # 코드 충돌 시 재생성 (PRIMARY KEY IntegrityError)
        for _ in range(8):
            c = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
            try:
                _conn.execute(
                    "INSERT INTO sessions(id,name,company,email,created) VALUES(?,?,?,?,?)",
                    (c, name, company, email, now))
                _conn.commit()
                return c
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique session code")
    code = await db(_ins)

    email_line = f"\n✉️ {html.escape(email)}" if email else ""
    await tg_send(
        f"🆕 새 방문자\n"
        f"👤 <b>{html.escape(name)}</b>\n"
        f"🏢 {html.escape(company)}{email_line}\n"
        f"<i>방금 채팅을 시작했어요 — 곧 질문이 옵니다.</i>\n"
        f"<i>#s={code}</i>")
    return {"session_id": code}


@app.post("/api/message")
async def post_message(req: Request):
    ip = req.client.host if req.client else "?"
    if not _rate_ok(f"msg:{ip}", limit=20, window=60):
        return JSONResponse({"error": "too many requests, please slow down"}, status_code=429)

    body = await req.json()
    sid  = (body.get("session_id") or "").strip()
    text = (body.get("text") or "").strip()[:4000]
    if not sid or not text:
        return JSONResponse({"error": "session_id and text required"}, status_code=400)
    if not SID_RE.match(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    def _get():
        return _conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    sess = await db(_get)
    if not sess:
        # 서버 재시작으로 세션이 사라졌어도 방문자는 유효한 코드 보유 -> 복구해서 메시지 유실 방지
        def _recreate():
            _ensure_session(sid)
            return _conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        sess = await db(_recreate)

    now = time.time()

    def _ins():
        _conn.execute(
            "INSERT INTO messages(session_id,sender,text,ts) VALUES(?,?,?,?)",
            (sid, "visitor", text, now))
        _conn.commit()
    await db(_ins)

    # 발표자에게 전달 (세션코드를 본문에 박아 라우팅이 재시작에도 self-heal)
    mid = await tg_send(
        f"👤 <b>{html.escape(sess['name'])}</b> · {html.escape(sess['company'])}\n"
        f"\n"
        f"💬 {html.escape(text)}\n"
        f"\n"
        f"<i>↩️ 답하려면 이 메시지를 밀어서(swipe) 답장 · #s={sid}</i>")
    if mid is not None:
        def _map():
            _conn.execute("INSERT OR REPLACE INTO tgmap(tg_message_id,session_id) VALUES(?,?)",
                          (mid, sid))
            _conn.commit()
        await db(_map)
    return {"ok": True}


@app.get("/api/poll")
async def poll(session_id: str, after: int = 0):
    """발표자 답장만 long-poll. after 이후의 presenter 메시지를 반환."""
    def _q():
        rows = _conn.execute(
            "SELECT id,text,ts FROM messages "
            "WHERE session_id=? AND sender='presenter' AND id>? ORDER BY id",
            (session_id, after)).fetchall()
        return [{"id": r["id"], "text": r["text"], "ts": r["ts"]} for r in rows]

    # 이벤트를 먼저 등록/clear 한 뒤 조회 -> 조회와 대기 사이에 도착한 답장의 wake 유실 방지
    ev = _pollers.setdefault(session_id, asyncio.Event())
    ev.clear()
    msgs = await db(_q)
    if msgs:
        return {"messages": msgs}
    try:
        await asyncio.wait_for(ev.wait(), timeout=25)  # Render 요청 한도 아래로 유지
    except asyncio.TimeoutError:
        return {"messages": []}
    return {"messages": await db(_q)}


# ---- 텔레그램 웹훅 (발표자 답장 수신) ----------------------------
@app.post("/tg/{secret}")
async def telegram_webhook(secret: str, req: Request):
    if not SECRET_OK or secret != TG_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    # 텔레그램이 보내는 시크릿 헤더도 확인(있으면)
    hdr = req.headers.get("x-telegram-bot-api-secret-token")
    if hdr is not None and hdr != TG_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    update = await req.json()
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return {"ok": True}

    # 발표자 본인 채팅만 허용
    if str(msg.get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
        return {"ok": True}

    reply_to = msg.get("reply_to_message") or {}
    sid = None

    # 1) 매핑 테이블 우선
    if reply_to.get("message_id"):
        def _lookup():
            row = _conn.execute("SELECT session_id FROM tgmap WHERE tg_message_id=?",
                                (reply_to["message_id"],)).fetchone()
            return row["session_id"] if row else None
        sid = await db(_lookup)

    # 2) 매핑이 없으면 답장 원문에서 세션코드 복구 (재시작 self-heal)
    #    발표자가 #s=CODE 가 적힌 메시지에 답장한 것 자체가 라우팅 증거이므로
    #    DB 상태와 무관하게 코드를 신뢰하고, 없으면 세션 row를 복구한다.
    if not sid:
        m = CODE_RE.search(reply_to.get("text", "") or "")
        if m:
            sid = m.group(1)
            await db(lambda: _ensure_session(sid))

    # 3) "AB12CD 답변내용" 형식 직접 지정 (스와이프 답장 안 될 때 폴백)
    if not sid:
        m = re.match(r"^([A-Z0-9]{6})\s+(.*)$", text, re.S)
        if m:
            sid = m.group(1)
            text = m.group(2).strip()
            await db(lambda: _ensure_session(sid))

    # 4) 그래도 못 찾으면: 최근 30분 내 활성 세션이 딱 하나면 그쪽으로
    if not sid:
        def _recent():
            cutoff = time.time() - 1800
            rows = _conn.execute(
                "SELECT DISTINCT session_id FROM messages WHERE ts>? AND sender='visitor'",
                (cutoff,)).fetchall()
            return [r["session_id"] for r in rows]
        recent = await db(_recent)
        if len(recent) == 1:
            sid = recent[0]

    if not sid:
        await tg_send("⚠️ 어느 문의에 답할지 못 찾았어요. 상대방 메시지에 <b>답장(swipe-reply)</b> 하거나, "
                      "<code>세션코드 답변내용</code> 형식으로 보내주세요.")
        return {"ok": True}

    now = time.time()
    def _ins():
        _conn.execute(
            "INSERT INTO messages(session_id,sender,text,ts) VALUES(?,?,?,?)",
            (sid, "presenter", text, now))
        _conn.commit()
    await db(_ins)
    _wake(sid)
    return {"ok": True}
