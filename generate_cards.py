import sys

def generate_html():
    rps_data = [
        ("Rock", "✊", "Scissors", "Paper"),
        ("Paper", "✋", "Rock", "Scissors"),
        ("Scissors", "✌️", "Paper", "Rock")
    ]
    colors = [("R", "#d32f2f"), ("G", "#388e3c"), ("B", "#1976d2")]

    effect_data = [
        ("グー (Rock)", 2),
        ("パー (Paper)", 2),
        ("チョキ (Scissors)", 2)
    ]

    ability_data = [
        ("show hand", 3, "相手の手札を1枚公開させる。<br>その後、自分が相手の非公開手札を2枚指定し強制公開させる。<br><br><span style='font-size:0.8em'>※相手の非公開手札は最低2枚残す</span>"),
        ("guess [手]", 1, "相手の「封印」カードの『手』を宣言する。<br><br>当たれば <b>+1点</b><br><span style='font-size:0.8em'>(当否のみ判明)</span>"),
        ("guess [手+色]", 1, "相手の「封印」カードの『手と色』を宣言する。<br><br>当たれば <b>+2点</b><br><span style='font-size:0.8em'>(1〜3ターン目は <b>+3点</b>)<br>(当否のみ判明)</span>"),
        ("guard guess", 1, "同ターンに相手が自分にguessをしていた場合、そのguessを無効化する。<br><br>さらに <b>自分に+1点</b>")
    ]

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>RPS Extend Cards</title>
<style>
  body { font-family: sans-serif; background: #eee; margin: 0; padding: 20px; }
  @media print {
    body { background: white; margin: 0; padding: 0; }
    .page { page-break-after: always; box-shadow: none; margin: 0; padding: 10mm; }
    .card { border: 1px solid #000 !important; }
  }
  .page {
    width: 210mm;
    min-height: 297mm;
    box-sizing: border-box;
    padding: 10mm;
    margin: 0 auto 20px auto;
    background: white;
    box-shadow: 0 0 5px rgba(0,0,0,0.1);
    display: flex;
    flex-wrap: wrap;
    align-content: flex-start;
    gap: 4mm;
  }
  .card {
    width: 63mm;
    height: 88mm;
    border: 1px dashed #999;
    box-sizing: border-box;
    position: relative;
    padding: 4mm;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
  }
  .diagonal-line {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(to top right, transparent 49.5%, #333 49.5%, #333 50.5%, transparent 50.5%);
    z-index: 0;
  }
  .card-content {
    position: relative;
    z-index: 1;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: rgba(255, 255, 255, 0.85); /* Readability over the diagonal line */
    padding: 2mm;
    border-radius: 4px;
  }
  .card-title {
    font-size: 1.1em;
    font-weight: bold;
    text-align: center;
    border-bottom: 1px solid #ccc;
    padding-bottom: 2mm;
    margin-bottom: 2mm;
  }
  .icon {
    font-size: 2.5em;
    text-align: center;
    margin: auto 0;
  }
  .win-lose {
    font-size: 0.8em;
    margin-top: auto;
  }
  .ability-text {
    font-size: 0.85em;
    text-align: left;
    margin-top: 2mm;
    flex-grow: 1;
  }
  .player-label {
    position: absolute;
    bottom: 1mm;
    right: 2mm;
    font-size: 0.6em;
    color: #666;
    z-index: 1;
  }
</style>
</head>
<body>
"""

    for player in [1, 2]:
        cards = []
        # RPS
        for rps_name, icon, win, lose in rps_data:
            for color_name, color_hex in colors:
                cards.append(f"""
<div class="card">
  <div class="diagonal-line"></div>
  <div class="card-content">
    <div class="card-title" style="color: {color_hex}">{rps_name} [{color_name}]</div>
    <div class="icon">{icon}</div>
    <div class="win-lose">
      <span style="color:#1976d2">〇 勝利: {win}</span><br>
      <span style="color:#d32f2f">✕ 敗北: {lose}</span>
    </div>
  </div>
  <div class="player-label">P{player}</div>
</div>
""")
        # Effects
        for effect_name, count in effect_data:
            for _ in range(count):
                cards.append(f"""
<div class="card">
  <div class="card-content">
    <div class="card-title">効果カード</div>
    <div class="ability-text">
      このターン、<br>
      <div style="text-align:center; font-weight:bold; font-size:1.1em; margin: 10px 0;">{effect_name}</div>
      で勝つと <b>+1点</b>
    </div>
  </div>
  <div class="player-label">P{player}</div>
</div>
""")
        # Abilities
        for ab_name, count, text in ability_data:
            for _ in range(count):
                cards.append(f"""
<div class="card">
  <div class="card-content">
    <div class="card-title">{ab_name}</div>
    <div class="ability-text">
      {text}
    </div>
  </div>
  <div class="player-label">P{player}</div>
</div>
""")
        
        # Paginate (9 cards per page)
        for i in range(0, len(cards), 9):
            html += '<div class="page">\n'
            html += "".join(cards[i:i+9])
            html += '</div>\n'

    html += """
</body>
</html>
"""
    with open("cards.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    generate_html()
