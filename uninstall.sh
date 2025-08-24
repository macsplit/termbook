#!/bin/bash

# Termbook Uninstall Script
# This script removes the termbook installation and cleans up

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
INSTALL_DIR="/usr/local/bin"
WRAPPER_SCRIPT="$INSTALL_DIR/termbook"

echo -e "${BLUE}=== Termbook Uninstall Script ===${NC}"
echo

# Remove global wrapper script
if [[ -f "$WRAPPER_SCRIPT" ]]; then
    echo -e "${YELLOW}Removing global termbook command...${NC}"
    if [[ -w "$INSTALL_DIR" ]]; then
        rm -f "$WRAPPER_SCRIPT"
    else
        sudo rm -f "$WRAPPER_SCRIPT"
    fi
    echo -e "${GREEN}✓ Removed $WRAPPER_SCRIPT${NC}"
else
    echo -e "${YELLOW}Global termbook command not found (already removed)${NC}"
fi

# Remove virtual environment
if [[ -d "$VENV_DIR" ]]; then
    echo -e "${YELLOW}Removing virtual environment...${NC}"
    rm -rf "$VENV_DIR"
    echo -e "${GREEN}✓ Removed virtual environment${NC}"
else
    echo -e "${YELLOW}Virtual environment not found (already removed)${NC}"
fi

# Clean build artifacts
echo -e "${YELLOW}Cleaning build artifacts...${NC}"
rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist" "$PROJECT_DIR"/*.egg-info
echo -e "${GREEN}✓ Cleaned build artifacts${NC}"

# Final check
if command -v termbook >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: 'termbook' command is still available. You may need to restart your shell or run 'hash -r'${NC}"
else
    echo -e "${GREEN}✓ termbook command successfully removed${NC}"
fi

echo
echo -e "${GREEN}=== Uninstall Complete ===${NC}"
echo -e "${GREEN}All termbook components have been removed.${NC}"
echo