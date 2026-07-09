"""
RPS Extend Online - 中央サーバー（ゲームホスト）

仕様書 Online/spec.md に基づく中央サーバー方式の実装。
- ゲームの全状態をサーバーが一元管理（クライアントはチート不能）。
- 情報の秘匿は「両者の選択が揃うまで相手画面に出さない」表示制御で実現。
- 6ターンのステートマシンを進行し、同点ならサドンデス。

使い方:
    python3 server.py [--host 0.0.0.0] [--port 5000]

2人が接続すると自動的に対戦が始まる。3人目以降は待機列に入り、
先の対戦が終わると順次マッチングされる。
"""

import argparse
import queue
import socket
import threading

import game as G
from protocol import Connection

_DISCONNECT = object()


class Disconnected(Exception):
    pass


class Player:
    """1接続を表す。受信はreaderスレッドでqueueへ流し込む。"""

    def __init__(self, conn: Connection, name: str):
        self.conn = conn
        self.name = name
        self.q: "queue.Queue" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while True:
            msg = self.conn.recv()
            if msg is None:
                self.q.put(_DISCONNECT)
                break
            self.q.put(msg)

    # --- 送信 ---
    def hello(self, text): self.conn.send({"type": "HELLO", "text": text})
    def msg(self, text): self.conn.send({"type": "MSG", "text": text})
    def state(self, lines): self.conn.send({"type": "STATE", "lines": lines})
    def gameover(self, text): self.conn.send({"type": "GAMEOVER", "text": text})

    def prompt(self, pid, text, options):
        self.conn.send({
            "type": "PROMPT", "pid": pid, "text": text,
            "mode": "select", "options": options,
        })

    def await_choice(self, pid, allowed):
        """PROMPTへの回答を待つ。allowedに無い値は無視して待ち続ける。"""
        while True:
            item = self.q.get()
            if item is _DISCONNECT:
                raise Disconnected(self.name)
            if not isinstance(item, dict):
                continue
            if item.get("type") == "CHOICE" and item.get("pid") == pid:
                val = item.get("value")
                if val in allowed:
                    return val
                # 不正値: 再要求せず、正しい回答が来るまで待つ
                continue


