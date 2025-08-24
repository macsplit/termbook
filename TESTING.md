# Termbook Testing Framework

This document describes the automated testing framework for termbook, following best practices for terminal application testing.

## Overview

The testing framework uses:
- **pytest** for test organization and execution
- **pexpect** for terminal interaction simulation
- **Proper fixtures** for test isolation and setup
- **Standard practices** for curses/terminal application testing

## Test Structure

```
tests/
├── __init__.py                    # Test package
├── conftest.py                   # Shared fixtures and configuration
├── test_basic_functionality.py  # Basic app functionality tests
├── test_resize_and_modals.py    # Resize and modal behavior tests
├── test_bookmarks.py            # Bookmark functionality tests
└── test_manual.py               # Manual debugging tests
```

## Running Tests

### Using the test runner (recommended):
```bash
./run_tests.sh
```

### Using pytest directly:
```bash
# Activate virtual environment
source venv/bin/activate

# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_basic_functionality.py -v

# Run tests matching a pattern
pytest tests/ -k test_help -v

# Run only fast tests (skip slow ones)
pytest tests/ -m "not slow" -v
```

## Test Categories

### Basic Functionality Tests (`test_basic_functionality.py`)
- Application startup and initialization
- Initial help message behavior (appearance, disappearance, timing)
- Basic navigation (arrow keys, page up/down, etc.)
- Help dialog functionality
- Quit functionality

### Resize and Modal Tests (`test_resize_and_modals.py`)
- Terminal resize handling
- Modal dialog behavior during resize
- Help message persistence across resizes
- Multiple resize stability
- Reading position preservation

### Bookmark Tests (`test_bookmarks.py`)
- Bookmark creation
- Bookmark viewing and navigation
- Bookmark formatting at different screen sizes
- Empty bookmark list handling

## Key Features

### Test Isolation
- Each test gets a clean termbook state
- Temporary EPUB files are created and cleaned up automatically
- Configuration files are managed per test

### Realistic Terminal Simulation
- Uses pexpect for true terminal interaction
- Sets standard terminal dimensions (24x80)
- Handles terminal control sequences properly
- Tests actual user interaction patterns

### Robust Error Handling
- Proper timeout handling
- Process cleanup on test failure
- Detailed error reporting with terminal output

## Test Fixtures

### `test_epub`
Creates a minimal but valid EPUB file with:
- Proper EPUB structure and metadata
- Valid TOC (NCX) file
- Sample content for testing navigation
- Automatic cleanup after test session

### `termbook_process`
Provides a running termbook process:
- Pre-loaded with test EPUB
- Standard terminal size (24x80)
- Proper timeout configuration
- Automatic process termination after test

### `clean_termbook_state`
Ensures clean application state:
- Cleans configuration directories before/after tests
- Removes bookmarks and reading history
- Provides isolated test environment

## Writing New Tests

### Basic Test Structure
```python
def test_feature_name(self, termbook_process):
    \"\"\"Test description.\"\"\"
    proc = termbook_process
    
    # Clear initial help message
    proc.send('j')
    time.sleep(0.5)
    
    # Test your feature
    proc.send('your_key')
    proc.expect(r'expected_output', timeout=5)
    
    # Assert conditions
    assert proc.isalive()
```

### Best Practices
1. **Always clear the initial help message** first in tests
2. **Use appropriate timeouts** for different operations
3. **Test both positive and negative cases**
4. **Include cleanup and error handling**
5. **Use descriptive test names** that explain what's being tested
6. **Add proper docstrings** explaining the test purpose

## Known Issues

### EPUB Parser Requirements
The test EPUB files must include:
- Valid container.xml
- Proper OPF package file
- NCX table of contents file (for EPUB 2.0)
- Valid XHTML content files

Missing any of these components will cause the EPUB parser to fail during initialization.

### Terminal Environment
Tests require:
- Linux/Unix environment with proper terminal support
- pexpect compatibility (Unix pseudo-terminals)
- Sufficient terminal capabilities for curses

## Future Enhancements

Potential improvements to the testing framework:
- **Screen content validation** using pyte for terminal parsing
- **Performance benchmarks** for large EPUB files  
- **Cross-platform testing** support
- **Visual regression testing** for terminal output
- **Integration with CI/CD** pipelines

## Debugging Tests

### Manual Testing
Use `test_manual.py` for debugging:
```bash
python tests/test_manual.py
```

### Verbose Output
Run tests with maximum verbosity:
```bash
pytest tests/ -vvv --tb=long
```

### Individual Test Debugging
```bash
pytest tests/test_file.py::TestClass::test_method -vvv
```

This framework provides comprehensive testing coverage while following established best practices for terminal application testing.