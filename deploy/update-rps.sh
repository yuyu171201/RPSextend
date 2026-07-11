#!/usr/bin/env bash
#
# ローカルの最新コードを本番サーバーへ反映するスクリプト（★ローカルの手元で実行★）。
# 接続情報(IP/ユーザー/鍵)は同ディレクトリの config.env から読み込みます
# （個人情報は .gitignore 済みの config.env に集約し、スクリプト本体には書かない）。
#
# やること:
#   1. サーバーの deploy/config.env を退避（サーバー側の設定を必ず保持するため）
#   2. rsync でローカル → サーバー /tmp/RPSextend へ転送（.git/.venv/.DS_Store/config.env は除外）
#   3. /opt へ入れ替え → config.env を復元 → root所有に → サービス再起動
#   4. 反映確認（サービス稼働 / 版 / カードUI配信）
#
# 前提: 初回セットアップ(setup-nginx-https.sh + deploy-rps.sh)は実行済みで、
#       config.env に DEPLOY_HOST 等が設定されていること。
#
# 使い方(ローカルの手元で):
#   ./deploy/update-rps.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "${SCRIPT_DIR}")"   # deploy/ の親 = リポジトリのルート

# shellcheck source=/dev/null
if [[ -f "${SCRIPT_DIR}/config.env" ]]; then
  source "${SCRIPT_DIR}/config.env"
else
  echo "エラー: config.env がありません。'cp deploy/config.env.example deploy/config.env' して値を記入してください。" >&2
  exit 1
fi

# --- 必須/既定値 ---
: "${DEPLOY_HOST:?config.env に DEPLOY_HOST（サーバーIP）を設定してください}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/RPSextend}"
SERVICE_NAME="${SERVICE_NAME:-rps-online}"
APP_PORT="${APP_PORT:-8000}"

# 鍵パスの先頭 ~ を展開
KEY="${DEPLOY_SSH_KEY/#\~/$HOME}"
[[ -f "$KEY" ]] || { echo "エラー: SSH鍵が見つかりません: $KEY" >&2; exit 1; }

TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
REMOTE_CFG="${REMOTE_DIR}/deploy/config.env"
BACKUP="/tmp/rps-config.env.bak.$$"
STAGE="/tmp/RPSextend.$$"

run_remote() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

echo "==> [0/4] 接続確認: ${TARGET}"
run_remote 'echo "  接続OK: $(whoami)@$(hostname)"'

echo "==> [1/4] サーバーの config.env を退避（存在すれば）"
run_remote "if [[ -f '${REMOTE_CFG}' ]]; then cp '${REMOTE_CFG}' '${BACKUP}' && echo '  退避: ${BACKUP}'; else echo '  (既存 config.env なし。復元はスキップ)'; fi"

echo "==> [2/4] rsync でコード転送 → ${TARGET}:${STAGE}"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude '.git' --exclude 'Online/.venv' --exclude '.DS_Store' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'deploy/config.env' \
  "${APP_DIR}/" "${TARGET}:${STAGE}/"

echo "==> [3/4] /opt へ入れ替え・config.env 復元・再起動"
run_remote "set -e
  sudo rm -rf '${REMOTE_DIR}'
  sudo mv '${STAGE}' '${REMOTE_DIR}'
  if [[ -f '${BACKUP}' ]]; then sudo cp '${BACKUP}' '${REMOTE_CFG}' && rm -f '${BACKUP}'; fi
  sudo chown -R root:root '${REMOTE_DIR}'
  sudo systemctl restart '${SERVICE_NAME}'
  echo '  入れ替え・再起動 完了'"

echo "==> [4/4] 反映確認"
run_remote "
  echo -n '  サービス: '; systemctl is-active '${SERVICE_NAME}'
  echo -n '  版: '; grep -m1 'バージョン' '${REMOTE_DIR}/Docs/RPSextend/latest.md' | sed 's/^- //'
  ok=NG
  for i in 1 2 3 4 5; do
    if curl -sf \"http://127.0.0.1:${APP_PORT}/\" | grep -q 'renderView'; then ok=OK; break; fi
    sleep 2
  done
  echo \"  カードUI(renderView)配信: \$ok\"
"

echo ""
echo "完了! https://${DOMAIN:-<ドメイン>}/ を2人が開くと最新版で対戦できます。"
echo "ログ: ssh -i \"$KEY\" ${TARGET} 'sudo journalctl -u ${SERVICE_NAME} -f'"
