#!/bin/bash

# Simple Test Runner for Termbook Application Features
# Tests termbook functionality without worrying about EPUB creation

set -e

echo "Termbook Application Feature Tests"
echo "=================================="
echo ""

# Check for test EPUB file
EPUB_FILE=""
for location in ~/test.epub /tmp/test.epub ./test.epub; do
    if [ -f "$location" ]; then
        EPUB_FILE="$location"
        echo "Found test EPUB: $EPUB_FILE"
        break
    fi
done

if [ -z "$EPUB_FILE" ]; then
    echo "No test EPUB file found."
    echo "Please place a test EPUB file in one of these locations:"
    echo "  ~/test.epub"
    echo "  /tmp/test.epub" 
    echo "  ./test.epub"
    echo ""
    echo "You can download any EPUB file for testing, for example:"
    echo "  curl -o ~/test.epub 'https://www.gutenberg.org/ebooks/74.epub.noimages'"
    echo ""
    exit 1
fi

echo "Setting up environment..."

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "Activated virtual environment"
fi

# Install minimal test dependencies (just what we need)
echo "Installing test dependencies..."
pip install pytest pexpect

echo ""
echo "Running application feature tests..."
echo "===================================="

# Run the application feature tests
pytest tests/test_application_features.py -v --tb=short

echo ""
echo "Test Summary:"
echo "============="
echo "✓ Tests focus on termbook's features, not dependencies"
echo "✓ Uses real EPUB file: $EPUB_FILE"
echo "✓ Tests actual user interactions and workflows"
echo ""
echo "To add more tests, edit tests/test_application_features.py"
echo "To run specific tests: pytest tests/test_application_features.py::TestClass::test_method -v"