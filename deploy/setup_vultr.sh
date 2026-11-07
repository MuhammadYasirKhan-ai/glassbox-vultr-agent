#!/usr/bin/env bash
# Run as root on a fresh Ubuntu 24.04 Vultr Cloud Compute VM:  bash deploy/setup_vultr.sh
set -euo pipefail
apt-get update && apt-get install -y docker.io python3-venv python3-pip git curl ufw
systemctl enable --now docker

# gVisor (runsc) as an extra isolation layer
if ! command -v runsc >/dev/null; then
  ARCH=$(uname -m)
  URL=https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}
  curl -fsSLo /usr/local/bin/runsc ${URL}/runsc && chmod +x /usr/local/bin/runsc
  curl -fsSLo /usr/local/bin/containerd-shim-runsc-v1 ${URL}/containerd-shim-runsc-v1
  chmod +x /usr/local/bin/containerd-shim-runsc-v1
  runsc install && systemctl restart docker
fi

APP_DIR=/opt/glassbox
[ -f .env ] || { echo "Create .env first: cp .env.example .env && nano .env"; exit 1; }
if [ "$(pwd)" != "$APP_DIR" ]; then mkdir -p "$APP_DIR" && cp -r . "$APP_DIR"; fi
cd "$APP_DIR"
docker build -t glassbox-sandbox:latest sandbox/
python3 -m venv .venv && .venv/bin/pip install -q .
# Enable gVisor only if it actually works with our image
sed -i '/^SANDBOX_RUNTIME=/d' .env
if docker run --rm --runtime runsc --entrypoint python glassbox-sandbox:latest -c "print(1)" >/dev/null 2>&1; then
  echo "SANDBOX_RUNTIME=runsc" >> .env; echo "gVisor: enabled"
else
  echo "SANDBOX_RUNTIME=" >> .env; echo "gVisor: not available, using plain Docker isolation"
fi

cat > /etc/systemd/system/glassbox.service <<UNIT
[Unit]
Description=GlassBox API
After=docker.service
[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn --factory app.main:get_app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now glassbox

# Public entry: Caddy gives automatic HTTPS if you point a domain at the VM; otherwise plain :80
apt-get install -y caddy
cat > /etc/caddy/Caddyfile <<'CADDY'
:80 {
  reverse_proxy 127.0.0.1:8000 {
    flush_interval -1
  }
}
CADDY
systemctl restart caddy
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
echo "Done. Test: curl http://<VM_IP>/healthz"
