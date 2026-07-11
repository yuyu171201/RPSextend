"""
RPS Extend Online - Web GUI サーバー（ブラウザで遊ぶ）

spec.md の中央サーバー方式のまま、クライアントをブラウザGUI化したもの。
- 対戦ロジックは engine.py を共用（TCP版 server.py と同じ判定・進行）。
- ブラウザ ⇔ サーバーは HTTP(JSON)。ブラウザはポーリングでメッセージを受け取り、
  PROMPT はカード風ボタンで選択する。
- 全状態はサーバーが保持（クライアントはチート不可）。秘匿は「揃うまで送らない」表示制御。

使い方:
    python3 web_server.py [--host 0.0.0.0] [--port 8000]
    → ブラウザで http://<サーバーIP>:8000/ を2人が開いて対戦。
"""

import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import engine

# token -> {"session", "tx", "last", "matched", "dead", "match", "joined"}
SESSIONS = {}
WAITING = []
LOCK = threading.Lock()

# 管理用ステータス確認トークン（環境変数 RPS_ADMIN_TOKEN）。
# 未設定なら /admin/status はサーバー内(loopback直叩き)からのみ許可し、
# Nginx経由(公開URL)からは拒否する。
ADMIN_TOKEN = os.environ.get("RPS_ADMIN_TOKEN", "").strip()


class HttpTransport:
    def __init__(self):
        self.outbox = []
        self.lock = threading.Lock()

    def send(self, obj):
        with self.lock:
            self.outbox.append(obj)

    def drain(self):
        with self.lock:
            msgs, self.outbox = self.outbox, []
            return msgs

    def close(self):
        pass


def _start_match_if_ready():
    if len(WAITING) >= 2:
        t0, t1 = WAITING.pop(0), WAITING.pop(0)
        s0, s1 = SESSIONS[t0], SESSIONS[t1]
        mid = uuid.uuid4().hex[:8]
        s0["matched"] = s1["matched"] = True
        s0["match"] = s1["match"] = mid
        threading.Thread(
            target=lambda: engine.Match(s0["session"], s1["session"]).run(),
            daemon=True).start()


# ポーリングが途絶えたら切断とみなすまでの秒数（ブラウザは約0.7秒間隔でポーリング）。
DISCONNECT_TIMEOUT = 15


def _abort_match(dead_rec):
    """対戦中セッション dead_rec が切断された時、対戦相手も含めて対戦を終了させる。
    両者のキューへ「切断者の名前を持つ」シグナルを送るので、どちらの手番待ちでも
    即座に中断できる。必ず LOCK を取得した状態で呼ぶこと。"""
    sig = engine.Disconnected(dead_rec["session"].name)
    mid = dead_rec.get("match")
    partners = [r for r in SESSIONS.values()
                if mid is not None and r.get("match") == mid] or [dead_rec]
    for r in partners:
        r["session"].q.put(sig)


def _drop_session(token, rec):
    """セッションを切断扱いにし、待機列/対戦から外す。LOCK 内で呼ぶこと。"""
    if rec.get("dead"):
        return
    rec["dead"] = True
    if token in WAITING:
        WAITING.remove(token)
    if rec.get("matched"):
        _abort_match(rec)


