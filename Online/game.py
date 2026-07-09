"""
RPS Extend Online - ゲームロジック / カード定義 / 盤面状態

仕様は ../Docs/RPSextend/latest.md (ver2.5.0) および ../inst.md に準拠する。

用語 (ver2.5.0):
  - 封印 (seal)   : 毎ターン伏せる囮札。相手のguess対象。ターン終わりに手札へ戻る（消耗しない）。
  - 本命 (combat) : 実際に勝負するRPS札。両者同時に伏せ、公開後に捨てる（消耗する）。
  - 効果カード     : 「グー/チョキ/パーで勝つと+1」。毎ターン1枚を公開使用。使ったら捨てる。
  - 能力カード     : show hand ×3 / guess(手) ×1 / guess(手+色) ×1 / guard guess ×1。毎ターン1枚使用。
"""

from dataclasses import dataclass, field
from typing import Optional

# ---- 基本定義 ------------------------------------------------------------

HANDS = ["グー", "チョキ", "パー"]
COLORS = ["赤", "緑", "青"]

# じゃんけんの三すくみ: key が value に勝つ
_BEATS = {"グー": "チョキ", "チョキ": "パー", "パー": "グー"}


def judge(hand_a: str, hand_b: str) -> int:
    """a視点の勝敗。 1=aの勝ち, -1=bの勝ち(aの負け), 0=あいこ。"""
    if hand_a == hand_b:
        return 0
    return 1 if _BEATS[hand_a] == hand_b else -1


@dataclass(frozen=True)
class RPSCard:
    hand: str   # グー / チョキ / パー
    color: str  # 赤 / 緑 / 青

    @property
    def cid(self) -> str:
        return f"{self.hand}{self.color}"

    def __str__(self) -> str:
        return f"{self.hand}({self.color})"


# 能力カードの種別
SHOW_HAND = "show_hand"
GUESS_HAND = "guess_hand"
GUESS_HAND_COLOR = "guess_hand_color"
GUARD = "guard"

ABILITY_LABEL = {
    SHOW_HAND: "覗き見(show hand)",
    GUESS_HAND: "探偵/guess(手)",
    GUESS_HAND_COLOR: "探偵/guess(手+色)",
    GUARD: "盾(guard guess)",
}


def new_rps_deck() -> list:
    """9枚のユニークなRPSカード（手×色）。"""
    return [RPSCard(h, c) for h in HANDS for c in COLORS]


def new_effect_deck() -> list:
    """効果カード6枚。値は「勝ちを宣言する手」。各手×2枚。"""
    return [h for h in HANDS for _ in range(2)]


def new_ability_deck() -> list:
    """能力カード6枚。"""
    return [SHOW_HAND, SHOW_HAND, SHOW_HAND, GUESS_HAND, GUESS_HAND_COLOR, GUARD]


# ---- プレイヤー状態 ------------------------------------------------------

@dataclass
class PlayerState:
    name: str = "Player"
    score: int = 0

    rps_hand: list = field(default_factory=new_rps_deck)
    rps_discard: list = field(default_factory=list)
    effect_hand: list = field(default_factory=new_effect_deck)
    effect_discard: list = field(default_factory=list)
    ability_hand: list = field(default_factory=new_ability_deck)
    ability_discard: list = field(default_factory=list)

    # 現ターンの選択
    seal: Optional[RPSCard] = None       # 封印（伏せ）
    combat: Optional[RPSCard] = None     # 本命（伏せ）
    effect_played: Optional[str] = None  # 使用した効果カード（宣言手）
    ability_played: Optional[str] = None # 使用した能力カード種別
    guess: Optional[tuple] = None        # (mode, hand, color|None)
    guarding: bool = False               # guardを使ったか

    # 開示・推理支援用
    # cid -> 開示されたターン番号（上下マーカー: 直前ターン開示かの判定に使う）
    revealed_turn: dict = field(default_factory=dict)

    def reset_turn(self):
        self.seal = None
        self.combat = None
        self.effect_played = None
        self.ability_played = None
        self.guess = None
        self.guarding = False


# ---- 対戦状態 ------------------------------------------------------------

