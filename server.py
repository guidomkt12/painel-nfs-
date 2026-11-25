import os, json, sqlite3, threading, time, hmac, base64, hashlib, uuid, urllib.request, urllib.error, tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT=Path(__file__).parent
DEMO=os.environ.get("DEMO_MODE")=="1"
DB=str(Path(tempfile.mkdtemp(prefix="keile-demo-"))/"demo.sqlite3") if DEMO else os.environ.get("DB_PATH","/data/keile.sqlite3")
try:
    USERS=json.loads(os.environ.get("PANEL_USERS_JSON","{}"))
except (ValueError, TypeError):
    USERS={}
TOKEN=os.environ.get("WEBHOOK_TOKEN","")
UPLOAD_URL=os.environ.get("N8N_UPLOAD_URL","")
UPLOAD_TOKEN=os.environ.get("N8N_UPLOAD_TOKEN","")
UPLOAD_HEADER=os.environ.get("N8N_UPLOAD_HEADER","apikey").strip()
ORIGIN=os.environ.get("PUBLIC_ORIGIN","").rstrip("/")
if DEMO:
    UPLOAD_URL=UPLOAD_TOKEN=TOKEN=""
    ORIGIN="http://127.0.0.1:"+os.environ.get("PORT","8080")
MAX_FILE=10*1024*1024
MAX_EVENT=512*1024
SIGNAL=threading.Condition()
OPS=set(os.environ.get("PANEL_OPERATIONS","PAJ,PAC").upper().split(","))
def now(): return datetime.now(timezone.utc).isoformat()
def equal(a,b): return hmac.compare_digest(str(a).encode(),str(b).encode())
def connect():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    return c
def init():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    with connect() as c:
        c.executescript("""PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,name TEXT NOT NULL,status TEXT NOT NULL,total INTEGER,revision INTEGER NOT NULL DEFAULT -1,updated TEXT NOT NULL,error TEXT);
        CREATE TABLE IF NOT EXISTS rows(batch_id TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(batch_id,id));
        CREATE TABLE IF NOT EXISTS reviews(batch_id TEXT NOT NULL,row_id TEXT NOT NULL,reviewed INTEGER NOT NULL,revision INTEGER NOT NULL,actor TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(batch_id,row_id));
        CREATE TABLE IF NOT EXISTS review_log(id INTEGER PRIMARY KEY,batch_id TEXT,row_id TEXT,reviewed INTEGER,revision INTEGER,actor TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,sha TEXT UNIQUE NOT NULL,name TEXT NOT NULL,content BLOB,attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,sent INTEGER DEFAULT 0,error TEXT);
        """)
def event(c,eid,kind,payload):
    c.execute("INSERT INTO events(event_id,kind,payload) VALUES(?,?,?)",(eid,kind,json.dumps(payload,ensure_ascii=False,separators=(",",":"))))
def notify():
    with SIGNAL: SIGNAL.notify_all()
def key(x):
    if not isinstance(x,str) or not x or len(x)>220 or any(ord(ch)<32 for ch in x): raise ValueError("Identificador inválido")
    return x
def integer(x,minval=0):
    if type(x) is not int or x<minval or x>10**15: raise ValueError("Número inválido")
    return x
def short(x,limit=300):
    if not isinstance(x,str) or len(x)>limit: raise ValueError("Texto inválido")
    return x
def safe_links(xs):
    if not isinstance(xs,list) or len(xs)>30: raise ValueError("Links inválidos")
    result=[]
    for x in xs:
        if not isinstance(x,dict): raise ValueError("Link inválido")
        url=short(x.get("url",""),3000); host=urlsplit(url).hostname or ""
        if urlsplit(url).scheme=="https" and (host=="outlook.office365.com" or host=="outlook.office.com" or host.endswith(".sharepoint.com") or host=="app.omie.com.br" or host.endswith(".omie.com.br")):
            result.append({"label":short(x.get("label","Documento"),150),"url":url})
    return result