def reaper():
    """一定時間ポーリングが来ないセッションを切断扱いにする。"""
    while True:
        time.sleep(3)
        now = time.time()
        with LOCK:
            for token, rec in list(SESSIONS.items()):
                if rec.get("dead"):
                    continue
                if now - rec["last"] > DISCONNECT_TIMEOUT:
                    _drop_session(token, rec)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 静音

    # --- 共通レスポンス ---
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- 管理用: 現在のプレイヤー確認 ---
    def _admin_authorized(self, qs):
        """/admin/* へのアクセス可否。
        - RPS_ADMIN_TOKEN 設定時: ?token= が一致すれば許可（公開URLでも可）。
        - 未設定時: Nginx経由(X-Forwarded-For/X-Real-IP 付き)は拒否し、
          サーバー内からの loopback 直叩きのみ許可。"""
        if ADMIN_TOKEN:
            return (qs.get("token") or [""])[0] == ADMIN_TOKEN
        via_proxy = self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP")
        host = self.client_address[0] if self.client_address else ""
        return (via_proxy is None) and host in ("127.0.0.1", "::1", "localhost")

    def _admin_status(self):
        now = time.time()
        waiting, matches, dead = [], {}, 0
        with LOCK:
            for token, rec in SESSIONS.items():
                name = rec["session"].name
                info = {
                    "name": name,
                    "id": token[:8],
                    "idle_sec": round(now - rec["last"], 1),
                    "age_sec": round(now - rec.get("joined", rec["last"]), 1),
                    "dead": bool(rec.get("dead")),
                }
                if rec.get("dead"):
                    dead += 1
                if rec.get("matched") and rec.get("match"):
                    matches.setdefault(rec["match"], []).append(info)
                elif not rec.get("dead"):
                    waiting.append(info)
            total = len(SESSIONS)
        playing = sum(len(v) for v in matches.values())
        return {
            "now": round(now, 1),
            "counts": {"total": total, "waiting": len(waiting),
                       "playing": playing, "dead": dead},
            "waiting": waiting,
            "matches": [{"match": mid, "players": ps} for mid, ps in matches.items()],
        }

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # --- ルーティング ---
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._html(PAGE)
            return
        if parsed.path == "/poll":
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0]
            with LOCK:
                rec = SESSIONS.get(token)
                if rec:
                    rec["last"] = time.time()
            if not rec:
                self._json({"messages": [], "expired": True})
                return
            self._json({"messages": rec["tx"].drain()})
            return
        if parsed.path == "/admin/status":
            qs = parse_qs(parsed.query)
            if not self._admin_authorized(qs):
                self._json({"error": "forbidden"}, 403)
                return
            self._json(self._admin_status())
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._read_body()

        if parsed.path == "/join":
            name = str(data.get("name") or "Player")[:16]
            token = uuid.uuid4().hex
            tx = HttpTransport()
            session = engine.Session(tx, name)
            with LOCK:
                SESSIONS[token] = {
                    "session": session, "tx": tx, "last": time.time(),
                    "matched": False, "dead": False,
                    "match": None, "joined": time.time(),
                }
                session.msg(f"ようこそ {name} さん。対戦相手を待っています...")
                WAITING.append(token)
                _start_match_if_ready()
            self._json({"token": token, "name": name})
            return

        if parsed.path == "/choice":
            token = str(data.get("token") or "")
            with LOCK:
                rec = SESSIONS.get(token)
                if rec:
                    rec["last"] = time.time()
            if rec:
                rec["session"].q.put({
                    "type": "CHOICE",
                    "pid": data.get("pid"),
                    "value": data.get("value"),
                })
            self._json({"ok": True})
            return

        if parsed.path == "/leave":
            # ブラウザを閉じた/離脱した時に sendBeacon で即時通知される。
            # reaper のタイムアウトを待たずにその場で対戦を終了させる。
            token = str(data.get("token") or "")
            with LOCK:
                rec = SESSIONS.get(token)
                if rec:
                    _drop_session(token, rec)
            self._json({"ok": True})
            return

        self._json({"error": "not found"}, 404)


# ---- ブラウザに配信する単一ページ（HTML/CSS/JS） -------------------------

