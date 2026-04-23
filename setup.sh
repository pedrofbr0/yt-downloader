#!/usr/bin/env bash
# setup.sh — Provisionamento do yt-downloader na VM Oracle Cloud
# ==============================================================
# Rode UMA VEZ após ter acesso à VM:
#
#   chmod +x setup.sh && sudo ./setup.sh
#
# Pré-requisito: VM Ubuntu 22.04, Docker instalado (n8n já roda nela).
# Se Docker não estiver instalado, o script instala automaticamente.

set -euo pipefail

PROJECT_DIR="/opt/yt-downloader"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

echo ""
echo "===================================================="
echo "  yt-downloader — setup na VM Oracle Cloud"
echo "===================================================="
echo ""

# ----------------------------------------------------------------
# 1. Instalar Docker (se ausente)
# ----------------------------------------------------------------
if ! command -v docker &>/dev/null; then
    echo "[1/7] Instalando Docker..."
    apt-get update -qq
    apt-get install -y ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) \
        signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo "[1/7] Docker instalado."
else
    echo "[1/7] Docker já instalado — OK"
fi

# ----------------------------------------------------------------
# 2. Copiar projeto para /opt/yt-downloader
# ----------------------------------------------------------------
echo "[2/7] Copiando projeto para $PROJECT_DIR ..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$SCRIPT_DIR" != "$PROJECT_DIR" ]]; then
    mkdir -p "$PROJECT_DIR"
    rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='.streamlit/secrets.toml' \
          "$SCRIPT_DIR/" "$PROJECT_DIR/"
fi
echo "[2/7] Projeto em $PROJECT_DIR"

# ----------------------------------------------------------------
# 3. Criar secrets.toml (se ainda não existir)
# ----------------------------------------------------------------
SECRETS_FILE="$PROJECT_DIR/.streamlit/secrets.toml"
if [[ ! -f "$SECRETS_FILE" ]]; then
    echo ""
    echo "[3/7] Criando $SECRETS_FILE ..."
    echo "      Digite uma senha forte para o app (não aparecerá na tela):"
    read -rs APP_PASSWORD
    echo ""
    mkdir -p "$PROJECT_DIR/.streamlit"
    cat > "$SECRETS_FILE" <<EOF
[app]
password = "$APP_PASSWORD"
EOF
    chmod 600 "$SECRETS_FILE"
    echo "[3/7] secrets.toml criado com permissão 600."
else
    echo "[3/7] $SECRETS_FILE já existe — mantido."
fi

# ----------------------------------------------------------------
# 4. Criar pasta de downloads
# ----------------------------------------------------------------
mkdir -p "$PROJECT_DIR/downloads"
echo "[4/7] Pasta downloads/ OK"

# ----------------------------------------------------------------
# 5. Descobrir rede do n8n e conectar nginx + yt-downloader a ela
# ----------------------------------------------------------------
echo ""
echo "[5/7] Procurando rede do n8n..."
N8N_NETWORK=$(docker inspect $(docker ps --filter "name=n8n" -q 2>/dev/null | head -1) \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null || true)

if [[ -n "$N8N_NETWORK" ]]; then
    echo "      Rede do n8n detectada: $N8N_NETWORK"
    echo "      Após subir os containers, execute para conectar nginx a ela:"
    echo ""
    echo "        docker network connect $N8N_NETWORK nginx-proxy"
    echo ""
    echo "      Depois edite nginx/nginx.conf para adicionar o upstream do n8n."
else
    echo "      n8n não encontrado ou sem container rodando — pule esta etapa por enquanto."
fi

# ----------------------------------------------------------------
# 6. Subir containers
# ----------------------------------------------------------------
echo "[6/7] Subindo containers com docker compose..."
cd "$PROJECT_DIR"
docker compose pull nginx 2>/dev/null || true
docker compose build --no-cache yt-downloader
docker compose up -d
echo "[6/7] Containers iniciados."

docker compose ps

# ----------------------------------------------------------------
# 7. Abrir porta 80 no firewall da VM (Oracle Cloud usa iptables)
# ----------------------------------------------------------------
echo ""
echo "[7/7] Abrindo porta 80 no iptables..."
if iptables -C INPUT -m state --state NEW -p tcp --dport 80 -j ACCEPT 2>/dev/null; then
    echo "      Porta 80 já aberta — OK"
else
    iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
    # Persistir regras (Debian/Ubuntu: netfilter-persistent)
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save
    else
        apt-get install -y -qq iptables-persistent
        netfilter-persistent save
    fi
    echo "      Porta 80 aberta e persistida."
fi

echo ""
echo "===================================================="
echo "  Setup concluído!"
echo ""
echo "  Acesso: http://$(curl -s ifconfig.me 2>/dev/null || echo '<IP_DA_VM>')/"
echo ""
echo "  Próximos passos manuais (Oracle Cloud Console):"
echo "  1. VCN → Sub-rede pública → Security List"
echo "  2. Add Ingress Rule: Protocol TCP, Dest Port 80"
echo ""
echo "  Para integrar o n8n ao nginx depois:"
echo "  - Edite nginx/nginx.conf (descomente bloco n8n)"
echo "  - docker network connect <rede_n8n> nginx-proxy"
echo "  - docker compose restart nginx"
echo "===================================================="
