.PHONY: install install-local uninstall clean test help build

# Default target
help:
	@echo "Termbook - Terminal EPUB Reader"
	@echo ""
	@echo "Available targets:"
	@echo "  install       - Build and install termbook to /usr/local/bin (requires sudo)"
	@echo "  install-local - Build and install termbook to ~/.local/bin (no sudo)"
	@echo "  uninstall     - Remove termbook installation"
	@echo "  clean         - Clean build artifacts"
	@echo "  test          - Test the installation"
	@echo "  build         - Build the package (without installing globally)"
	@echo "  help          - Show this help message"
	@echo ""
	@echo "Requirements: python3, pip3"
	@echo "Note: Global install requires sudo, local install does not"

# Install termbook globally
install:
	@echo "Installing termbook globally..."
	./install.sh

# Install termbook locally
install-local:
	@echo "Installing termbook locally..."
	./install_local.sh

# Uninstall termbook
uninstall:
	@echo "Uninstalling termbook..."
	./uninstall.sh

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info/ __pycache__/
	@echo "Build artifacts cleaned."

# Build without installing globally
build:
	@echo "Building termbook in virtual environment..."
	@if [ ! -d "venv" ]; then \
		echo "Creating virtual environment..."; \
		python3 -m venv venv; \
	fi
	@echo "Activating virtual environment and installing..."
	. venv/bin/activate && \
	pip install --upgrade pip setuptools wheel && \
	pip install -r requirements.txt && \
	pip install -e .
	@echo "Build complete. Use 'source venv/bin/activate' to use termbook."

# Test the installation
test:
	@echo "Testing termbook installation..."
	@if command -v termbook >/dev/null 2>&1; then \
		echo "✓ termbook command is available globally"; \
		timeout 2s termbook --help >/dev/null 2>&1 || true; \
		echo "✓ termbook executable works"; \
	else \
		echo "✗ termbook command not found globally"; \
		echo "  Run 'make install' to install termbook"; \
		exit 1; \
	fi