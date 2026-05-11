#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
#  J.A.R.V.I.S  —  Deployment Script  (git-based)
#  Usage:  ./deploy.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ORACLE_IP="158.180.78.104"
SSH_KEY="$HOME/OneDrive/Desktop/stock-ai.pem"
SSH_USER="ubuntu"
REMOTE_DIR="/opt/jarvis"
BRANCH="main"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[deploy]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
fail() { echo -e "${RED}[ FAIL ]${NC} $1"; exit 1; }

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
REMOTE="${SSH_USER}@${ORACLE_IP}"

# ── 1. Push latest commits to GitHub ─────────────────────────────────────────
log "Pushing local commits to origin/${BRANCH}..."
git push origin "${BRANCH}" || fail "git push failed — check remote and auth"
ok "GitHub up to date."

# ── 2. Pull on Oracle server & restart ───────────────────────────────────────
log "Deploying to ${ORACLE_IP}..."
ssh ${SSH_OPTS} "${REMOTE}" bash <<EOF
  set -e
  cd ${REMOTE_DIR}

  # First-time clone guard
  if [ ! -d ".git" ]; then
    echo "[server] No git repo found. Cloning..."
    REPO_URL=\$(git -C /tmp remote get-url origin 2>/dev/null || echo "")
    [ -z "\$REPO_URL" ] && { echo "Set REPO_URL in deploy.sh for first-time clone."; exit 1; }
    git clone "\$REPO_URL" .
  else
    git fetch origin
    git reset --hard origin/${BRANCH}
    echo "[server] Code updated to \$(git rev-parse --short HEAD)"
  fi

  # Rebuild and restart (only jarvis-api; redis keeps its data volume)
  sudo docker compose build --no-cache jarvis-api
  sudo docker compose up -d
  sleep 5
  sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
EOF

ok "Server running latest code."

# ── 3. Health check from laptop ───────────────────────────────────────────────
log "Running health check..."
RESP=$(curl -s --max-time 10 "http://${ORACLE_IP}:8000/health" || echo '{"alive":false}')
echo "  Response: ${RESP}"
echo "${RESP}" | grep -q '"alive":true' && ok "JARVIS is online." || fail "Health check failed."

echo ""
echo "  Endpoint : http://${ORACLE_IP}:8000"
echo "  WebSocket: ws://${ORACLE_IP}:8000/ws"
echo ""