class Match:
    """2プレイヤーの1対戦を進行する。"""

    def __init__(self, p0: Player, p1: Player):
        self.p = [p0, p1]
        self.state = G.GameState(p0.name, p1.name)

    # ---- 送信ヘルパ ------------------------------------------------------

    def both_msg(self, text):
        for pl in self.p:
            pl.msg(text)

    def broadcast_log_tail(self, n=6):
        for i in (0, 1):
            self._send_board(i)

    def _rps_options(self, cards):
        return [{"key": c.cid, "label": str(c)} for c in cards]

    # ---- 盤面表示 --------------------------------------------------------

    def _send_board(self, i):
        st = self.state
        me = st.players[i]
        opp = st.players[st.opp(i)]
        L = []
        L.append("=" * 52)
        L.append(f" ターン {st.turn}/{G.GameState.TOTAL_TURNS}    "
                 f"あなた({me.name}): {me.score}点   "
                 f"相手({opp.name}): {opp.score}点")
        L.append("=" * 52)

        # 自分のRPS手札（開示マーカー付き）
        def mark(c):
            t = me.revealed_turn.get(c.cid)
            flag = " *開示中*" if t == st.turn or t == st.turn - 1 else ""
            return str(c) + flag
        L.append("あなたのRPS手札: " + " / ".join(mark(c) for c in me.rps_hand))
        L.append("あなたの効果カード: " + " / ".join(f"{h}で勝つ" for h in me.effect_hand))
        L.append("あなたの能力カード: "
                 + " / ".join(G.ABILITY_LABEL[a] for a in me.ability_hand))

        # 相手の公開情報
        opp_rps_public = [str(c) for c in opp.rps_discard]
        L.append("-" * 52)
        L.append(f"相手の使用済み本命(公開): "
                 + (" / ".join(opp_rps_public) if opp_rps_public else "なし"))
        L.append(f"相手の残りRPS枚数(手札+封印): {len(opp.rps_hand) + (1 if opp.seal else 0)}")
        # 相手の開示中カード（自分がshow handで見たもの / マーカー有効なもの）
        opp_revealed = [c for c in opp.rps_hand
                        if opp.revealed_turn.get(c.cid) in (st.turn, st.turn - 1)]
        if opp_revealed:
            L.append("相手の開示中カード: " + " / ".join(str(c) for c in opp_revealed))
        L.append(f"相手の使用済み効果: "
                 + (" / ".join(f"{h}で勝つ" for h in opp.effect_discard) or "なし"))
        L.append(f"相手の使用済み能力: "
                 + (" / ".join(G.ABILITY_LABEL[a] for a in opp.ability_discard) or "なし"))

        # ログ末尾
        L.append("-" * 52)
        L.append("[ログ]")
        for line in st.log[-6:]:
            L.append("  " + line)
        L.append("=" * 52)
        self.p[i].state(L)

    # ---- 入力ヘルパ ------------------------------------------------------

    def prompt_both(self, specs):
        """specs = {i: (pid, text, options)}。両者に同時要求し、両者の回答を返す。"""
        for i, (pid, text, options) in specs.items():
            self.p[i].prompt(pid, text, options)
        results = {}
        for i, (pid, _text, options) in specs.items():
            allowed = {o["key"] for o in options}
            results[i] = self.p[i].await_choice(pid, allowed)
        return results

    def prompt_one(self, i, pid, text, options):
        self.p[i].prompt(pid, text, options)
        allowed = {o["key"] for o in options}
        return self.p[i].await_choice(pid, allowed)

    # ---- フェイズ処理 ----------------------------------------------------

    def phase_seal(self):
        """PHASE_1 封印: 各自RPSから1枚を伏せる（相手に見せない）。"""
        st = self.state
        self.both_msg("― 封印フェイズ: 伏せるRPS(囮)を1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            opts = self._rps_options(st.players[i].rps_hand)
            specs[i] = (f"seal-{st.turn}", "封印するRPSカードを選択:", opts)
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            card = next(c for c in me.rps_hand if c.cid == res[i])
            me.rps_hand.remove(card)
            me.seal = card
            self.p[i].msg(f"あなたは {card} を封印しました（相手には見えません）。")
        self.both_msg("両者が封印しました。")

    def phase_effect(self):
        """PHASE_2 効果カード: 各自1枚を選び、両者揃ったら公開。"""
        st = self.state
        self.both_msg("― 効果フェイズ: 使用する効果カードを1枚選んでください(公開) ―")
        specs = {}
        for i in (0, 1):
            hand = st.players[i].effect_hand
            # 同じ手が複数あるので key に手名（重複可）を使うが選択は手単位でよい
            seen = []
            opts = []
            for h in hand:
                if h not in seen:
                    seen.append(h)
                    opts.append({"key": h, "label": f"{h}で勝つ"})
            specs[i] = (f"effect-{st.turn}", "効果カードを選択:", opts)
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            h = res[i]
            me.effect_hand.remove(h)
            me.effect_played = h
        # 公開
        for i in (0, 1):
            st.add_log(f"{st.players[i].name} の効果: 「{st.players[i].effect_played}で勝つ」")
        self.both_msg(
            f"効果公開 → {st.players[0].name}: 「{st.players[0].effect_played}で勝つ」 / "
            f"{st.players[1].name}: 「{st.players[1].effect_played}で勝つ」")

    def phase_ability(self):
        """PHASE_3 能力カード: 各自1枚を選び公開 → show hand → guess/guard 記録。"""
        st = self.state
        self.both_msg("― 能力フェイズ: 使用する能力カードを1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            hand = st.players[i].ability_hand
            seen = []
            opts = []
            for a in hand:
                if a not in seen:
                    seen.append(a)
                    opts.append({"key": a, "label": G.ABILITY_LABEL[a]})
            specs[i] = (f"ability-{st.turn}", "能力カードを選択:", opts)
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            a = res[i]
            me.ability_hand.remove(a)
            me.ability_played = a
            st.add_log(f"{me.name} の能力: {G.ABILITY_LABEL[a]}")
        self.both_msg(
            f"能力公開 → {st.players[0].name}: {G.ABILITY_LABEL[st.players[0].ability_played]} / "
            f"{st.players[1].name}: {G.ABILITY_LABEL[st.players[1].ability_played]}")

        # (1) show hand を先に処理（相手優先）
        for i in (0, 1):
            if st.players[i].ability_played == G.SHOW_HAND:
                self._do_show_hand(actor=i)

        # (2) guess の宣言を記録（guardはフラグのみ）
        for i in (0, 1):
            ap = st.players[i].ability_played
            if ap == G.GUARD:
                st.players[i].guarding = True
                self.p[i].msg("盾を構えました。相手があなたの封印をguessすれば無効化＋あなたに+1。")
            elif ap in (G.GUESS_HAND, G.GUESS_HAND_COLOR):
                self._declare_guess(actor=i, mode=ap)

    def _do_show_hand(self, actor):
        """actorが覗き見を使用。相手が1枚→actorが最大2枚を開示させる（floor2）。"""
        st = self.state
        target_i = st.opp(actor)
        target = st.players[target_i]
        hand = target.rps_hand
        max_total = min(3, max(0, len(hand) - 2))

        self.p[actor].msg(f"覗き見発動: {target.name} の手札を開示させます。")
        self.p[target_i].msg(f"{st.players[actor].name} の覗き見の対象になりました。")

        if max_total <= 0:
            self.p[actor].msg("相手の手札が少なく（非公開2枚を残す規則）、開示は行われませんでした。")
            self.p[target_i].msg("非公開2枚を残す規則により、開示は行われませんでした。")
            return

        revealed = []
        # 相手が1枚選んで開示
        opts = self._rps_options(hand)
        chosen = self.prompt_one(
            target_i, f"showhand-self-{st.turn}-{actor}",
            "覗き見: 自分から開示する1枚を選んでください:", opts)
        card = next(c for c in hand if c.cid == chosen)
        revealed.append(card)

        # actorが残りを最大2枚指定（floor2を満たす範囲）
        actor_allow = min(2, max_total - 1)
        remaining = [c for c in hand if c not in revealed]
        for n in range(actor_allow):
            if len(remaining) <= 0:
                break
            opts = self._rps_options(remaining)
            pick = self.prompt_one(
                actor, f"showhand-force-{st.turn}-{actor}-{n}",
                f"覗き見: 開示させる相手のカードを指定 ({n + 1}/{actor_allow}):", opts)
            card = next(c for c in remaining if c.cid == pick)
            revealed.append(card)
            remaining.remove(card)

        for c in revealed:
            target.revealed_turn[c.cid] = st.turn
        names = " / ".join(str(c) for c in revealed)
        self.p[actor].msg(f"開示されたカード: {names}")
        self.p[target_i].msg(f"あなたのカードが開示されました: {names}")
        st.add_log(f"{st.players[actor].name} の覗き見で {len(revealed)}枚が開示された")

    def _declare_guess(self, actor, mode):
        st = self.state
        hand_opts = [{"key": h, "label": h} for h in G.HANDS]
        ghand = self.prompt_one(
            actor, f"guess-hand-{st.turn}", "guess: 相手の封印の『手』を予想:", hand_opts)
        gcolor = None
        if mode == G.GUESS_HAND_COLOR:
            color_opts = [{"key": c, "label": c} for c in G.COLORS]
            gcolor = self.prompt_one(
                actor, f"guess-color-{st.turn}", "guess: 相手の封印の『色』を予想:", color_opts)
        st.players[actor].guess = (mode, ghand, gcolor)
        label = ghand + (gcolor if gcolor else "")
        self.p[actor].msg(f"guessを宣言: {label}（結果は勝負後に判明）")

    def phase_combat(self):
        """PHASE_4 本命: 手札(封印を除く)から1枚を両者同時に伏せる。"""
        st = self.state
        self.both_msg("― 本命フェイズ: 実際に勝負するRPSを1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            opts = self._rps_options(st.players[i].rps_hand)
            specs[i] = (f"combat-{st.turn}", "本命(勝負)のRPSを選択:", opts)
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            card = next(c for c in me.rps_hand if c.cid == res[i])
            me.rps_hand.remove(card)
            me.combat = card
            self.p[i].msg(f"あなたの本命: {card}（相手には未公開）")
        self.both_msg("両者が本命を伏せました。公開します...")

    def phase_reveal(self):
        """PHASE_5 本命公開・勝敗、PHASE_6 guess判定。"""
        st = self.state
        out = G.resolve_combat(st)
        for line in out["detail"]:
            st.add_log(line)
        self.both_msg("【本命公開】")
        for line in out["detail"]:
            self.both_msg("  " + line)

        # guess判定
        gres = G.resolve_guesses(st)
        if gres:
            self.both_msg("【guess判定】")
        for r in gres:
            self.p[r["to"]].msg("  " + r["text"])

    def play_turn(self):
        st = self.state
        self.broadcast_log_tail()
        self.phase_seal()
        self.phase_effect()
        self.broadcast_log_tail()
        self.phase_ability()
        self.broadcast_log_tail()
        self.phase_combat()
        self.phase_reveal()
        G.end_turn(st)
        self.broadcast_log_tail()

    def sudden_death(self):
        """サドンデス: 残り3枚を封印なしで出し合い、先にラウンドを取った方が勝ち。"""
        st = self.state
        self.both_msg("=" * 52)
        self.both_msg("★ サドンデス ★ 残ったRPSで純粋なじゃんけん。先に1勝した方が勝者。")
        rnd = 0
        while all(len(p.rps_hand) > 0 for p in st.players):
            rnd += 1
            self.broadcast_log_tail()
            self.both_msg(f"― サドンデス 第{rnd}戦 ―")
            specs = {}
            for i in (0, 1):
                opts = self._rps_options(st.players[i].rps_hand)
                specs[i] = (f"sd-{rnd}", "出すRPSを選択:", opts)
            res = self.prompt_both(specs)
            picks = []
            for i in (0, 1):
                me = st.players[i]
                card = next(c for c in me.rps_hand if c.cid == res[i])
                me.rps_hand.remove(card)
                picks.append(card)
            r = G.judge(picks[0].hand, picks[1].hand)
            self.both_msg(f"公開: {st.players[0].name} {picks[0]} vs "
                          f"{st.players[1].name} {picks[1]}")
            if r == 0:
                self.both_msg("→ あいこ。次の1枚へ。")
                st.add_log(f"サドンデス第{rnd}戦: あいこ")
                continue
            winner = 0 if r == 1 else 1
            st.add_log(f"サドンデス第{rnd}戦: {st.players[winner].name} の勝ち")
            return winner
        return None  # 全あいこ

    def run(self):
        try:
            for i in (0, 1):
                self.p[i].hello(
                    f"対戦開始! あなたは {self.state.players[i].name}、"
                    f"相手は {self.state.players[self.state.opp(i)].name} です。")
            for _ in range(G.GameState.TOTAL_TURNS):
                self.play_turn()

            winner, text = G.final_result(self.state)
            if winner is None:
                self.both_msg(text)
                sd = self.sudden_death()
                if sd is None:
                    self._finish(None, "全てあいこ → 引き分け")
                else:
                    self._finish(sd, f"サドンデス制覇: {self.state.players[sd].name} の勝利!")
            else:
                self._finish(winner, text)
        except Disconnected as e:
            for pl in self.p:
                try:
                    pl.gameover(f"相手({e})が切断しました。対戦を終了します。")
                except OSError:
                    pass
        finally:
            for pl in self.p:
                pl.conn.close()

    def _finish(self, winner, text):
        st = self.state
        for i in (0, 1):
            self._send_board(i)
            if winner is None:
                self.p[i].gameover(f"【結果】引き分け  ({st.players[0].score}-{st.players[1].score})\n{text}")
            elif winner == i:
                self.p[i].gameover(f"【結果】あなたの勝ち！  {text}")
            else:
                self.p[i].gameover(f"【結果】あなたの負け…  {text}")


def serve(host, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(f"[RPS Extend Online] サーバー起動: {host}:{port}")
    print("2人の接続を待っています... (Ctrl-C で終了)")

    waiting = []
    lock = threading.Lock()

    def register(conn):
        conn.send({"type": "MSG", "text": "接続しました。名前を送信してください。"})
        msg = conn.recv()
        name = "Player"
        if isinstance(msg, dict) and msg.get("type") == "NAME":
            name = str(msg.get("name") or "Player")[:16]
        player = Player(conn, name)
        player.msg(f"ようこそ {name} さん。対戦相手を待っています...")
        with lock:
            waiting.append(player)
            if len(waiting) >= 2:
                p0, p1 = waiting.pop(0), waiting.pop(0)
                threading.Thread(target=lambda: Match(p0, p1).run(),
                                 daemon=True).start()

    try:
        while True:
            sock, addr = srv.accept()
            print(f"接続: {addr}")
            conn = Connection(sock)
            threading.Thread(target=register, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nサーバーを終了します。")
    finally:
        srv.close()


def main():
    ap = argparse.ArgumentParser(description="RPS Extend Online サーバー")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
