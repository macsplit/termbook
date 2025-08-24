#!/bin/bash

# Termbook Test Runner
# This script sets up the test environment and runs the test suite

set -e  # Exit on any error

echo "Setting up test environment..."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "Activated virtual environment"
fi

# Install test dependencies
echo "Installing test dependencies..."
pip install -r requirements-test.txt

# Ensure termbook is installed in development mode
echo "Installing termbook in development mode..."
pip install -e .

# Run tests with different configurations
echo ""
echo "Running termbook test suite..."
echo "================================"

# Run all tests
echo "Running all tests..."
pytest tests/ -v

# Run only fast tests (excluding slow ones)
echo ""
echo "Running fast tests only..."
pytest tests/ -v -m "not slow"

# Run with coverage if available
if pip list | grep -q pytest-cov; then
    echo ""
    echo "Running tests with coverage..."
    pytest tests/ --cov=termbook --cov-report=term-missing
fi

echo ""
echo "Test run complete!"
echo ""
echo "Usage examples:"
echo "  ./run_tests.sh                    # Run all tests"
echo "  pytest tests/test_basic_functionality.py -v  # Run specific test file"
echo "  pytest tests/ -k test_help -v    # Run tests matching 'help'"
echo "  pytest tests/ -m 'not slow' -v   # Skip slow tests"