class GameState:
    """1マッチ分の全状態。サーバーが一元管理する。"""

    TOTAL_TURNS = 6

    def __init__(self, name0: str, name1: str):
        self.players = [PlayerState(name0), PlayerState(name1)]
        self.turn = 1  # 1..6
        self.log = []  # 公開イベントの履歴（両者に見せてよい文字列）

    def opp(self, i: int) -> int:
        return 1 - i

    def add_log(self, text: str):
        self.log.append(f"[T{self.turn}] {text}")


# ---- 判定ロジック --------------------------------------------------------

def resolve_combat(state: GameState) -> dict:
    """本命公開後の勝敗・点数計算。結果dictを返す。"""
    p0, p1 = state.players
    c0, c1 = p0.combat, p1.combat
    result = judge(c0.hand, c1.hand)  # p0視点

    out = {"c0": c0, "c1": c1, "winner": None, "detail": []}
    if result == 0:
        out["detail"].append(f"本命公開: {c0} vs {c1} → あいこ")
        return out

    winner = 0 if result == 1 else 1
    out["winner"] = winner
    wp = state.players[winner]
    wcard = wp.combat
    wp.score += 1
    lines = [f"本命公開: {c0} vs {c1} → {wp.name} の勝ち (+1)"]

    # 効果ボーナス: 勝者の宣言手と勝った本命の手が一致
    if wp.effect_played == wcard.hand:
        wp.score += 1
        lines.append(f"効果ボーナス: 宣言「{wp.effect_played}で勝つ」と一致 (+1)")

    out["detail"] = lines
    return out


def resolve_guesses(state: GameState) -> list:
    """guess/guardの判定と点数計算。各プレイヤーへ通知する結果(dict)のリスト。"""
    results = []
    for i in (0, 1):
        gp = state.players[i]          # guessする側
        tp = state.players[state.opp(i)]  # 封印される側
        if gp.guess is None:
            continue
        mode, ghand, gcolor = gp.guess

        # 相手がguardを使い、かつguess対象になっている → 無効化＋guard側+1
        if tp.guarding:
            tp.score += 1
            state.add_log(f"{tp.name} の盾が発動: {gp.name} のguessを無効化 ({tp.name} +1)")
            results.append({"to": i, "text": "あなたのguessは相手の盾で無効化された。"})
            continue

        seal = tp.seal
        if mode == GUESS_HAND:
            hit = (seal.hand == ghand)
            if hit:
                gp.score += 1
                state.add_log(f"{gp.name} のguess(手)が的中 (+1)")
            results.append({
                "to": i,
                "text": f"guess(手)「{ghand}」→ {'的中! (+1)' if hit else 'はずれ'}",
            })
        else:  # GUESS_HAND_COLOR
            hit = (seal.hand == ghand and seal.color == gcolor)
            pts = 3 if state.turn <= 3 else 2
            if hit:
                gp.score += pts
                state.add_log(f"{gp.name} のguess(手+色)が的中 (+{pts})")
            results.append({
                "to": i,
                "text": f"guess(手+色)「{ghand}{gcolor}」→ "
                        f"{f'的中! (+{pts})' if hit else 'はずれ'}",
            })
    return results


def end_turn(state: GameState):
    """後始末: 本命と使用効果/能力を捨て、封印を手札へ戻す。開示マーカーを更新。"""
    for p in state.players:
        # 本命は捨てる
        if p.combat is not None:
            p.rps_discard.append(p.combat)
        # 封印は手札へ戻す
        if p.seal is not None:
            p.rps_hand.append(p.seal)
        # 効果・能力は捨てる
        if p.effect_played is not None:
            p.effect_discard.append(p.effect_played)
        if p.ability_played is not None:
            p.ability_discard.append(p.ability_played)
        p.reset_turn()
    state.turn += 1


def final_result(state: GameState):
    """6ターン終了時点の勝敗。 (winner|None, text)。同点は None を返す（サドンデスへ）。"""
    s0, s1 = state.players[0].score, state.players[1].score
    if s0 > s1:
        return 0, f"{state.players[0].name} の勝利 ({s0} - {s1})"
    if s1 > s0:
        return 1, f"{state.players[1].name} の勝利 ({s1} - {s0})"
    return None, f"同点 ({s0} - {s1}) → サドンデスへ"
