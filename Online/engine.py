"""
RPS Extend Online - 対戦エンジン（トランスポート非依存）

Match/Session はネットワーク実装（TCPソケット / HTTP）に依存しない。
- Session: 1プレイヤーとの入出力窓口。送信は `transport.send(obj)`、
  受信（CHOICE等）は `q`（Queue）へ外部から投入される。
- Match: 2つのSessionで6ターンのステートマシンを進行し勝敗を決定する。

TCP版は server.py、Web(GUI)版は web_server.py がそれぞれ transport と
`q` への投入を担う。
"""

import queue
import threading

import game as G

# Sessionのqへ投入する「切断」センチネル
DISCONNECT = object()


class Disconnected(Exception):
    pass


class Session:
    """1プレイヤーとの入出力。transport.send(obj) で送信、q に受信を投入。"""

    def __init__(self, transport, name: str):
        self.tx = transport
        self.name = name
        self.q: "queue.Queue" = queue.Queue()

    # --- 送信 ---
    def hello(self, text): self.tx.send({"type": "HELLO", "text": text})
    def msg(self, text): self.tx.send({"type": "MSG", "text": text})

    def state(self, lines, view=None):
        """盤面を送信。lines=テキスト版(後方互換), view=GUI用の構造化データ。"""
        obj = {"type": "STATE", "lines": lines}
        if view is not None:
            obj["view"] = view
        self.tx.send(obj)

    def gameover(self, text): self.tx.send({"type": "GAMEOVER", "text": text})

    def prompt(self, pid, text, options, kind=None, source=None):
        # kind:   選択肢をカードで描画する際の種別
        #         ("rps" / "effect" / "ability" / "hand" / "color")。
        # source: 選択肢が「自分の盤面のどのゾーンの札か」。
        #         "rps_hand" / "effect" / "ability" なら盤面のそのカードを直接クリックして選ぶ。
        #         "panel"（既定）なら操作パネルにカード選択肢を出す（盤面に無い選択＝guess等）。
        self.tx.send({
            "type": "PROMPT", "pid": pid, "text": text,
            "mode": "select", "kind": kind, "source": source or "panel",
            "options": options,
        })

    def await_choice(self, pid, allowed):
        """PROMPTへの回答を待つ。allowedに無い値は無視して待ち続ける。"""
        while True:
            item = self.q.get()
            # 相手の切断で対戦を中断する。Disconnected インスタンスが
            # 投入された場合は切断者の名前を保持したまま送出する
            # （自分の手番でなくても中断できるようにするため）。
            if isinstance(item, Disconnected):
                raise item
            if item is DISCONNECT:
                raise Disconnected(self.name)
            if not isinstance(item, dict):
                continue
            if item.get("type") == "CHOICE" and item.get("pid") == pid:
                val = item.get("value")
                if val in allowed:
                    return val

    def close(self):
        try:
            self.tx.close()
        except Exception:
            pass


