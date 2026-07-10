#!/usr/bin/env bash
#
# OCI (Ubuntu) 上に Nginx + Let's Encrypt で HTTPS を構築するスクリプト。
# 個人情報(ドメイン/メール)は同ディレクトリの config.env から読み込みます。
# （config.env は .gitignore 済みなので GitHub には公開されません）
#
# 使い方:
#   1. cp config.env.example config.env して自分の値を記入
#   2. sudo ./setup-nginx-https.sh
#
# ※ 事前に OCIコンソールのセキュリティリスト/NSG で
#    ポート 80, 443 (TCP, 0.0.0.0/0) の Ingress を開けておくこと。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
if [[ -f "${SCRIPT_DIR}/config.env" ]]; then
  source "${SCRIPT_DIR}/config.env"
else
  echo "エラー: config.env がありません。'cp config.env.example config.env' して値を記入してください。" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "sudo で実行してください: sudo ./setup-nginx-https.sh" >&2
  exit 1
fi
: "${DOMAIN:?config.env に DOMAIN を設定してください}"
: "${EMAIL:?config.env に EMAIL を設定してください}"

echo "==> [1/5] パッケージ更新 & Nginx / Certbot インストール"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx iptables-persistent

echo "==> [2/5] インスタンス内 iptables でポート 80/443 を開放"
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
netfilter-persistent save

echo "==> [3/5] Nginx 起動 & server_name 設定"
systemctl enable --now nginx
CERTBOT_DOMAINS="-d ${DOMAIN}"
SERVER_NAMES="${DOMAIN}"
for d in ${EXTRA_DOMAINS:-}; do
  CERTBOT_DOMAINS="${CERTBOT_DOMAINS} -d ${d}"
  SERVER_NAMES="${SERVER_NAMES} ${d}"
done
CONF="/etc/nginx/sites-available/${DOMAIN}.conf"
cat > "${CONF}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAMES};
    root /var/www/html;
    index index.html index.nginx-debian.html;
    location / { try_files \$uri \$uri/ =404; }
}
EOF
ln -sf "${CONF}" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> [4/5] Let's Encrypt 証明書を取得"
certbot --nginx ${CERTBOT_DOMAINS} --non-interactive --agree-tos --redirect -m "${EMAIL}"

echo "==> [5/5] 自動更新の確認"
systemctl enable certbot.timer 2>/dev/null || true
certbot renew --dry-run

echo ""
echo "完了! https://${DOMAIN} にアクセスして確認してください。"
