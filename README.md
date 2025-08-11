# termbook

A terminal-based EPUB reader optimized for programming books, with inline 256-color image rendering and syntax-highlighted code blocks.

## About

termbook is a derivative of [epr (epub-reader)](https://github.com/wustho/epr) by Benawi Adha, enhanced with features specifically designed for reading technical and programming books in the terminal.

### Key Features

- **256-color inline image rendering** - View diagrams, charts, and illustrations directly in your terminal
- **Syntax-highlighted code blocks** - Automatic language detection and highlighting for code snippets
- **Full EPUB support** - Read any standard EPUB file
- **Curses-based interface** - Clean, distraction-free reading experience
- **Vim-like keybindings** - Familiar navigation for power users
- **Reading history** - Track your reading progress across sessions
- **Bookmarks** - Mark and jump to specific positions
- **Search functionality** - Find text within your books
- **Table of Contents navigation** - Quick chapter jumping
- **Adjustable text width** - Customize reading column width

## Attribution

This project is based on **epr (epub-reader)** by Benawi Adha:
- Original repository: https://github.com/wustho/epr
- License: MIT
- Author: Benawi Adha (benawiadha@gmail.com)

The core EPUB reading functionality, curses interface, and navigation system are from the original epr project. Enhancements for image rendering and code highlighting were added by Lee Hanken.

## Requirements

### System Dependencies
- Python 3.8 or higher
- Terminal with 256-color support
- Linux (primary target, may work on other Unix-like systems)

### Python Dependencies
- `Pillow` (PIL) - For image processing and rendering
- `pygments` - For syntax highlighting of code blocks

## Installation

### Method 1: Direct Installation
```bash
# Clone the repository
git clone https://github.com/leehanken/termbook.git
cd termbook

# Install Python dependencies
pip install Pillow pygments

# Make the script executable
chmod +x termbook.py

# Optionally, create a symlink in your PATH
sudo ln -s $(pwd)/termbook.py /usr/local/bin/termbook
```

### Method 2: Using pip with local directory
```bash
# Clone the repository
git clone https://github.com/leehanken/termbook.git
cd termbook

# Install with pip (includes dependencies)
pip install -e .
```

### Method 3: Virtual Environment (Recommended)
```bash
# Clone the repository
git clone https://github.com/leehanken/termbook.git
cd termbook

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install Pillow pygments

# Run directly from virtual environment
./venv/bin/python termbook.py [EPUBFILE]
```

### Method 4: System Package Manager
For Arch Linux users (AUR):
```bash
# If/when available in AUR
yay -S termbook
```

For Debian/Ubuntu (if packaged):
```bash
# Future possibility
sudo apt install termbook
```

## Usage

```bash
# Read an EPUB file
termbook book.epub

# Read last opened book
termbook

# Show reading history
termbook -r

# Search history for a book
termbook "python programming"

# Dump EPUB contents (debug)
termbook -d book.epub

# Show help
termbook --help
```

## Key Bindings

| Key | Action |
|-----|--------|
| `?` | Show help |
| `q` | Quit |
| `j`, `↓` | Scroll down |
| `k`, `↑` | Scroll up |
| `Ctrl-d` | Half page down |
| `Ctrl-u` | Half page up |
| `Space`, `PgDn`, `→` | Next page |
| `PgUp`, `←` | Previous page |
| `n` | Next chapter |
| `p` | Previous chapter |
| `g`, `Home` | Beginning of chapter |
| `G`, `End` | End of chapter |
| `o` | Open image in viewer |
| `/` | Search |
| `n` | Next search result |
| `N` | Previous search result |
| `Tab`, `t` | Table of contents |
| `m` | Show metadata |
| `b[n]` | Set bookmark n |
| `` `[n]`` | Jump to bookmark n |
| `=` | Toggle/set width |
| `-` | Decrease width |
| `+` | Increase width |
| `c` | Cycle color schemes |

## Color Schemes

termbook includes three color schemes:
- **0**: Default theme
- **1**: Dark theme
- **2**: Light theme

Press `c` while reading to cycle through themes.

## Terminal Configuration

For best results, ensure your terminal:
- Supports 256 colors (most modern terminals do)
- Has a monospace font with good Unicode support
- Has sufficient width (80+ columns recommended)

### Testing 256-color support:
```bash
# Check if your terminal supports 256 colors
tput colors
# Should output: 256

# Or check TERM variable
echo $TERM
# Should contain "256color"
```

## Troubleshooting

### Images not displaying correctly
- Ensure your terminal supports 256 colors
- Try adjusting terminal font size
- Check that Pillow is properly installed: `python -c "from PIL import Image; print('OK')"`

### Code highlighting not working
- Install pygments: `pip install pygments`
- Check pygments is working: `python -c "import pygments; print('OK')"`

### Unicode/special characters not displaying
- Ensure your terminal uses UTF-8 encoding
- Try a different terminal font with better Unicode support

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project maintains the MIT License from the original epr project.

MIT License - see LICENSE file for details.

## Author

**termbook enhancements**: Lee Hanken

**Original epr**: Benawi Adha (benawiadha@gmail.com)

## Acknowledgments

Special thanks to Benawi Adha for creating the excellent epr epub reader that serves as the foundation for this project.