class Match:
    """2プレイヤーの1対戦を進行する。"""

    def __init__(self, p0: Session, p1: Session):
        self.p = [p0, p1]
        self.state = G.GameState(p0.name, p1.name)

    # ---- 送信ヘルパ ------------------------------------------------------

    def both_msg(self, text):
        for pl in self.p:
            pl.msg(text)

    def broadcast_board(self):
        for i in (0, 1):
            self._send_board(i)

    def _rps_options(self, cards):
        # hand/color も持たせ、GUI が RPS ミニカードとして描画できるようにする。
        return [{"key": c.cid, "label": str(c), "hand": c.hand, "color": c.color}
                for c in cards]

    # ---- 盤面表示 --------------------------------------------------------

    @staticmethod
    def _card(c):
        return {"hand": c.hand, "color": c.color}

    def _build_view(self, i):
        """プレイヤー i 視点の盤面を GUI 用の構造化データにする。
        秘匿情報（相手の手札・封印の中身）は一切含めない。"""
        st = self.state
        me = st.players[i]
        opp = st.players[st.opp(i)]

        def revealed(pl, c):
            # 直前ターン or 今ターンに開示された札（上下マーカー相当）
            return pl.revealed_turn.get(c.cid) in (st.turn, st.turn - 1)

        me_rps = [{"hand": c.hand, "color": c.color, "revealed": revealed(me, c)}
                  for c in me.rps_hand]
        opp_revealed = [self._card(c) for c in opp.rps_hand if revealed(opp, c)]

        return {
            "turn": st.turn,
            "total": G.GameState.TOTAL_TURNS,
            "me": {
                "name": me.name,
                "score": me.score,
                "rps": me_rps,
                "seal": self._card(me.seal) if me.seal else None,
                "combat": self._card(me.combat) if me.combat else None,
                "effect": list(me.effect_hand),
                "ability": list(me.ability_hand),
                "effect_played": me.effect_played,
                "ability_played": me.ability_played,
            },
            "opp": {
                "name": opp.name,
                "score": opp.score,
                "public": [self._card(c) for c in opp.rps_discard],
                "hidden": len(opp.rps_hand) + (1 if opp.seal else 0),
                "revealed": opp_revealed,
                "effect_used": list(opp.effect_discard),
                "ability_used": list(opp.ability_discard),
            },
        }

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

        def mark(c):
            t = me.revealed_turn.get(c.cid)
            flag = " *開示中*" if t in (st.turn, st.turn - 1) else ""
            return str(c) + flag
        L.append("あなたのRPS手札: " + " / ".join(mark(c) for c in me.rps_hand))
        L.append("あなたの効果カード: " + " / ".join(f"{h}で勝つ" for h in me.effect_hand))
        L.append("あなたの能力カード: "
                 + " / ".join(G.ABILITY_LABEL[a] for a in me.ability_hand))

        opp_rps_public = [str(c) for c in opp.rps_discard]
        L.append("-" * 52)
        L.append("相手の使用済み本命(公開): "
                 + (" / ".join(opp_rps_public) if opp_rps_public else "なし"))
        L.append(f"相手の残りRPS枚数(手札+封印): {len(opp.rps_hand) + (1 if opp.seal else 0)}")
        opp_revealed = [c for c in opp.rps_hand
                        if opp.revealed_turn.get(c.cid) in (st.turn, st.turn - 1)]
        if opp_revealed:
            L.append("相手の開示中カード: " + " / ".join(str(c) for c in opp_revealed))
        L.append("相手の使用済み効果: "
                 + (" / ".join(f"{h}で勝つ" for h in opp.effect_discard) or "なし"))
        L.append("相手の使用済み能力: "
                 + (" / ".join(G.ABILITY_LABEL[a] for a in opp.ability_discard) or "なし"))

        L.append("-" * 52)
        L.append("[ログ]")
        for line in st.log[-6:]:
            L.append("  " + line)
        L.append("=" * 52)
        self.p[i].state(L, self._build_view(i))

    # ---- 入力ヘルパ ------------------------------------------------------

    def prompt_both(self, specs):
        """specs = {i: (pid, text, options, kind, source)}。両者に同時要求し回答を返す。"""
        for i, (pid, text, options, kind, source) in specs.items():
            self.p[i].prompt(pid, text, options, kind, source)
        results = {}
        for i, (pid, _text, options, _kind, _source) in specs.items():
            allowed = {o["key"] for o in options}
            results[i] = self.p[i].await_choice(pid, allowed)
        return results

    def prompt_one(self, i, pid, text, options, kind=None, source=None):
        self.p[i].prompt(pid, text, options, kind, source)
        allowed = {o["key"] for o in options}
        return self.p[i].await_choice(pid, allowed)

    # ---- フェイズ処理 ----------------------------------------------------

    def phase_seal(self):
        st = self.state
        self.both_msg("― 封印フェイズ: 伏せるRPS(囮)を1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            opts = self._rps_options(st.players[i].rps_hand)
            specs[i] = (f"seal-{st.turn}", "封印するRPSカードを盤面から選択:",
                        opts, "rps", "rps_hand")
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            card = next(c for c in me.rps_hand if c.cid == res[i])
            me.rps_hand.remove(card)
            me.seal = card
            self.p[i].msg(f"あなたは {card} を封印しました（相手には見えません）。")
        self.both_msg("両者が封印しました。")

    def phase_effect(self):
        st = self.state
        self.both_msg("― 効果フェイズ: 使用する効果カードを1枚選んでください(公開) ―")
        specs = {}
        for i in (0, 1):
            hand = st.players[i].effect_hand
            seen, opts = [], []
            for h in hand:
                if h not in seen:
                    seen.append(h)
                    opts.append({"key": h, "label": f"{h}で勝つ"})
            specs[i] = (f"effect-{st.turn}", "使う効果カードを盤面から選択:",
                        opts, "effect", "effect")
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            me.effect_hand.remove(res[i])
            me.effect_played = res[i]
        for i in (0, 1):
            st.add_log(f"{st.players[i].name} の効果: 「{st.players[i].effect_played}で勝つ」")
        self.both_msg(
            f"効果公開 → {st.players[0].name}: 「{st.players[0].effect_played}で勝つ」 / "
            f"{st.players[1].name}: 「{st.players[1].effect_played}で勝つ」")

    def phase_ability(self):
        st = self.state
        self.both_msg("― 能力フェイズ: 使用する能力カードを1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            hand = st.players[i].ability_hand
            seen, opts = [], []
            for a in hand:
                if a not in seen:
                    seen.append(a)
                    opts.append({"key": a, "label": G.ABILITY_LABEL[a]})
            specs[i] = (f"ability-{st.turn}", "使う能力カードを盤面から選択:",
                        opts, "ability", "ability")
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            me.ability_hand.remove(res[i])
            me.ability_played = res[i]
            st.add_log(f"{me.name} の能力: {G.ABILITY_LABEL[res[i]]}")
        self.both_msg(
            f"能力公開 → {st.players[0].name}: {G.ABILITY_LABEL[st.players[0].ability_played]} / "
            f"{st.players[1].name}: {G.ABILITY_LABEL[st.players[1].ability_played]}")

        for i in (0, 1):
            if st.players[i].ability_played == G.SHOW_HAND:
                self._do_show_hand(actor=i)

        for i in (0, 1):
            ap = st.players[i].ability_played
            if ap == G.GUARD:
                st.players[i].guarding = True
                self.p[i].msg("盾を構えました。相手があなたの封印をguessすれば無効化＋あなたに+1。")
            elif ap in (G.GUESS_HAND, G.GUESS_HAND_COLOR):
                self._declare_guess(actor=i, mode=ap)

    def _do_show_hand(self, actor):
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
        opts = self._rps_options(hand)
        chosen = self.prompt_one(
            target_i, f"showhand-self-{st.turn}-{actor}",
            "覗き見: 盤面の自分の手札から、開示する1枚を選択:", opts, "rps", "rps_hand")
        revealed.append(next(c for c in hand if c.cid == chosen))

        actor_allow = min(2, max_total - 1)
        remaining = [c for c in hand if c not in revealed]
        for n in range(actor_allow):
            if not remaining:
                break
            opts = self._rps_options(remaining)
            pick = self.prompt_one(
                actor, f"showhand-force-{st.turn}-{actor}-{n}",
                f"覗き見: 開示させる相手のカードを指定 ({n + 1}/{actor_allow}):",
                opts, "rps", "panel")
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
            actor, f"guess-hand-{st.turn}", "guess: 相手の封印の『手』を予想:", hand_opts, "hand")
        gcolor = None
        if mode == G.GUESS_HAND_COLOR:
            color_opts = [{"key": c, "label": c} for c in G.COLORS]
            gcolor = self.prompt_one(
                actor, f"guess-color-{st.turn}", "guess: 相手の封印の『色』を予想:", color_opts, "color")
        st.players[actor].guess = (mode, ghand, gcolor)
        self.p[actor].msg(f"guessを宣言: {ghand}{gcolor or ''}（結果は勝負後に判明）")

    def phase_combat(self):
        st = self.state
        self.both_msg("― 本命フェイズ: 実際に勝負するRPSを1枚選んでください ―")
        specs = {}
        for i in (0, 1):
            opts = self._rps_options(st.players[i].rps_hand)
            specs[i] = (f"combat-{st.turn}", "本命(勝負)のRPSを盤面から選択:",
                        opts, "rps", "rps_hand")
        res = self.prompt_both(specs)
        for i in (0, 1):
            me = st.players[i]
            card = next(c for c in me.rps_hand if c.cid == res[i])
            me.rps_hand.remove(card)
            me.combat = card
            self.p[i].msg(f"あなたの本命: {card}（相手には未公開）")
        self.both_msg("両者が本命を伏せました。公開します...")

    def phase_reveal(self):
        st = self.state
        out = G.resolve_combat(st)
        for line in out["detail"]:
            st.add_log(line)
        self.both_msg("【本命公開】")
        for line in out["detail"]:
            self.both_msg("  " + line)

        gres = G.resolve_guesses(st)
        if gres:
            self.both_msg("【guess判定】")
        for r in gres:
            self.p[r["to"]].msg("  " + r["text"])

    def play_turn(self):
        st = self.state
        self.broadcast_board()
        self.phase_seal()
        self.phase_effect()
        self.broadcast_board()
        self.phase_ability()
        self.broadcast_board()
        self.phase_combat()
        self.phase_reveal()
        G.end_turn(st)
        self.broadcast_board()

    def sudden_death(self):
        st = self.state
        self.both_msg("=" * 52)
        self.both_msg("★ サドンデス ★ 残ったRPSで純粋なじゃんけん。先に1勝した方が勝者。")
        rnd = 0
        while all(len(p.rps_hand) > 0 for p in st.players):
            rnd += 1
            self.broadcast_board()
            self.both_msg(f"― サドンデス 第{rnd}戦 ―")
            specs = {}
            for i in (0, 1):
                opts = self._rps_options(st.players[i].rps_hand)
                specs[i] = (f"sd-{rnd}", "出すRPSを盤面から選択:", opts, "rps", "rps_hand")
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
        return None

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
                except Exception:
                    pass
        finally:
            for pl in self.p:
                pl.close()

    def _finish(self, winner, text):
        st = self.state
        for i in (0, 1):
            self._send_board(i)
            if winner is None:
                self.p[i].gameover(
                    f"【結果】引き分け  ({st.players[0].score}-{st.players[1].score})\n{text}")
            elif winner == i:
                self.p[i].gameover(f"【結果】あなたの勝ち！  {text}")
            else:
                self.p[i].gameover(f"【結果】あなたの負け…  {text}")