def apply(e):
    eid=key(e.get("event_id")); bid=key(e.get("batch_id")); kind=e.get("type")
    rev=integer(e.get("revision",0)); ts=now()
    if kind not in {"batch.started","row.upsert","batch.completed","batch.failed"}: raise ValueError("Tipo desconhecido")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM events WHERE event_id=?",(eid,)).fetchone(): return {"duplicate":True}
        b=c.execute("SELECT * FROM batches WHERE id=?",(bid,)).fetchone()
        if kind=="batch.started":
            name=short(e.get("name","DDA"),250); total=e.get("total")
            if total is not None: integer(total)
            if b and b["revision"]>=rev: return {"stale":True}
            if b and b["status"]=="completed": return {"stale":True}
            c.execute("INSERT INTO batches(id,name,status,total,revision,updated) VALUES(?,?, 'processing',?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='processing',total=excluded.total,revision=excluded.revision,updated=excluded.updated,error=NULL",(bid,name,total,rev,ts))
            p={"id":bid,"name":name,"status":"processing","total":total,"updated":ts}
        elif kind=="row.upsert":
            if not b: raise ValueError("Lote deve iniciar antes das linhas")
            if b["status"]=="failed": raise ValueError("Lote está encerrado com erro")
            rid=key(e.get("row_id")); r=e.get("row")
            if not isinstance(r,dict): raise ValueError("Linha ausente")
            op=short(r.get("operacao",""),50).upper()
            if op not in OPS and op!="NÃO IDENTIFICADA": raise ValueError("Operação inválida")
            status=short(r.get("status","REVISAR"),160)
            data={"operacao":op,"fornecedor":short(r.get("fornecedor",""),300),"documento":short(r.get("documento",""),150),"cnpj":short(r.get("cnpj",""),40),"valor_centavos":integer(r.get("valor_centavos")),"vencimento":short(r.get("vencimento",""),30),"status":status,"nota":short(r.get("nota",""),500),"detalhe":short(r.get("detalhe",""),12000),"links":safe_links(r.get("links",[]))}
            prev=c.execute("SELECT revision,data FROM rows WHERE batch_id=? AND id=?",(bid,rid)).fetchone()
            if prev and prev["revision"]>=rev: return {"stale":True}
            if b["status"]=="completed" and not prev: raise ValueError("Lote concluído não aceita nova linha")
            c.execute("INSERT INTO rows VALUES(?,?,?,?,?) ON CONFLICT(batch_id,id) DO UPDATE SET revision=excluded.revision,data=excluded.data,updated=excluded.updated",(bid,rid,rev,json.dumps(data,ensure_ascii=False),ts))
            same=bool(prev and json.loads(prev["data"])==data)
            if same: c.execute("UPDATE reviews SET revision=? WHERE batch_id=? AND row_id=? AND revision=?",(rev,bid,rid,prev["revision"]))
            v=c.execute("SELECT reviewed,actor FROM reviews WHERE batch_id=? AND row_id=? AND revision=?",(bid,rid,rev)).fetchone()
            p={"batch_id":bid,"id":rid,"revision":rev,"updated":ts,**data,"reviewed":bool(v["reviewed"]) if v else False,"review_actor":v["actor"] if v else None}
        else:
            if not b: raise ValueError("Lote não encontrado")
            if b["revision"]>=rev or b["status"]=="completed": return {"stale":True}
            count=c.execute("SELECT count(*) FROM rows WHERE batch_id=?",(bid,)).fetchone()[0]
            total=e.get("total",b["total"])
            if total is not None: integer(total)
            if kind=="batch.completed" and (total is None or count!=total): raise ValueError("Total informado não corresponde às linhas recebidas")
            status="completed" if kind=="batch.completed" else "failed"
            error=short(e.get("error",""),1000) if status=="failed" else None
            c.execute("UPDATE batches SET status=?,total=?,revision=?,updated=?,error=? WHERE id=?",(status,total,rev,ts,error,bid))
            p={"id":bid,"name":b["name"],"status":status,"total":total,"updated":ts,"error":error}
        event(c,eid,kind,p)
    notify()
    return {"accepted":True}
def snapshot():
    with connect() as c:
        c.execute("BEGIN")
        seq=c.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
        batches=[dict(x) for x in c.execute("SELECT id,name,status,total,updated,error FROM batches ORDER BY updated DESC")]
        rows=[]
        for x in c.execute("""SELECT r.*,v.reviewed,v.actor,v.revision AS review_revision FROM rows r LEFT JOIN reviews v ON v.batch_id=r.batch_id AND v.row_id=r.id"""):
            valid=x["review_revision"]==x["revision"]
            rows.append({"batch_id":x["batch_id"],"id":x["id"],"revision":x["revision"],"updated":x["updated"],**json.loads(x["data"]),"reviewed":bool(x["reviewed"]) if valid else False,"review_actor":x["actor"] if valid else None})
    return {"seq":seq,"batches":batches,"rows":rows}
