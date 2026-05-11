#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
#  J.A.R.V.I.S  —  Oracle Cloud Ubuntu Server Setup
#  Run once on a fresh Ubuntu 22.04 instance:
#    chmod +x setup_server.sh && sudo ./setup_server.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

JARVIS_PORT=8000
JARVIS_USER="ubuntu"          # default Oracle Cloud SSH user
REPO_DIR="/opt/jarvis"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[JARVIS]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
fail() { echo -e "${RED}[ FAIL ]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && fail "Run as root:  sudo ./setup_server.sh"

# ── 1. System update ──────────────────────────────────────────────────────────
log "Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq git curl ca-certificates gnupg lsb-release
ok "System updated."

# ── 2. Docker installation ────────────────────────────────────────────────────
log "Installing Docker..."
if command -v docker &>/dev/null; then
    ok "Docker already installed: $(docker --version)"
else
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) \
        signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    systemctl enable docker
    systemctl start docker
    usermod -aG docker "${JARVIS_USER}"
    ok "Docker installed: $(docker --version)"
fi

# ── 3. Firewall — Oracle Cloud uses iptables (NOT ufw by default) ─────────────
log "Opening port ${JARVIS_PORT} in iptables..."

# Accept new + established connections on JARVIS_PORT
iptables -C INPUT -p tcp --dport "${JARVIS_PORT}" -m state \
    --state NEW,ESTABLISHED -j ACCEPT 2>/dev/null \
|| iptables -I INPUT -p tcp --dport "${JARVIS_PORT}" -m state \
    --state NEW,ESTABLISHED -j ACCEPT

# Persist across reboots
apt-get install -y -qq iptables-persistent
netfilter-persistent save
ok "Port ${JARVIS_PORT} opened in iptables."

# NOTE: You must ALSO open the port in the OCI Console:
#   Networking → VCN → Security Lists → Ingress Rules → Add TCP 8000

# ── 4. Project directory ──────────────────────────────────────────────────────
log "Creating project directory at ${REPO_DIR}..."
mkdir -p "${REPO_DIR}"
chown "${JARVIS_USER}:${JARVIS_USER}" "${REPO_DIR}"
ok "Directory ready."

# ── 5. Systemd service (auto-start on reboot) ─────────────────────────────────
log "Installing JARVIS systemd service..."
cat > /etc/systemd/system/jarvis.service << EOF
[Unit]
Description=JARVIS AI Backend
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
User=${JARVIS_USER}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable jarvis.service
ok "Systemd service 'jarvis' registered (starts on boot)."

# ── 6. Summary ────────────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "<your-oracle-ip>")

echo ""
echo "════════════════════════════════════════════════════════"
echo "  JARVIS Server Setup Complete"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  Public IP    : ${PUBLIC_IP}"
echo "  API endpoint : http://${PUBLIC_IP}:${JARVIS_PORT}"
echo "  Project dir  : ${REPO_DIR}"
echo ""
echo "  Next steps:"
echo "  1. Copy project:  see deploy.sh on your laptop"
echo "  2. Add .env:      scp .env ${JARVIS_USER}@${PUBLIC_IP}:${REPO_DIR}/"
echo "  3. Start stack:   sudo systemctl start jarvis"
echo "                    — OR —"
echo "                    cd ${REPO_DIR} && docker compose up -d --build"
echo ""
echo "  OCI Console reminder:"
echo "  Networking > VCN > Security Lists > Ingress > Add TCP ${JARVIS_PORT}"
echo ""
