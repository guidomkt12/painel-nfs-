"""Serverless deployment entry point. Use a dedicated PostgreSQL database."""
import os, time, json, base64, hashlib, urllib.request, urllib.error, threading
from urllib.parse import unquote, urlsplit
from pathlib import Path
from flask import Flask, Response, request, jsonify, send_from_directory, stream_with_context
import psycopg
from psycopg.rows import dict_row
import server as core

app = Flask(__name__, static_folder=None)
LIMIT = 4 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = LIMIT
DATABASE = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
READY = False
INIT_LOCK = threading.Lock()

class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def row_factory(cursor):
    maker = dict_row(cursor)
    return lambda values: Row(maker(values))

class Pg:
    def __init__(self):
        self.raw = psycopg.connect(DATABASE, connect_timeout=8, row_factory=row_factory)
    def __enter__(self):
        return self
    def __exit__(self, typ, value, tb):
        try:
            self.raw.rollback() if typ else self.raw.commit()
        finally:
            self.raw.close()
    def execute(self, sql, args=()):
        if sql == "BEGIN IMMEDIATE":
            return self.raw.execute("SELECT pg_advisory_xact_lock(764308216)")
        if sql == "BEGIN":
            return self.raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return self.raw.execute(sql.replace("?", "%s"), args)

def configuration_errors():
    errors = []
    if core.DEMO: errors.append("DEMO_MODE")
    if not DATABASE: errors.append("DATABASE_URL")
    if not isinstance(core.USERS, dict) or not core.USERS or any(not isinstance(v, str) or len(v) < 16 for v in core.USERS.values()): errors.append("PANEL_USERS_JSON")
    if len(core.TOKEN) < 32: errors.append("WEBHOOK_TOKEN")
    if not core.ORIGIN.startswith("https://"): errors.append("PUBLIC_ORIGIN")
    if not core.UPLOAD_URL.startswith("https://"): errors.append("N8N_UPLOAD_URL")
    if len(core.UPLOAD_TOKEN) < 32: errors.append("N8N_UPLOAD_TOKEN")
    if core.UPLOAD_HEADER.lower() not in ("apikey","authorization","x-api-key"): errors.append("N8N_UPLOAD_HEADER")
    return errors

def ensure():
    global READY
    if READY:
        return
    with INIT_LOCK:
        if READY:
            return
        missing = configuration_errors()
        if missing:
            raise RuntimeError("Production configuration incomplete: " + ", ".join(missing))
        with Pg() as c:
            c.execute("BEGIN IMMEDIATE")
            for sql in [
                "CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,name TEXT NOT NULL,status TEXT NOT NULL,total BIGINT,revision BIGINT NOT NULL DEFAULT -1,updated TEXT NOT NULL,error TEXT)",
                "CREATE TABLE IF NOT EXISTS rows(batch_id TEXT NOT NULL,id TEXT NOT NULL,revision BIGINT NOT NULL,data TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(batch_id,id))",
                "CREATE TABLE IF NOT EXISTS reviews(batch_id TEXT NOT NULL,row_id TEXT NOT NULL,reviewed INTEGER NOT NULL,revision BIGINT NOT NULL,actor TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(batch_id,row_id))",
                "CREATE TABLE IF NOT EXISTS review_log(id BIGSERIAL PRIMARY KEY,batch_id TEXT,row_id TEXT,reviewed INTEGER,revision BIGINT,actor TEXT,updated TEXT)",
                "CREATE TABLE IF NOT EXISTS events(seq BIGSERIAL PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,sha TEXT UNIQUE NOT NULL,name TEXT NOT NULL,content BYTEA,attempts INTEGER DEFAULT 0,next_attempt DOUBLE PRECISION DEFAULT 0,sent INTEGER DEFAULT 0,error TEXT)"
            ]:
                c.execute(sql)
        core.connect = Pg
        READY = True

