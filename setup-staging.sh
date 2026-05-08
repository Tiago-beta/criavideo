#!/bin/bash
# ============================================
# CriaVideo - Setup Staging on VPS
# Execute as root on the server.
# ============================================
set -e

STAGING_REPO="/opt/levita-video-staging-repo.git"
STAGING_APP="/opt/levita-video-staging"
TRAEFIK_DYNAMIC_DIR="/data/coolify/proxy/dynamic"
PROD_ENV="/opt/levita-video/.env"
TRAEFIK_DYNAMIC_DIR="/data/coolify/proxy/dynamic"

echo "========================================="
echo "  CriaVideo - Setup Staging"
echo "========================================="

echo "[1/5] Creating staging bare repository..."
if [ ! -d "$STAGING_REPO" ]; then
    mkdir -p "$STAGING_REPO"
    git init --bare "$STAGING_REPO"
    echo "  Created $STAGING_REPO"
else
    echo "  $STAGING_REPO already exists"
fi

mkdir -p "$STAGING_APP"
mkdir -p "$STAGING_APP/media"

echo "[2/5] Configuring staging post-receive hook..."
cat > "$STAGING_REPO/hooks/post-receive" << 'HOOKEOF'
#!/bin/bash
set -e

STAGING_REPO="/opt/levita-video-staging-repo.git"
STAGING_APP="/opt/levita-video-staging"

echo "========================================="
echo "  CRIAVIDEO STAGING - Deploying..."
echo "========================================="

# Safety: checkout only tracked git files. Never run git clean here.
git --work-tree="$STAGING_APP" --git-dir="$STAGING_REPO" checkout -f

cd "$STAGING_APP"

if [ -d "$TRAEFIK_DYNAMIC_DIR" ] && [ -f "$STAGING_APP/criavideo-staging.yaml" ]; then
    cp "$STAGING_APP/criavideo-staging.yaml" "$TRAEFIK_DYNAMIC_DIR/criavideo-staging.yaml"
    echo "[Staging] Traefik dynamic config installed"
fi

if [ ! -f .env.staging ]; then
    echo "ERROR: .env.staging not found in $STAGING_APP"
    echo "Create it from .env.example or copy production .env and override SITE_URL/MEDIA_DIR/DATABASE_URL."
    exit 1
fi

docker compose -f docker-compose.staging.yml -p levita-video-staging up -d --build

echo "========================================="
echo "  CriaVideo staging deploy complete"
echo "========================================="
docker compose -f docker-compose.staging.yml -p levita-video-staging ps
HOOKEOF

chmod +x "$STAGING_REPO/hooks/post-receive"

echo "[3/5] Preparing .env.staging..."
if [ ! -f "$STAGING_APP/.env.staging" ]; then
    if [ -f "$PROD_ENV" ]; then
        cp "$PROD_ENV" "$STAGING_APP/.env.staging"
        {
            echo ""
            echo "# Staging overrides"
            echo "ENVIRONMENT=staging"
            echo "SITE_URL=https://staging.criavideo.pro"
            echo "CORS_ORIGINS=https://staging.criavideo.pro"
            echo "PUBLIC_API_URL=https://staging.criavideo.pro/api"
            echo "MEDIA_DIR=/opt/levita-video/media"
        } >> "$STAGING_APP/.env.staging"
        echo "  .env.staging created from production .env with staging overrides"
    else
        echo "  WARNING: production .env not found at $PROD_ENV"
        echo "  Create manually: $STAGING_APP/.env.staging"
    fi
else
    echo "  .env.staging already exists"
fi

echo "[4/5] Installing Traefik staging dynamic config..."
if [ -d "$TRAEFIK_DYNAMIC_DIR" ]; then
    if [ -f "$STAGING_APP/criavideo-staging.yaml" ]; then
        cp "$STAGING_APP/criavideo-staging.yaml" "$TRAEFIK_DYNAMIC_DIR/criavideo-staging.yaml"
        echo "  Traefik staging config copied to $TRAEFIK_DYNAMIC_DIR/criavideo-staging.yaml"
    else
        echo "  WARNING: $STAGING_APP/criavideo-staging.yaml not found yet. It will be available after the first push."
    fi
else
    echo "  WARNING: Traefik dynamic dir not found: $TRAEFIK_DYNAMIC_DIR"
fi

echo "[5/5] Done"
echo ""
echo "On your PC run:"
echo "  git remote add staging root@criavideo.pro:/opt/levita-video-staging-repo.git"
echo "  git push staging master"
echo ""
echo "Required DNS:"
echo "  staging.criavideo.pro -> VPS IP"