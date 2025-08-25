# Changelog

All notable changes to termbook will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2025-08-25

### Changed
- **Improved image rendering quality**: Upgraded from 4x4 to 8x8 pixel oversampling for much smoother images with better color accuracy
- **Simplified multiple image handling**: Replaced complex thumbnail grid with clean text list showing filename, alt text, and captions
- **Enhanced color preservation**: Made palette matching more conservative to prevent wrong color reuse
- **Better light theme visibility**: Fixed hint text colors for better readability on light theme

### Fixed
- Black pixels no longer dominate entire character blocks due to improved oversampling
- Pale colors (greens, reds) now preserved instead of being mapped to grayscale
- Loading animations and hints now properly visible on light theme

### Technical
- Increased `samples_per_dim` from 4 to 8 for smoother image rendering
- Reduced `max_acceptable_distance` from 2500 to 1500 for more accurate color matching
- Selective saturation boost only for near-gray colors (< 0.3 saturation)
- Removed complex thumbnail grid pagination system

## [1.1.0] - Previous Release
- Initial enhanced image rendering with color support
- Added image thumbnails and selection interface
- Support for inline image display in terminal
- Multiple theme support (default, dark, light)