def review(bid,rid,revision,checked,actor):
    if type(checked) is not bool: raise ValueError("Conferência inválida")
    key(bid); key(rid); integer(revision); ts=now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT revision FROM rows WHERE batch_id=? AND id=?",(bid,rid)).fetchone()
        if not r or r["revision"]!=revision: raise ValueError("Evidência mudou; confira novamente")
        c.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?) ON CONFLICT(batch_id,row_id) DO UPDATE SET reviewed=excluded.reviewed,revision=excluded.revision,actor=excluded.actor,updated=excluded.updated",(bid,rid,int(checked),revision,actor,ts))
        c.execute("INSERT INTO review_log(batch_id,row_id,reviewed,revision,actor,updated) VALUES(?,?,?,?,?,?)",(bid,rid,int(checked),revision,actor,ts))
        event(c,str(uuid.uuid4()),"row.reviewed",{"batch_id":bid,"id":rid,"revision":revision,"reviewed":checked,"review_actor":actor,"updated":ts})
    notify()
def enqueue(name,content):
    if not name.lower().endswith(".xlsx") or not content.startswith(b"PK\x03\x04"): raise ValueError("Envie um Excel .xlsx válido")
    name=Path(name).name; short(name,200)
    sha=hashlib.sha256(content).hexdigest(); bid="upload-"+sha; ts=now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old=c.execute("SELECT id FROM outbox WHERE sha=?",(sha,)).fetchone()
        if old: return {"id":old["id"],"duplicate":True}
        c.execute("INSERT INTO outbox(id,sha,name,content) VALUES(?,?,?,?)",(bid,sha,name,content))
        c.execute("INSERT INTO batches(id,name,status,updated) VALUES(?,?, 'queued',?)",(bid,name,ts))
        event(c,bid+":queued","batch.queued",{"id":bid,"name":name,"status":"queued","total":None,"updated":ts})
    notify(); return {"id":bid,"duplicate":False}
def dispatch():
    while True:
        try:
            if UPLOAD_URL and UPLOAD_TOKEN:
                with connect() as c: x=c.execute("SELECT * FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY next_attempt LIMIT 1",(time.time(),)).fetchone()
                if x:
                    payload=json.dumps({"batch_id":x["id"],"filename":x["name"],"xlsx_base64":base64.b64encode(x["content"]).decode()}).encode()
                    req=urllib.request.Request(UPLOAD_URL,data=payload,headers={UPLOAD_HEADER:("Bearer "+UPLOAD_TOKEN if UPLOAD_HEADER.lower()=="authorization" else UPLOAD_TOKEN),"Content-Type":"application/json"},method="POST")
                    try:
                        with urllib.request.urlopen(req,timeout=45) as response:
                            if response.status not in (200,201,202): raise RuntimeError("Resposta não aceita")
                            ack=json.loads(response.read(65536))
                            if ack.get("accepted") is not True or ack.get("batch_id")!=x["id"]: raise RuntimeError("Confirmação inválida")
                        with connect() as c: c.execute("UPDATE outbox SET sent=1,content=NULL,error=NULL WHERE id=?",(x["id"],))
                    except Exception as err:
                        with connect() as c: c.execute("UPDATE outbox SET attempts=attempts+1,next_attempt=?,error=? WHERE id=?",(time.time()+min(900,5*2**min(x["attempts"],7)),type(err).__name__,x["id"]))
        except Exception: pass
        time.sleep(2)
