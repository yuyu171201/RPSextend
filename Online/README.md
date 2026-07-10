# RPS Extend Online（ジャンケン・エクステンド オンライン対戦）

`spec.md` の中央サーバー方式に基づく、**1対1オンライン対戦**の実装です。
**ブラウザで遊ぶGUI版**（推奨）と、ターミナルで遊ぶ**CUI版**の2つのクライアントを用意しています。
どちらも同じ対戦エンジン（`engine.py`）・同じルールで動作します。
ルールは [`../Docs/RPSextend/latest.md`](../Docs/RPSextend/latest.md)（ver2.5.0）と [`../inst.md`](../inst.md) に準拠します。

- **言語 / 依存**: Python 3（標準ライブラリのみ。インストール不要）
- **接続方式**: 中央サーバー（クライアント・サーバー型）
- **状態管理**: 全状態をサーバーが一元管理（クライアント側からのチート不可）
- **情報の秘匿**: 両者の選択が揃うまで相手画面に表示しない、という表示制御で実現

## ファイル構成

| ファイル | 役割 |
|---|---|
| `web_server.py` | **GUI版サーバー**。ブラウザにゲーム画面を配信し、HTTPで対戦を仲介 |
| `server.py`     | CUI版サーバー（TCP）。ターミナルクライアント用 |
| `client.py`     | CUI版クライアント（ターミナル） |
| `engine.py`     | 対戦エンジン（マッチング進行・ステートマシン・判定）。GUI/CUI共用 |
| `game.py`       | カード定義・盤面状態・勝敗/得点ロジック |
| `protocol.py`   | TCP通信（改行区切りJSON）ユーティリティ |

---

## 遊び方 A: ブラウザGUI版（推奨）

### 1. サーバーを起動（どこか1台）

```bash
cd Online
python3 web_server.py                 # http://0.0.0.0:8000/ で待ち受け
# ポートを変える場合:
python3 web_server.py --host 0.0.0.0 --port 8000
```

### 2. プレイヤー2人がブラウザで開く

各プレイヤーがブラウザで **`http://<サーバーのIP>:8000/`** を開きます
（同じPCで試すなら `http://127.0.0.1:8000/`）。
名前を入れて「参加する」を押すと、もう1人の参加を待って自動で対戦が始まります。

### 3. 操作

画面の指示に従って、**カード（ボタン）をクリック**するだけです。
- 色付きカード（赤/緑/青）は枠色で見分けられます。
- 盤面・スコア・実況ログがリアルタイムに更新されます。
- 相手の番のときは「相手の操作を待っています…」と表示されます。

---

## 遊び方 B: ターミナルCUI版

### 1. サーバーを起動

```bash
cd Online
python3 server.py                     # 0.0.0.0:5000 で待ち受け
```

### 2. プレイヤー2人が接続（ターミナルを2つ）

```bash
cd Online
python3 client.py --host 127.0.0.1 --port 5000 --name アリス
python3 client.py --host 127.0.0.1 --port 5000 --name ボブ
```

別PCからは `--host` にサーバーのIPアドレスを指定します。

### 3. 操作

画面の指示に従い、選択肢の**番号を入力**するだけです。

```
封印するRPSカードを選択:
  1) グー(赤)
  2) グー(緑)
  ...
> 1
```

---

いずれの方式でも、2人が接続すると自動的に対戦が始まります。
3人目以降は待機列に入り、先の対戦が終わると順次マッチングされます。

## ゲームの流れ（各ターン）

サーバーは以下のステートマシンで進行します（`spec.md` §3）。

1. **封印（伏せ）** … RPSを1枚伏せる。相手のguess対象。ターン終わりに手札へ戻る。
2. **効果カード（公開）** … 「◯で勝つと+1」を1枚公開使用。
3. **能力カード（公開）** … 覗き見 / guess(手) / guess(手+色) / 盾 を1枚使用。
   - 覗き見: まず相手が1枚開示 → 自分が最大2枚を指定して開示（非公開2枚は必ず残す）。
4. **本命（伏せ）** … 実際に勝負するRPSを1枚、両者同時に伏せる。
5. **公開・じゃんけん** … 本命のみ公開。勝者+1。宣言手と一致で追加+1。
6. **guess判定** … 封印を当てていれば加点（盾で防がれていなければ）。当否のみ判明。
7. **後始末** … 本命と使用効果/能力を捨て、封印を手札へ戻す。

全6ターンで得点の高い方が勝ち。同点なら**サドンデス**（残りRPSでの純粋なじゃんけん、
先に1勝で決着、全あいこなら引き分け）。

## 動作確認済みの挙動

