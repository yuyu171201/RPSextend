#!/usr/bin/env bash
#
# RPS Extend Online を OCI(Ubuntu) 上に本番デプロイするスクリプト。
# 個人情報(ドメイン/ポート)は同ディレクトリの config.env から読み込みます。
# 前提: setup-nginx-https.sh 実行済み (Nginx + Let's Encrypt が構築済み)。
#
# やること:
#   1. uv をインストール(未導入時)
#   2. web_server.py を systemd サービスとして常駐化 (127.0.0.1:APP_PORT)
#   3. Nginx を DOMAIN → 127.0.0.1:APP_PORT のリバースプロキシに設定
#
# 使い方(コード配置先で実行):
#   sudo ./deploy-rps.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "${SCRIPT_DIR}")"   # deploy/ の親 = リポジトリのルート

# shellcheck source=/dev/null
if [[ -f "${SCRIPT_DIR}/config.env" ]]; then
  source "${SCRIPT_DIR}/config.env"
else
  echo "エラー: config.env がありません。'cp config.env.example config.env' して値を記入してください。" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "sudo で実行してください: sudo ./deploy-rps.sh" >&2
  exit 1
fi
: "${DOMAIN:?config.env に DOMAIN を設定してください}"
APP_PORT="${APP_PORT:-8000}"

echo "==> [1/3] uv をインストール (未導入時のみ)"
apt-get update -y && apt-get install -y curl
if [[ ! -x /root/.local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
( cd "${APP_DIR}/Online" && HOME=/root /root/.local/bin/uv python install 3.12 || true )

echo "==> [2/3] systemd サービスを設定 & 起動"
cat > /etc/systemd/system/rps-online.service <<EOF
[Unit]
Description=RPS Extend Online (web_server.py)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/Online
Environment=HOME=/root
ExecStart=/root/.local/bin/uv run web_server.py --host 127.0.0.1 --port ${APP_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now rps-online
sleep 2
systemctl --no-pager --full status rps-online | head -n 8 || true

echo "==> [3/3] Nginx リバースプロキシを設定"
CONF="/etc/nginx/sites-available/${DOMAIN}.conf"
cat > "${CONF}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
EOF
ln -sf "${CONF}" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo ""
echo "完了! https://${DOMAIN}/ を2人が開くと対戦できます。"
echo "ログ: sudo journalctl -u rps-online -f"