class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args): pass
    def send(self,status,data,mime="application/json"):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type",mime); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store, private"); self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","no-referrer"); self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"); self.end_headers(); self.wfile.write(data)
    def user(self):
        try:
            auth=self.headers.get("Authorization","")
            if not auth.startswith("Basic "): return None
            u,p=base64.b64decode(auth[6:],validate=True).decode().split(":",1)
            return u if u in USERS and equal(USERS[u],p) else None
        except Exception: return None
    def require(self):
        u=self.user()
        if u: return u
        self.send_response(401);self.send_header("WWW-Authenticate",'Basic realm="Painelzinho da Keile", charset="UTF-8"');self.send_header("Content-Length","0");self.send_header("Cache-Control","no-store");self.end_headers();return None
    def body(self,limit):
        n=int(self.headers.get("Content-Length","0"))
        if n<=0 or n>limit: raise ValueError("Tamanho inválido")
        data=self.rfile.read(n)
        if len(data)!=n: raise ValueError("Corpo incompleto")
        return data
    def do_GET(self):
        try:
            path=urlsplit(self.path).path
            if path=="/health": return self.send(200,{"ok":True})
            if not self.require(): return
            if path=="/api/config": return self.send(200,{"demo":DEMO})
            if path=="/api/snapshot": return self.send(200,snapshot())
            if path=="/api/live":
                self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Cache-Control","no-cache, no-store, private");self.send_header("X-Accel-Buffering","no");self.send_header("Connection","close");self.end_headers()
                self.close_connection=True
                try:
                    snap=snapshot();seq=snap["seq"]
                    self.wfile.write(("id: "+str(seq)+"\nevent: snapshot\ndata: "+json.dumps(snap,ensure_ascii=False)+"\n\n").encode());self.wfile.flush()
                    while True:
                        with connect() as c: events=c.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 500",(seq,)).fetchall()
                        for x in events:
                            self.wfile.write(("id: "+str(x["seq"])+"\nevent: change\ndata: "+json.dumps({"type":x["kind"],"payload":json.loads(x["payload"])},ensure_ascii=False)+"\n\n").encode());seq=x["seq"]
                        if not events:
                            self.wfile.write(b": heartbeat\n\n");self.wfile.flush()
                            with SIGNAL: SIGNAL.wait(timeout=1)
                        else: self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,OSError): return
                return
            assets={"/":("index.html","text/html; charset=utf-8"),"/app.js":("app.js","text/javascript; charset=utf-8"),"/style.css":("style.css","text/css; charset=utf-8"),"/keile.svg":("keile.svg","image/svg+xml")}
            if path not in assets: return self.send(404,{"error":"Não encontrado"})
            file,mime=assets[path];return self.send(200,(ROOT/"public"/file).read_bytes(),mime)
        except Exception: self.send(500,{"error":"Falha interna"})
    def do_POST(self):
        try:
            path=urlsplit(self.path).path
            if path=="/api/events":
                if DEMO: return self.send(403,{"error":"Modo demonstração não recebe dados externos"})
                if not equal(self.headers.get("Authorization",""),"Bearer "+TOKEN): return self.send(401,{"error":"Não autorizado"})
                return self.send(200,apply(json.loads(self.body(MAX_EVENT))))
            actor=self.require()
            if not actor: return
            if not ORIGIN or self.headers.get("Origin")!=ORIGIN: return self.send(403,{"error":"Origem inválida"})
            if path=="/api/review":
                b=json.loads(self.body(16384));review(b["batch_id"],b["row_id"],b["revision"],b["reviewed"],actor);return self.send(200,{"ok":True})
            if path=="/api/demo":
                if not DEMO: return self.send(404,{"error":"Não encontrado"})
                bid="demo-"+str(uuid.uuid4()); apply({"event_id":bid+":start","batch_id":bid,"type":"batch.started","revision":1,"name":"DDA FICTÍCIO.xlsx","total":3})
                def simulate():
                    for i,(vendor,doc,amount) in enumerate([("Fornecedor Alfa","123",28719),("Fornecedor Beta","568/7049",228000),("Fornecedor Gama","345-A1",273166)],1):
                        time.sleep(2)
                        apply({"event_id":bid+":"+str(i),"batch_id":bid,"type":"row.upsert","row_id":"PAJ:"+str(i),"revision":2,"row":{"operacao":"PAJ","fornecedor":vendor,"documento":doc,"valor_centavos":amount,"status":"REVISAR","nota":"Exemplo fictício","detalhe":"Evento simulado, sem consulta ao Omie.","links":[]}})
                    apply({"event_id":bid+":end","batch_id":bid,"type":"batch.completed","revision":3,"total":3})
                threading.Thread(target=simulate,daemon=True).start()
                return self.send(202,{"accepted":True,"id":bid})
            if path=="/api/upload":
                if DEMO: return self.send(403,{"error":"Demonstração não aceita arquivos reais"})
                if not UPLOAD_URL or not UPLOAD_TOKEN: return self.send(503,{"error":"Webhook n8n ainda não configurado"})
                from urllib.parse import unquote
                name=unquote(self.headers.get("X-Filename",""))
                return self.send(202,enqueue(name,self.body(MAX_FILE)))
            return self.send(404,{"error":"Não encontrado"})
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as err: return self.send(400,{"error":str(err)[:250]})
        except Exception: return self.send(500,{"error":"Falha interna"})
if __name__=="__main__":
    if DEMO:
        if not isinstance(USERS,dict) or not USERS or any(p!="123456" for p in USERS.values()): raise SystemExit("Configure PANEL_USERS_JSON com usuários de teste e senha 123456.")
    elif not isinstance(USERS,dict) or not USERS or any(not isinstance(p,str) or len(p)<16 for p in USERS.values()) or len(TOKEN)<32 or not ORIGIN.startswith("https://") or (not UPLOAD_URL.startswith("https://") or len(UPLOAD_TOKEN)<32 or UPLOAD_HEADER.lower() not in ("apikey","authorization","x-api-key")):
        raise SystemExit("Configure usuários individuais (senhas >=16), WEBHOOK_TOKEN >=32, PUBLIC_ORIGIN HTTPS e webhook de envio seguro.")
    init();threading.Thread(target=dispatch,daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1" if DEMO else "0.0.0.0",int(os.environ.get("PORT","8080"))),Handler).serve_forever()
