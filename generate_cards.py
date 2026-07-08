import sys

def generate_html():
    colors = [
        ("R", "赤", "#d32f2f"), 
        ("G", "緑", "#388e3c"), 
        ("B", "青", "#1976d2")
    ]
    
    rps_data = [
        ("グー", "✊", "✌️ チョキ", "✋ パー"),
        ("チョキ", "✌️", "✋ パー", "✊ グー"),
        ("パー", "✋", "✊ グー", "✌️ チョキ")
    ]

    effect_data = [
        ("グーで勝つ", "✊", "グー"),
        ("チョキで勝つ", "✌️", "チョキ"),
        ("パーで勝つ", "✋", "パー")
    ]

    ability_data = [
        ("覗き見", "開示", "", "👁️", "相手の非公開手札を開示させる。<br>①相手が1枚選んで開示<br>②自分が2枚指定して開示<br><span style='font-size:0.8em'>※非公開札は常に2枚以上残す</span>", "能力・毎ターン必ず1枚・使ったら捨てる", 3),
        ("探偵（手）", "封印当て", "+1", "🎯", "相手の<b>封印の「手」</b>を宣言。<br>当たれば <b>+1点</b>（開示しなくても良い）。", "能力・相手が盾なら無効化", 1),
        ("探偵（手＋色）", "封印当て", "+2 / +3", "🎯", "相手の<b>封印の「手＋色」</b>を宣言。<br>当たれば <b>+2点</b><br><span style='font-size:0.8em'>（1〜3ターン目は<b>+3点</b>、開示しなくても良い）</span>", "能力・相手が盾なら無効化", 1),
        ("盾", "防御", "+1", "🛡️", "相手の<b>探偵</b>を無効化し、<b>自分に+1点</b>。<br><span style='font-size:0.8em'>（相手が探偵を使わなければ不発）</span>", "能力・毎ターン必ず1枚・使ったら捨てる", 1)
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
    .page { page-break-after: always; box-shadow: none !important; margin: 0 !important; padding: 10mm !important; }
    .card { border: 1px solid #ccc !important; box-shadow: none !important; }
  }
  .page {
    width: 210mm;
    min-height: 297mm;
    box-sizing: border-box;
    padding: 10mm;
    margin: 0 auto 20px auto;
    background: white;
    box-shadow: 0 0 5px rgba(0,0,0,0.1);
    display: grid;
    grid-template-columns: repeat(3, 63mm);
    gap: 4mm;
    justify-content: center;
    align-content: flex-start;
  }
  .card {
    width: 63mm;
    height: 88mm;
    border-radius: 4mm;
    box-sizing: border-box;
    position: relative;
    background: white;
    overflow: hidden;
    page-break-inside: avoid;
    box-shadow: 0 0 0 1px #ddd inset;
  }
  
  /* RPS Cards */
  .card-rps {
    border: 2px solid var(--c) !important;
  }
  .half {
    position: absolute;
    width: 100%;
    height: 50%;
    padding: 3mm 4mm;
    box-sizing: border-box;
  }
  .top { top: 0; left: 0; }
  .bottom { bottom: 0; right: 0; transform: rotate(180deg); }
  .emoji-big {
    font-size: 2.2em;
    margin-bottom: 1mm;
  }
  .rps-title {
    font-size: 1.3em;
    font-weight: 900;
    margin-bottom: 2mm;
    display: flex;
    align-items: center;
    gap: 2mm;
  }
  .color-circle {
    display: inline-block;
    background: var(--c);
    color: white;
    width: 1.2em;
    height: 1.2em;
    border-radius: 50%;
    text-align: center;
    line-height: 1.2em;
    font-size: 0.7em;
  }
  .win-lose {
    font-size: 0.8em;
    font-weight: bold;
    line-height: 1.4;
  }
  .win-label { color: #2e7d32; display: inline-block; width: 10mm; }
  .lose-label { color: #c62828; display: inline-block; width: 10mm; }
  
  .diagonal-line {
    position: absolute;
    top: 0; left: 0; width: 100%; height: 100%;
    z-index: 0;
  }
  .center-tag {
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    background: white;
    border: 1px solid var(--c);
    color: var(--c);
    padding: 1mm 3mm;
    border-radius: 3mm;
    font-size: 0.7em;
    font-weight: bold;
    z-index: 2;
  }
  
  .revealed-mark {
    position: absolute;
    bottom: 2mm;
    left: 50%;
    transform: translateX(-50%) rotate(180deg);
    font-size: 0.8em;
    font-weight: bold;
    color: var(--c);
    background: white;
    width: 1.6em;
    height: 1.6em;
    line-height: 1.6em;
    text-align: center;
    border: 1px solid var(--c);
    border-radius: 50%;
    z-index: 5;
  }

  /* Special Cards */
  .card-special {
    padding: 0;
  }
  .header {
    color: white;
    padding: 3mm 4mm;
    height: 32%;
    box-sizing: border-box;
    border-radius: 4mm 4mm 0 0;
  }
  .header-small {
    font-size: 0.55em;
    letter-spacing: 0.5px;
    opacity: 0.9;
  }
  .header-title {
    font-size: 1.1em;
    font-weight: bold;
    margin-top: 1mm;
    display: flex;
    align-items: center;
    gap: 2mm;
  }
  .badge {
    background: rgba(0,0,0,0.3);
    padding: 0.5mm 1.5mm;
    border-radius: 2mm;
    font-size: 0.7em;
  }
  .header-subtitle {
    font-size: 0.75em;
    margin-top: 1mm;
    opacity: 0.9;
  }
  .body {
    padding: 2mm 4mm;
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    align-items: center;
  }
  .body-icon {
    font-size: 3em;
    margin: 1mm 0 3mm 0;
  }
  .body-text {
    font-size: 0.8em;
    text-align: left;
    line-height: 1.5;
    width: 100%;
  }
  .footer {
    font-size: 0.55em;
    color: #666;
    border-top: 1px solid #eee;
    padding: 2mm 0;
    margin: 0 4mm;
    text-align: left;
  }
</style>
</head>
<body>
"""

    cards = []
    # RPS (1人分 = 9枚)
    for rps_name, icon, win, lose in rps_data:
        for color_char, color_ja, color_hex in colors:
            cards.append(f"""
<div class="card card-rps" style="--c: {color_hex}">
  <svg class="diagonal-line" viewBox="0 0 100 100" preserveAspectRatio="none">
    <!-- 斜線を左下(0,100)から右上(100,0)へ -->
    <line x1="0" y1="100" x2="100" y2="0" stroke="var(--c)" stroke-width="0.8" stroke-dasharray="2,2" />
  </svg>
  
  <div class="half top">
    <div class="emoji-big">{icon}</div>
    <div class="rps-title">
       {rps_name} <span class="color-circle">{color_char}</span>
    </div>
    <div class="win-lose">
      <div class="win-line"><span class="win-label">WIN</span> {win}</div>
      <div class="lose-line"><span class="lose-label">LOSE</span> {lose}</div>
    </div>
  </div>

  <div class="half bottom">
    <div class="emoji-big">{icon}</div>
    <div class="rps-title">
       {rps_name} <span class="color-circle">{color_char}</span>
    </div>
    <div class="win-lose">
      <div class="win-line"><span class="win-label">WIN</span> {win}</div>
      <div class="lose-line"><span class="lose-label">LOSE</span> {lose}</div>
    </div>
  </div>

  <div class="center-tag">
    {color_ja} / {color_char}
  </div>
  
  <div class="revealed-mark">公</div>
</div>
""")
    # Effects (1人分 = 6枚)
    for effect_name, icon, rps_name in effect_data:
        for _ in range(2):
            cards.append(f"""
<div class="card card-special">
  <div class="header" style="background: #a67c00;">
    <div class="header-small">効果カード / EFFECT</div>
    <div class="header-title">{effect_name} <span class="badge">+1</span></div>
    <div class="header-subtitle">{rps_name} {icon}</div>
  </div>
  <div class="body">
    <div class="body-icon">{icon}</div>
    <div class="body-text">
      この回<b>{rps_name}</b>で勝ったとき、追加で<b>+1</b>。
    </div>
  </div>
  <div class="footer">
    公開して使用・毎ターン必ず1枚・使ったら捨てる
  </div>
</div>
""")
    # Abilities (1人分 = 6枚)
    for ab_name, subtitle, badge, icon, text, footer, count in ability_data:
        badge_html = f'<span class="badge">{badge}</span>' if badge else ''
        for _ in range(count):
            cards.append(f"""
<div class="card card-special">
  <div class="header" style="background: #3e4f5e;">
    <div class="header-small">能力カード / ABILITY</div>
    <div class="header-title">{ab_name} {badge_html}</div>
    <div class="header-subtitle">{subtitle}</div>
  </div>
  <div class="body">
    <div class="body-icon">{icon}</div>
    <div class="body-text">
      {text}
    </div>
  </div>
  <div class="footer">
    {footer}
  </div>
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