PAGE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RPS Extend Online</title>
<style>
  :root{
    /* みずいろ基調のライトテーマ（home.sumyuu.com のパステル水色イメージ） */
    --bg:#e9fbff; --bg2:#d6f4fb;
    --panel:#ffffff; --panel2:#eef9fd; --ink:#123640;
    --muted:#5c8895; --accent:#00c2d6; --accent-ink:#053b42; --line:#c3e9f2;
    --glow:rgba(0,194,214,.16);
    --aka:#e0524b; --midori:#2f9e52; --ao:#2f74d0;
  }
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;font-family:-apple-system,"Hiragino Kaku Gothic ProN","Yu Gothic",
       "Meiryo",system-ui,sans-serif;color:var(--ink);
       background:linear-gradient(180deg,var(--bg),var(--bg2)) fixed}
  header{padding:14px 18px;background:linear-gradient(90deg,#ccf4fb,#e9fbff);
         border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
  header h1{font-size:18px;margin:0;letter-spacing:.5px}
  header .tag{font-size:12px;color:var(--muted)}
  .wrap{max-width:1000px;margin:0 auto;padding:18px;display:grid;
        grid-template-columns:1.15fr .85fr;gap:16px}
  @media(max-width:820px){.wrap{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;
        box-shadow:0 6px 20px var(--glow)}
  .scoreboard{display:flex;justify-content:space-between;align-items:center;gap:10px;
        background:var(--panel2);border-radius:12px;padding:12px 14px;margin-bottom:12px}
  .scoreboard .me{color:var(--accent);font-weight:700}
  .scoreboard .turn{font-size:13px;color:var(--muted)}
  .score{font-size:22px;font-weight:800}
  h2{font-size:14px;color:var(--muted);margin:2px 0 10px;font-weight:600;
     text-transform:uppercase;letter-spacing:1px}
  #board{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
     font-size:12.5px;line-height:1.5;background:#f2fcff;border-radius:10px;padding:12px;
     border:1px solid var(--line);max-height:340px;overflow:auto}
  #log{height:220px;overflow:auto;font-size:13px;line-height:1.6}
  #log .l{padding:2px 0;border-bottom:1px dashed #cdeaf1}
  #prompt{min-height:120px}
  #promptText{font-size:15px;font-weight:700;margin-bottom:12px}
  .opts{display:flex;flex-wrap:wrap;gap:10px}
  .opt{cursor:pointer;border:2px solid var(--line);background:var(--panel2);color:var(--ink);
     border-radius:12px;padding:12px 14px;font-size:15px;font-weight:700;min-width:92px;
     text-align:center;transition:transform .06s ease,border-color .1s ease}
  .opt:hover{transform:translateY(-2px);border-color:var(--accent)}
  .opt.aka{border-color:var(--aka)} .opt.midori{border-color:var(--midori)}
  .opt.ao{border-color:var(--ao)}
  .opt .sub{display:block;font-size:11px;color:var(--muted);font-weight:500;margin-top:2px}
  .waiting{color:var(--muted);font-size:14px;padding:10px 0}
  .dot{display:inline-block;animation:blink 1.2s infinite}
  @keyframes blink{0%,100%{opacity:.2}50%{opacity:1}}
  /* join */
  #join{max-width:460px;margin:60px auto;text-align:center}
  #join input{font-size:16px;padding:12px 14px;border-radius:10px;border:1px solid var(--line);
     background:#f7feff;color:var(--ink);width:100%;margin:14px 0}
  #join input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--glow)}
  .btn{cursor:pointer;background:var(--accent);color:#fff;border:none;border-radius:10px;
     padding:12px 22px;font-size:16px;font-weight:800;box-shadow:0 4px 12px var(--glow)}
  .btn:hover{filter:brightness(1.05)}
  .btn:disabled{opacity:.5;cursor:default}
  #banner{display:none;margin-top:12px;padding:14px;border-radius:12px;font-weight:800;
     font-size:16px;text-align:center;white-space:pre-wrap}
  .win{background:#e2f9ea;color:#1f7a3a;border:1px solid #a7e5bd}
  .lose{background:#fdeaec;color:#b23a44;border:1px solid #f2b8be}
  .draw{background:#e4f6fb;color:#1f6675;border:1px solid #b6e2ee}
  .rule{color:var(--midori)} .rule.b{color:var(--aka)}
  /* ルール説明ボタン / モーダル */
  .ghost{cursor:pointer;margin-left:auto;background:#fff;color:var(--accent-ink);
     border:1px solid var(--line);border-radius:10px;padding:8px 14px;font-size:13px;
     font-weight:700;box-shadow:0 3px 10px var(--glow)}
  .ghost:hover{border-color:var(--accent)}
  .modal-overlay{position:fixed;inset:0;background:rgba(10,45,55,.35);
     display:none;align-items:flex-start;justify-content:center;padding:24px;z-index:50;
     overflow:auto}
  .modal-overlay.open{display:flex}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:16px;
     max-width:640px;width:100%;padding:22px 24px;box-shadow:0 18px 50px rgba(0,120,140,.25);
     margin:auto}
  .modal-head{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .modal-head h2{margin:0;font-size:19px;color:var(--ink);text-transform:none;letter-spacing:0}
  .modal .close{margin-left:auto;cursor:pointer;background:var(--panel2);border:1px solid var(--line);
     border-radius:9px;width:34px;height:34px;font-size:18px;color:var(--muted);line-height:1}
  .modal .close:hover{color:var(--ink);border-color:var(--accent)}
  .rules-body{font-size:14px;line-height:1.7;color:var(--ink)}
  .rules-body h3{font-size:15px;margin:18px 0 6px;color:var(--accent-ink);
     border-left:4px solid var(--accent);padding-left:8px}
  .rules-body p{margin:6px 0}
  .rules-body ul,.rules-body ol{margin:6px 0;padding-left:22px}
  .rules-body li{margin:4px 0}
  .rules-body .tip{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
     padding:10px 12px;margin:10px 0}
  .rules-body .key{background:#fff7dd;border:1px solid #f0e0a0;border-radius:10px;
     padding:10px 12px;margin:10px 0}
  .rules-body b{color:var(--accent-ink)}
  .pill{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;
     padding:1px 9px;margin-right:4px;border:1px solid var(--line)}
  .pill.aka{color:var(--aka);border-color:var(--aka)}
  .pill.midori{color:var(--midori);border-color:var(--midori)}
  .pill.ao{color:var(--ao);border-color:var(--ao)}
</style>
</head>
<body>
<header>
  <h1>✊✌️🖐 RPS Extend <span style="color:var(--accent)">Online</span></h1>
  <span class="tag">中央サーバー方式 / ブラウザGUI</span>
  <button class="ghost" id="rulesBtn">📖 ルール説明</button>
</header>

<!-- 参加画面 -->
<section id="join" class="card">
  <h2 style="letter-spacing:1px">オンライン対戦に参加</h2>
  <p style="color:var(--muted);font-size:13px">名前を入れて参加すると、もう1人の参加を待って自動で対戦が始まります。</p>
  <input id="name" maxlength="16" placeholder="あなたの名前" />
  <div><button class="btn" id="joinBtn">参加する</button></div>
  <div id="joinMsg" style="color:var(--muted);margin-top:14px;font-size:13px"></div>
</section>

<!-- 対戦画面 -->
<section id="game" class="wrap" style="display:none">
  <div>
    <div class="scoreboard">
      <div><div class="turn" id="turnLbl">ターン -</div>
           <div class="me" id="meName">あなた</div><div class="score" id="meScore">0</div></div>
      <div style="font-size:22px;color:var(--muted)">VS</div>
      <div style="text-align:right"><div class="turn">相手</div>
           <div id="oppName">相手</div><div class="score" id="oppScore">0</div></div>
    </div>
    <div class="card" id="prompt">
      <h2>あなたの操作</h2>
      <div id="promptText"></div>
      <div class="opts" id="opts"></div>
      <div class="waiting" id="waiting" style="display:none">相手の操作を待っています<span class="dot">…</span></div>
    </div>
    <div id="banner"></div>
  </div>
  <div>
    <div class="card" style="margin-bottom:16px">
      <h2>盤面</h2>
      <div id="board">接続中…</div>
    </div>
    <div class="card">
      <h2>実況ログ</h2>
      <div id="log"></div>
    </div>
  </div>
</section>

<!-- ルール説明モーダル（初めての人向け） -->
<div class="modal-overlay" id="rules">
  <div class="modal">
    <div class="modal-head">
      <h2>🔰 はじめての方へ — 遊び方</h2>
      <button class="close" id="rulesClose" title="閉じる">×</button>
    </div>
    <div class="rules-body">
      <p>「じゃんけん」に<b>読み合い</b>と<b>情報戦</b>を足した、2人用の対戦ゲームです。
         運の要素はほぼゼロ。カードは最初から全部持っています。</p>

      <h3>🎯 目的</h3>
      <p><b>6ターン</b>対戦して、<b>得点が高い方の勝ち</b>。同点なら「サドンデス」で決着します。</p>

      <h3>🃏 使うカード（3種類）</h3>
      <ul>
        <li><b>RPSカード（9枚）</b>：グー・チョキ・パー ×
          <span class="pill aka">赤</span><span class="pill midori">緑</span><span class="pill ao">青</span>。
          色は勝敗に関係なく、読み合いのための目印です。</li>
        <li><b>効果カード（6枚）</b>：「◯◯で勝つと +1点」。毎ターン1枚を<b>公開して</b>使います。</li>
        <li><b>能力カード（6枚）</b>：相手の手札を覗く／相手の伏せ札を当てる／守る。毎ターン1枚使います。</li>
      </ul>

      <div class="key">
        <b>いちばん大事：「封印」と「本命」は別のカードです。</b><br>
        ・<b>封印</b>＝毎ターン1枚伏せる“おとり”。相手に当てられると失点のリスクですが、
          <b>ターン終わりに手札へ戻り、減りません</b>。<br>
        ・<b>本命</b>＝実際にじゃんけんする1枚。伏せて同時に公開し、<b>使ったら捨てます（減る）</b>。
      </div>

      <h3>🔁 1ターンの流れ</h3>
      <ol>
        <li><b>封印</b>：RPSを1枚伏せる（おとり。最後まで見せません）</li>
        <li><b>効果カード</b>：「◯で勝てば+1」を1枚公開して使う</li>
        <li><b>能力カード</b>：覗き見／guess（伏せ札当て）／guard（守り）から1枚</li>
        <li><b>本命</b>：実際に戦うRPSを1枚、両者<b>同時に</b>伏せる</li>
        <li><b>公開・じゃんけん</b>：本命だけ公開。勝った方 +1</li>
        <li><b>guess判定</b>：相手の封印を当てていれば加点（守られていなければ）</li>
        <li><b>後始末</b>：本命と使ったカードを捨て、封印は手札へ戻す</li>
      </ol>

      <h3>🏆 点の入り方</h3>
      <ul>
        <li>本命のじゃんけんに<b>勝つ</b> → <b>+1</b></li>
        <li>勝った手が、公開した<b>効果カードの宣言と同じ</b> → さらに <b>+1</b></li>
        <li><b>guess</b>で相手の封印を当てる → 手だけ <b>+1</b>／手＋色 <b>+2</b>
            （序盤の1〜3ターンは手＋色が <b>+3</b>）</li>
        <li><b>guard</b>中に相手が guess してきたら、それを無効化して <b>+1</b></li>
      </ul>

      <h3>🎲 サドンデス（同点のとき）</h3>
      <p>残ったRPSを封印なしで1枚ずつ出し、先に勝った方の勝ち。全部あいこなら引き分けです。</p>

      <div class="tip">
        💡 <b>コツ</b>：相手の<b>本命は毎回公開</b>されるので、覚えておくと相手の残り手が読めます。
        封印は「一度見せた札をあえて伏せる」のも有効。読み切られない立ち回りを目指しましょう。
      </div>
    </div>
  </div>
</div>

<script>
let TOKEN=null, polling=false, gameStarted=false, over=false;

function el(id){return document.getElementById(id)}
function addLog(text){
  const d=document.createElement('div'); d.className='l';
  let cls='';
  if(text.includes('勝ち')||text.includes('的中')||text.includes('+')) cls='rule';
  d.textContent=text; if(cls)d.classList.add(cls);
  const log=el('log'); log.appendChild(d); log.scrollTop=log.scrollHeight;
}

function colorClass(label){
  if(label.includes('赤')) return 'aka';
  if(label.includes('緑')) return 'midori';
  if(label.includes('青')) return 'ao';
  return '';
}

function renderBoard(lines){
  // 得点・ターンを抽出してスコアボードへ、全文はboardへ
  el('board').textContent = lines.join('\n');
  for(const ln of lines){
    let m = ln.match(/ターン\s*(\d+\/\d+)/); if(m) el('turnLbl').textContent='ターン '+m[1];
    m = ln.match(/あなた\((.+?)\):\s*(\d+)点\s+相手\((.+?)\):\s*(\d+)点/);
    if(m){ el('meName').textContent='あなた('+m[1]+')'; el('meScore').textContent=m[2];
           el('oppName').textContent=m[3]; el('oppScore').textContent=m[4]; }
  }
}

function showPrompt(msg){
  el('waiting').style.display='none';
  el('promptText').textContent = msg.text;
  const box=el('opts'); box.innerHTML='';
  (msg.options||[]).forEach(o=>{
    const b=document.createElement('div');
    b.className='opt '+colorClass(o.label);
    b.textContent=o.label;
    b.onclick=()=>{ submitChoice(msg.pid, o.key); };
    box.appendChild(b);
  });
}

function clearPrompt(waiting){
  el('promptText').textContent='';
  el('opts').innerHTML='';
  el('waiting').style.display = waiting ? 'block':'none';
}

function submitChoice(pid, value){
  clearPrompt(true);
  fetch('/choice',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:TOKEN,pid,value})});
}

function showBanner(text){
  const b=el('banner'); b.style.display='block'; b.textContent=text;
  if(text.includes('勝ち')) b.className='win';
  else if(text.includes('負け')) b.className='lose';
  else b.className='draw';
  clearPrompt(false);
}

function handle(msg){
  switch(msg.type){
    case 'MSG': addLog(msg.text); break;
    case 'HELLO':
      gameStarted=true; el('join').style.display='none'; el('game').style.display='grid';
      addLog('▶ '+msg.text); clearPrompt(true); break;
    case 'STATE': renderBoard(msg.lines||[]); break;
    case 'PROMPT': showPrompt(msg); break;
    case 'GAMEOVER': over=true; addLog('■ '+msg.text); showBanner(msg.text); break;
  }
}

async function poll(){
  if(!TOKEN||over) return;
  try{
    const r=await fetch('/poll?token='+TOKEN);
    const d=await r.json();
    (d.messages||[]).forEach(handle);
  }catch(e){/* ネットワーク一時エラーは無視 */}
  if(!over) setTimeout(poll, 700);
}

el('joinBtn').onclick=async()=>{
  const name=el('name').value.trim()||'Player';
  el('joinBtn').disabled=true;
  el('joinMsg').textContent='サーバーに接続中…';
  try{
    const r=await fetch('/join',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name})});
    const d=await r.json();
    TOKEN=d.token;
    el('joinMsg').textContent='参加しました。対戦相手を待っています…';
    poll();
  }catch(e){
    el('joinMsg').textContent='接続に失敗しました。サーバーを確認してください。';
    el('joinBtn').disabled=false;
  }
};
el('name').addEventListener('keydown',e=>{ if(e.key==='Enter') el('joinBtn').click(); });

// ルール説明モーダルの開閉
function openRules(){ el('rules').classList.add('open'); }
function closeRules(){ el('rules').classList.remove('open'); }
el('rulesBtn').onclick=openRules;
el('rulesClose').onclick=closeRules;
el('rules').addEventListener('click',e=>{ if(e.target===el('rules')) closeRules(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeRules(); });

// タブを閉じる/離脱する時にサーバーへ即時通知し、対戦を終了させる。
window.addEventListener('pagehide',()=>{
  if(TOKEN && !over){
    try{ navigator.sendBeacon('/leave', JSON.stringify({token:TOKEN})); }catch(e){}
  }
});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="RPS Extend Online Web GUIサーバー")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    threading.Thread(target=reaper, daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[RPS Extend Online] Web GUIサーバー起動: http://{args.host}:{args.port}/")
    print("ブラウザで上記URLを2人が開くと対戦が始まります。(Ctrl-C で終了)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを終了します。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