@app.after_request
def protect(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

@app.errorhandler(413)
def too_large(_):
    return jsonify(error="Arquivo excede o limite de 4 MiB"), 413

@app.before_request
def authorize():
    if request.path == "/health":
        return None
    if request.path in ("/app.js", "/style.css", "/keile.svg"):
        return None
    try:
        ensure()
    except Exception:
        app.logger.error("Panel initialization failed; missing variables: %s", ", ".join(configuration_errors()) or "none; check database")
        return jsonify(error="Configuração ou banco de produção indisponível", code="PANEL_NOT_READY"), 503
    if request.path == "/api/events":
        if not core.equal(request.headers.get("Authorization",""), "Bearer " + core.TOKEN):
            return jsonify(error="Não autorizado"), 401
        return None
    a = request.authorization
    if not a or a.type != "basic" or a.username not in core.USERS or not core.equal(core.USERS[a.username], a.password):
        return Response(status=401, headers={"WWW-Authenticate": 'Basic realm="Painelzinho da Keile", charset="UTF-8"'})
    if request.method == "POST" and request.headers.get("Origin") != core.ORIGIN:
        return jsonify(error="Origem inválida"), 403

@app.get("/health")
def health():
    try:
        ensure()
        with Pg() as c:
            c.execute("SELECT 1")
        return jsonify(ok=True)
    except Exception:
        app.logger.error("Panel health check failed; missing variables: %s", ", ".join(configuration_errors()) or "none; check database")
        return jsonify(ok=False, error="Serviço indisponível", code="PANEL_NOT_READY"), 503

@app.get("/")
def index():
    return send_from_directory(core.ROOT / "public", "index.html")

@app.get("/<name>")
def asset(name):
    if name not in {"app.js","style.css","keile.svg"}:
        return jsonify(error="Não encontrado"), 404
    return send_from_directory(core.ROOT / "public", name)

@app.get("/api/config")
def config():
    return jsonify(demo=False, max_upload_bytes=LIMIT)

@app.get("/api/snapshot")
def snapshot():
    return jsonify(core.snapshot())

@app.get("/api/live")
def live():
    # Each connection is bounded; browser reconnects and receives current state.
    def generate():
        snap = core.snapshot()
        seq = snap["seq"]
        yield "retry: 1000\nid: %s\nevent: snapshot\ndata: %s\n\n" % (seq, json.dumps(snap, ensure_ascii=False))
        end = time.monotonic() + 24
        while time.monotonic() < end:
            with Pg() as c:
                events = c.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 100", (seq,)).fetchall()
            for e in events:
                seq = e["seq"]
                yield "id: %s\nevent: change\ndata: %s\n\n" % (seq, json.dumps({"type":e["kind"],"payload":json.loads(e["payload"])}, ensure_ascii=False))
            if not events:
                yield ": heartbeat\n\n"
                time.sleep(1)
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={"X-Accel-Buffering":"no"})

@app.post("/api/events")
def events():
    try:
        if request.content_length is None or request.content_length > core.MAX_EVENT:
            return jsonify(error="Evento acima do limite"), 413
        return jsonify(core.apply(request.get_json(force=True)))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return jsonify(error=str(e)[:250]), 400

@app.post("/api/review")
def review():
    try:
        b = request.get_json(force=True)
        core.review(b["batch_id"], b["row_id"], b["revision"], b["reviewed"], request.authorization.username)
        return jsonify(ok=True)
    except (ValueError, KeyError, TypeError) as e:
        return jsonify(error=str(e)[:250]), 400

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

@app.post("/api/upload")
def upload():
    # Serverless requests must await the n8n ACK; no background thread.
    try:
        result = core.enqueue(unquote(request.headers.get("X-Filename","")), request.get_data())
    except (ValueError, TypeError) as e:
        return jsonify(error=str(e)[:250]), 400
    bid = result["id"]
    with Pg() as c:
        x = c.execute("SELECT * FROM outbox WHERE id=?", (bid,)).fetchone()
    if x["sent"]:
        return jsonify(id=bid, duplicate=True), 200
    try:
        payload = json.dumps({"batch_id":bid,"filename":x["name"],"xlsx_base64":base64.b64encode(x["content"]).decode()}).encode()
        auth = "Bearer " + core.UPLOAD_TOKEN if core.UPLOAD_HEADER.lower() == "authorization" else core.UPLOAD_TOKEN
        req = urllib.request.Request(core.UPLOAD_URL, data=payload, headers={core.UPLOAD_HEADER:auth,"Content-Type":"application/json"}, method="POST")
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
            ack = json.loads(response.read(65536))
            if response.status not in (200,201,202) or ack.get("accepted") is not True or ack.get("batch_id") != bid:
                raise ValueError("ACK inválido")
        with Pg() as c:
            c.execute("UPDATE outbox SET sent=1,content=NULL,error=NULL WHERE id=?", (bid,))
        return jsonify(id=bid, duplicate=bool(result["duplicate"])), 202
    except Exception:
        with Pg() as c:
            c.execute("UPDATE outbox SET attempts=attempts+1,error=? WHERE id=? AND sent=0", ("Falha no envio ao n8n",bid))
        return jsonify(error="n8n não confirmou o arquivo. Reenvie o mesmo XLSX para tentar novamente.", id=bid), 502

@app.errorhandler(500)
def internal(_):
    return jsonify(error="Falha interna"), 500