- 2人接続 → 6ターン進行 → 勝敗表示（サドンデス含む）※GUI/CUI両方
- 封印・本命の秘匿（両者の入力が揃うまで相手に見えない）
- 効果ボーナス / 覗き見(floor2) / guess当否 / 盾による無効化
- 対戦相手の切断検知（残ったプレイヤーへ通知して終了）
- 連続マッチング（対戦終了後、待機列の次の2人が自動対戦）
- GUI: カードの色分け表示、リアルタイムの盤面/スコア/ログ更新、相手待ち表示

---

## 本番運用（サーバー常駐・起動/停止）

本番サーバー（OCI + Nginx + Let's Encrypt）では、`web_server.py` を systemd サービス
**`rps-online`** として常駐させています（構成・デプロイ手順は [`../deploy/`](../deploy/) と
リポジトリ直下の `CLAUDE.md` を参照）。通信経路は
`ブラウザ ──https──▶ Nginx(443) ──proxy──▶ 127.0.0.1:8000 (web_server.py)`。

### サーバー上で実行（SSH ログイン後）

```bash
sudo systemctl start   rps-online   # 起動
sudo systemctl stop    rps-online   # 停止
sudo systemctl restart rps-online   # 再起動（コード更新後など）
systemctl status rps-online --no-pager   # 状態確認
sudo journalctl -u rps-online -f         # ライブログ
```

自動起動（OS 再起動時に自動で立ち上がる設定）の切り替え:

```bash
sudo systemctl enable  rps-online   # OS起動時に自動起動（deploy-rps.sh で設定済み）
sudo systemctl disable rps-online   # 自動起動をやめる
```

### 手元から SSH ワンライナーで

```bash
ssh <user>@<サーバー> 'sudo systemctl start   rps-online'   # 起動
ssh <user>@<サーバー> 'sudo systemctl stop    rps-online'   # 停止
ssh <user>@<サーバー> 'sudo systemctl restart rps-online'   # 再起動
ssh <user>@<サーバー> 'systemctl status rps-online --no-pager | head -n 12'
```

補足:
- `stop` はゲームサーバー（`web_server.py`）だけを止めます。Nginx(443) は動いたままなので、
  停止中にアクセスすると **502 Bad Gateway** が返ります。サイトごと止めるなら
  `sudo systemctl stop nginx` も併用してください。
- このサービスは `Restart=always` 設定のため、プロセスがクラッシュしても自動復帰します。
  完全に止めたいときは手動 kill ではなく必ず `systemctl stop` を使ってください。

### 現在のプレイヤーを確認する（管理用ステータス）

`GET /admin/status` で、いま接続中のプレイヤー（対戦中／待機中）を JSON で確認できます。

```bash
# サーバー上（SSHログイン後）: 内部ポートを直接叩く
curl -s http://127.0.0.1:8000/admin/status | python3 -m json.tool
```

出力例:

```json
{
  "counts": { "total": 3, "waiting": 1, "playing": 2, "dead": 0 },
  "waiting": [ { "name": "キャロル", "id": "4c1ae005", "idle_sec": 0.3, "age_sec": 12.0, "dead": false } ],
  "matches": [ { "match": "4fc147e9",
                 "players": [ { "name": "アリス", "id": "ccd7189a", ... },
                              { "name": "ボブ",   "id": "56e75a23", ... } ] } ]
}
```

- `counts`: 合計 / 待機中 / 対戦中 / 切断待ち の人数。
- `matches`: 対戦中のペア（`match` が同じ2人が対戦相手）。`waiting`: マッチ待ちの人。
- `idle_sec` はポーリング最終受信からの経過秒。20秒を超えると reaper が切断扱いにします。

**アクセス制御（既定で安全）:**
- 環境変数 `RPS_ADMIN_TOKEN` **未設定**時は、`/admin/status` は**サーバー内からの loopback 直叩き（`127.0.0.1:8000`）のみ許可**。
  Nginx 経由の公開URLからは `403 Forbidden`（`X-Forwarded-For` が付くため）。
- 公開URL経由でも見たい場合は `RPS_ADMIN_TOKEN` を設定し、`?token=` を付けてアクセス:

  ```bash
  # systemd に環境変数を渡す例（サーバー上）
  sudo systemctl edit rps-online
  #   [Service]
  #   Environment=RPS_ADMIN_TOKEN=<任意の長い文字列>
  sudo systemctl restart rps-online

  curl -s "https://rps.sumyuu.com/admin/status?token=<上と同じ文字列>" | python3 -m json.tool
  ```

  ※トークンは個人情報同様に扱い、リポジトリに直書きしないこと。
