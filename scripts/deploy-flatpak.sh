#!/bin/bash
#
# Deploy termbook as a Flatpak to the NUC server.
# Sets up the website, flatpak repo, and nginx configuration.
#
# Usage: ./scripts/deploy-flatpak.sh [--rebuild] [--skip-tests]
#
# Environment variables:
#   NUC_HOST        - SSH host (default: nuc)
#   NUC_DEPLOY_DIR  - Deploy directory on NUC (default: /var/www/termbook)
#   FLATPAK_ARCH    - Architecture (default: x86_64)
#   FLATPAK_BRANCH  - Branch name (default: master)
#

set -euo pipefail

# Configuration
NUC_HOST="${NUC_HOST:-nuc}"
NUC_DEPLOY_DIR="${NUC_DEPLOY_DIR:-/var/www/termbook}"
FLATPAK_ARCH="${FLATPAK_ARCH:-x86_64}"
FLATPAK_BRANCH="${FLATPAK_BRANCH:-master}"
APP_ID="dev.termbook.Termbook"
MANIFEST_PATH="flatpak/uk.leehanken.termbook.json"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build/flatpak"
OSTREE_REPO="${BUILD_DIR}/repo"
WEBSITE_SRC="${REPO_ROOT}/website"

# Temporary directories
TEMP_DIR=$(mktemp -d)
trap "rm -rf '$TEMP_DIR'" EXIT

# Helper functions
info() {
    echo "[INFO] $*" >&2
}

error() {
    echo "[ERROR] $*" >&2
    exit 1
}

warning() {
    echo "[WARNING] $*" >&2
}

step() {
    echo ""
    echo "=== $* ===" >&2
    echo ""
}

# Parse arguments
REBUILD=false
SKIP_TESTS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rebuild)
            REBUILD=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        *)
            error "Unknown argument: $1"
            ;;
    esac
done

# Validate environment
if ! command -v flatpak &> /dev/null; then
    error "flatpak not installed"
fi

if ! command -v flatpak-builder &> /dev/null; then
    error "flatpak-builder not installed"
fi

if ! command -v ostree &> /dev/null; then
    error "ostree not installed"
fi

if ! command -v gpg &> /dev/null; then
    warning "gpg not installed; repository signing will be skipped"
    HAS_GPG=false
else
    HAS_GPG=true
fi

# Validate repository
if [[ ! -f "${REPO_ROOT}/setup.py" ]]; then
    error "Not in termbook repository root (setup.py not found)"
fi

VERSION=$(python3 -c "import sys; sys.path.insert(0, '${REPO_ROOT}'); from termbook import __version__; print(__version__)")
info "Building termbook v${VERSION}"

# Run tests unless skipped
if [[ "$SKIP_TESTS" != "true" ]]; then
    step "Running tests"
    cd "$REPO_ROOT"
    if ! venv/bin/python3 -m pytest tests/ -q; then
        error "Tests failed"
    fi
fi

# Clean and prepare build directory
step "Preparing build directory"
if [[ "$REBUILD" == "true" ]]; then
    rm -rf "$BUILD_DIR"
fi
mkdir -p "$BUILD_DIR" "$OSTREE_REPO"

# Build flatpak
step "Building Flatpak"
cd "$REPO_ROOT"

flatpak-builder \
    --arch="$FLATPAK_ARCH" \
    --repo="$OSTREE_REPO" \
    --force-clean \
    "${BUILD_DIR}/build" \
    "$MANIFEST_PATH"

info "Flatpak built successfully"

# Generate .flatpakref file
step "Generating .flatpakref file"
cat > "${TEMP_DIR}/${APP_ID}.flatpakref" << 'EOF'
[Flatpak Ref]
Title=termbook
Url=https://termbook.dev/termbook.flatpakrepo
GPGKey=
IsRuntime=false
EOF

# Generate .flatpakrepo file
step "Generating .flatpakrepo file"
cat > "${TEMP_DIR}/termbook.flatpakrepo" << 'EOF'
[Flatpak Repo]
Title=termbook Repository
Url=https://termbook.dev/repo
Comment=EPUB reader for the terminal
Homepage=https://github.com/macsplit/termbook
Icon=https://termbook.dev/favicon.png
EOF

# Sign repository if GPG is available
if [[ "$HAS_GPG" == "true" ]]; then
    step "Signing ostree repository"

    # Look for a GPG key
    GPG_KEY_ID=$(gpg --list-keys --with-colons | grep "pub:" | head -1 | cut -d: -f5)

    if [[ -z "$GPG_KEY_ID" ]]; then
        warning "No GPG key found; repository will be unsigned"
    else
        info "Using GPG key: $GPG_KEY_ID"
        flatpak build-sign --gpg-sign="$GPG_KEY_ID" --gpg-homedir="$HOME/.gnupg" "$OSTREE_REPO"
        info "Repository signed"
    fi
fi

