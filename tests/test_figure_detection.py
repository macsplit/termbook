#!/usr/bin/env python3
"""
Tests for figure detection and image labeling functionality
"""
import pytest
import tempfile
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import termbook

class TestFigureDetection:
    """Test figure number detection and image labeling"""
    
    def test_extract_figure_number_basic(self):
        """Test basic figure number extraction"""
        # Test various figure formats
        test_cases = [
            ("Figure 1.6", "1.6"),
            ("Fig 2.3", "2.3"), 
            ("Table 4.1", "4.1"),
            ("Listing 3", "3"),
            ("figure 1.6", "1.6"),  # case insensitive
            ("No figure here", None),
            ("", None),
        ]
        
        for text, expected in test_cases:
            result = termbook.extract_figure_number(text)
            assert result == expected, f"Failed for '{text}': expected {expected}, got {result}"
    
    def test_extract_figure_number_with_html(self):
        """Test figure number extraction from HTML content"""
        html_content = '<h5 class="figure-container-h5"><span class="num-string">Figure 1.6</span> The overall layers of a React application</h5>'
        
        result = termbook.extract_figure_number(html_content)
        assert result == "1.6", f"Expected '1.6', got '{result}'"
    
    def test_get_enhanced_image_label_with_figure_number(self):
        """Test enhanced image labeling when figure number is found"""
        # Mock data simulating EPUB processing
        img_path = "../Images/CH01_F06_Barklund.png"
        img_idx = 0
        img_alts = ["figure"]  # Generic alt text
        
        # Mock src_lines that include figure caption after image
        src_lines = [
            "<div class=\"browsable-container figure-container\" id=\"p97\">",
            "<img alt=\"figure\" src=\"../Images/CH01_F06_Barklund.png\"/>",
            "<h5 class=\"figure-container-h5\"><span class=\"num-string\">Figure 1.6</span> The overall layers of a React application, with several examples of each.</h5>",
            "</div>",
            "<p>Note that each layer in the stack...</p>"
        ]
        img_line_num = 1  # Image is at line 1, caption at line 2
        
        result = termbook.get_enhanced_image_label(img_path, img_idx, img_alts, src_lines, img_line_num)
        
        # Should find figure number and create enhanced label
        assert "Figure 1.6" in result, f"Expected figure number in result, got: '{result}'"
        assert result != "figure", "Should not fallback to generic alt text when figure number available"
    
    def test_get_enhanced_image_label_fallback_to_alt(self):
        """Test fallback to alt text when no figure number found"""
        img_path = "../Images/some_image.png"
        img_idx = 0
        img_alts = ["A diagram showing the process"]
        src_lines = [
            "<p>Some text</p>",
            "<img alt=\"A diagram showing the process\" src=\"../Images/some_image.png\"/>",
            "<p>More text without figure numbers</p>"
        ]
        img_line_num = 1
        
        result = termbook.get_enhanced_image_label(img_path, img_idx, img_alts, src_lines, img_line_num)
        
        # Should use the descriptive alt text
        assert "diagram" in result.lower(), f"Expected alt text content in result, got: '{result}'"
    
    def test_get_enhanced_image_label_filename_fallback(self):
        """Test fallback to filename when no other info available"""
        img_path = "../Images/CH01_F06_Barklund.png"
        img_idx = 0
        img_alts = [""]  # Empty alt text
        src_lines = ["<p>No figure info here</p>"]
        img_line_num = 0
        
        result = termbook.get_enhanced_image_label(img_path, img_idx, img_alts, src_lines, img_line_num)
        
        # Should fallback to filename
        assert "CH01_F06_Barklund.png" in result, f"Expected filename in result, got: '{result}'"

if __name__ == '__main__':
    pytest.main([__file__, '-v'])