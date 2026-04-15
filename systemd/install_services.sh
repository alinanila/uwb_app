#!/usr/bin/env bash
set -euo pipefail

# Usage:
#./systemd/install_services.sh agent
#./systemd/install_services.sh hub
#./systemd/install_services.sh localizer
#./systemd/install_services.sh server
#./systemd/install_services.sh all

SERVICE_DIR="/etc/systemd/system"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

install_agent() {
  echo "Installing uwb-agent.service..."
  sudo cp "$REPO_DIR/systemd/uwb-agent.service" "$SERVICE_DIR/"
  sudo systemctl daemon-reload
  sudo systemctl enable uwb-agent
  sudo systemctl restart uwb-agent
  sudo systemctl status uwb-agent --no-pager || true
}

install_hub() {
  echo "Installing uwb-hub.service..."
  sudo cp "$REPO_DIR/systemd/uwb-hub.service" "$SERVICE_DIR/"
  sudo systemctl daemon-reload
  sudo systemctl enable uwb-hub
  sudo systemctl restart uwb-hub
  sudo systemctl status uwb-hub --no-pager || true
}

install_localizer() {
  echo "Installing uwb-localize.service..."
  sudo cp "$REPO_DIR/systemd/uwb-localize.service" "$SERVICE_DIR/"
  sudo systemctl daemon-reload
  sudo systemctl enable uwb-localize
  sudo systemctl restart uwb-localize
  sudo systemctl status uwb-localize --no-pager || true
}

install_server() {
  echo "Installing uwb-server.service..."
  sudo cp "$REPO_DIR/systemd/uwb-server.service" "$SERVICE_DIR/"
  sudo systemctl daemon-reload
  sudo systemctl enable uwb-server
  sudo systemctl restart uwb-server
  sudo systemctl status uwb-server --no-pager || true
}

case "${1:-}" in
  agent)
    install_agent
    ;;
  hub)
    install_hub
    ;;
  localizer)
    install_localizer
    ;;
  server)
    install_server
    ;;
  all)
    install_agent
    install_hub
    install_localizer
    install_server
    ;;
  *)
    echo "Usage: $0 {agent|hub|localizer|server|all}"
    exit 1
    ;;
esac