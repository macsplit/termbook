#!/bin/bash
# Termbook wrapper script - finds project directory dynamically
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERMBOOK_DIR="$SCRIPT_DIR"
source "$TERMBOOK_DIR/venv/bin/activate"
exec "$TERMBOOK_DIR/venv/bin/termbook" "$@"
