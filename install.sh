#!/bin/bash

# Termbook Build and Installation Script
# This script sets up a virtual environment, builds the package, and installs the executable to /usr/local/bin

set -e  # Exit on any error

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

echo -e "${BLUE}=== Termbook Installation Script ===${NC}"
echo "Project directory: $PROJECT_DIR"
echo "Virtual environment: $VENV_DIR"
echo "Install location: $INSTALL_DIR"
echo

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies
echo -e "${YELLOW}Checking dependencies...${NC}"
if ! command_exists python3; then
    echo -e "${RED}Error: python3 is required but not installed.${NC}"
    exit 1
fi

if ! command_exists pip3; then
    echo -e "${RED}Error: pip3 is required but not installed.${NC}"
    exit 1
fi

# Check if we can write to /usr/local/bin (need sudo)
if [[ ! -w "$INSTALL_DIR" ]]; then
    echo -e "${YELLOW}Note: Will need sudo privileges to install to $INSTALL_DIR${NC}"
    if ! sudo -v; then
        echo -e "${RED}Error: sudo access required for installation${NC}"
        exit 1
    fi
fi

# Clean previous build artifacts
echo -e "${YELLOW}Cleaning previous build artifacts...${NC}"
rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist" "$PROJECT_DIR"/*.egg-info

# Set up virtual environment
if [[ -d "$VENV_DIR" ]]; then
    echo -e "${YELLOW}Removing existing virtual environment...${NC}"
    rm -rf "$VENV_DIR"
fi

echo -e "${YELLOW}Creating virtual environment...${NC}"
python3 -m venv "$VENV_DIR"

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source "$VENV_DIR/bin/activate"

# Upgrade pip and install build tools
echo -e "${YELLOW}Upgrading pip and installing build tools...${NC}"
pip install --upgrade pip setuptools wheel

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -r "$PROJECT_DIR/requirements.txt"

# Build and install the package in the virtual environment
echo -e "${YELLOW}Building and installing termbook...${NC}"
cd "$PROJECT_DIR"
pip install -e .

# Verify the installation in venv works
echo -e "${YELLOW}Verifying installation...${NC}"
if [[ ! -f "$VENV_DIR/bin/termbook" ]]; then
    echo -e "${RED}Error: termbook executable not found in virtual environment${NC}"
    exit 1
fi

# Test the executable
echo -e "${YELLOW}Testing termbook executable...${NC}"
if ! "$VENV_DIR/bin/termbook" --version >/dev/null 2>&1; then
    # If --version doesn't work, just try running it briefly
    timeout 2s "$VENV_DIR/bin/termbook" --help >/dev/null 2>&1 || true
fi

# Create the wrapper script for /usr/local/bin
echo -e "${YELLOW}Creating wrapper script...${NC}"
cat > /tmp/termbook_wrapper << EOF
#!/bin/bash
# Termbook wrapper script - auto-generated
TERMBOOK_DIR="$PROJECT_DIR"
source "\$TERMBOOK_DIR/venv/bin/activate"
exec "\$TERMBOOK_DIR/venv/bin/termbook" "\$@"
EOF

# Install the wrapper script
echo -e "${YELLOW}Installing termbook to $INSTALL_DIR...${NC}"
sudo cp /tmp/termbook_wrapper "$WRAPPER_SCRIPT"
sudo chmod +x "$WRAPPER_SCRIPT"
rm /tmp/termbook_wrapper

# Verify the global installation
echo -e "${YELLOW}Verifying global installation...${NC}"
if [[ ! -f "$WRAPPER_SCRIPT" ]]; then
    echo -e "${RED}Error: Failed to install wrapper script${NC}"
    exit 1
fi

if [[ ! -x "$WRAPPER_SCRIPT" ]]; then
    echo -e "${RED}Error: Wrapper script is not executable${NC}"
    exit 1
fi

# Test global command
echo -e "${YELLOW}Testing global termbook command...${NC}"
if command_exists termbook; then
    echo -e "${GREEN}✓ termbook command is available globally${NC}"
    # Brief test - timeout after 2 seconds in case it hangs waiting for input
    timeout 2s termbook --help >/dev/null 2>&1 || true
else
    echo -e "${YELLOW}Note: You may need to restart your shell or run 'hash -r' for the command to be available${NC}"
fi

# Installation summary
echo
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo -e "${GREEN}✓ Virtual environment created at: $VENV_DIR${NC}"
echo -e "${GREEN}✓ Dependencies installed${NC}"
echo -e "${GREEN}✓ Termbook built and installed in virtual environment${NC}"
echo -e "${GREEN}✓ Wrapper script installed at: $WRAPPER_SCRIPT${NC}"
echo
echo -e "${BLUE}Usage:${NC}"
echo "  termbook <epub-file>    # Read an EPUB file"
echo "  termbook --help         # Show help"
echo
echo -e "${BLUE}Development:${NC}"
echo "  To activate the virtual environment for development:"
echo "  source $VENV_DIR/bin/activate"
echo
echo -e "${YELLOW}Note: The global 'termbook' command uses the virtual environment automatically.${NC}"