# Prepare files for deployment
step "Preparing deployment files"
cp "${WEBSITE_SRC}/index.html" "${TEMP_DIR}/"
cp "${WEBSITE_SRC}/style.css" "${TEMP_DIR}/"
cp "${WEBSITE_SRC}/screenshot.png" "${TEMP_DIR}/"

# Deploy to NUC
step "Deploying to NUC"

# Create deploy directory on NUC
ssh "$NUC_HOST" "mkdir -p '${NUC_DEPLOY_DIR}'" || error "Failed to create deploy directory on NUC"

# Copy website files
info "Copying website files..."
scp "${TEMP_DIR}/index.html" "${NUC_HOST}:${NUC_DEPLOY_DIR}/"
scp "${TEMP_DIR}/style.css" "${NUC_HOST}:${NUC_DEPLOY_DIR}/"
scp "${TEMP_DIR}/screenshot.png" "${NUC_HOST}:${NUC_DEPLOY_DIR}/"
scp "${TEMP_DIR}/${APP_ID}.flatpakref" "${NUC_HOST}:${NUC_DEPLOY_DIR}/"
scp "${TEMP_DIR}/termbook.flatpakrepo" "${NUC_HOST}:${NUC_DEPLOY_DIR}/"

# Copy ostree repository
info "Copying ostree repository (this may take a moment)..."
rsync -av --delete "$OSTREE_REPO/" "${NUC_HOST}:${NUC_DEPLOY_DIR}/repo/" || error "Failed to copy ostree repo"

# Generate and deploy nginx configuration
step "Configuring nginx on NUC"

# Reuse the existing termbook.dev port if present; otherwise fall back to the
# first free slot in the usual range.
NEXT_PORT=$(ssh "$NUC_HOST" "
  if [ -f /etc/nginx/sites-available/termbook.dev ]; then
    existing=\$(sed -n 's/^[[:space:]]*listen[[:space:]]\\+\\([0-9][0-9]*\\);/\\1/p' /etc/nginx/sites-available/termbook.dev | head -1)
    if [ -n \"\$existing\" ]; then
      echo \$existing
      exit 0
    fi
  fi
  for port in 33333 33334 33335 33336 33337 33338 33339 33340; do
    if ! grep -q \"listen \$port\" /etc/nginx/sites-available/* 2>/dev/null; then
      echo \$port
      break
    fi
  done
")

if [[ -z "$NEXT_PORT" ]]; then
    error "No available ports found for nginx"
fi

info "Using port: $NEXT_PORT"

# Create nginx config
NGINX_CONFIG=$(cat << 'NGINX_EOF'
server {
    listen PORT;
    server_name termbook.dev;

    root DEPLOY_DIR;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location = /uk.leehanken.termbook.flatpakref {
        default_type application/vnd.flatpak.ref;
    }

    location = /termbook.flatpakrepo {
        default_type application/vnd.flatpak.repo;
    }

    location /repo/ {
        alias DEPLOY_DIR/repo/;
    }
}
NGINX_EOF
)

# Replace placeholders
NGINX_CONFIG="${NGINX_CONFIG//PORT/$NEXT_PORT}"
NGINX_CONFIG="${NGINX_CONFIG//DEPLOY_DIR/$NUC_DEPLOY_DIR}"

# Deploy nginx config
ssh "$NUC_HOST" "cat > /tmp/termbook.dev << 'CONFIG_EOF'
$NGINX_CONFIG
CONFIG_EOF
" || error "Failed to create nginx config on NUC"

ssh "$NUC_HOST" "sudo mv /tmp/termbook.dev /etc/nginx/sites-available/" || error "Failed to move nginx config"

# Enable site
ssh "$NUC_HOST" "sudo ln -sf /etc/nginx/sites-available/termbook.dev /etc/nginx/sites-enabled/ 2>/dev/null; true" || true

# Test nginx config
if ! ssh "$NUC_HOST" "sudo nginx -t"; then
    error "nginx configuration test failed on NUC"
fi

# Reload nginx
ssh "$NUC_HOST" "sudo systemctl reload nginx" || error "Failed to reload nginx"

# Verify deployment
step "Verifying deployment"

# Check website is accessible
if ! curl -s "http://localhost:$NEXT_PORT/" > /dev/null 2>&1; then
    # Try via SSH
    if ! ssh "$NUC_HOST" "curl -s http://127.0.0.1:$NEXT_PORT/ > /dev/null"; then
        warning "Could not verify website is accessible (this may be expected in some environments)"
    fi
fi

info "Deployment complete"
echo ""
echo "=========================================="
echo "Flatpak deployment successful!"
echo "=========================================="
echo ""
echo "Port: $NEXT_PORT"
echo "Website: http://termbook.dev:$NEXT_PORT"
echo "Flatpak Repo: https://termbook.dev/repo"
echo "App ID: $APP_ID"
echo "Version: $VERSION"
echo ""
echo "To use the Flatpak tunnel, configure Cloudflare to:"
echo "  - Route termbook.dev to http://NUC_IP:$NEXT_PORT"
echo "  - Or use: cloudflared tunnel route dns termbook.dev http://localhost:$NEXT_PORT"
echo ""
