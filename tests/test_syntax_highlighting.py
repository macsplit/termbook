"""Test syntax highlighting with theme support."""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termbook import HTMLtoLines


class TestSyntaxHighlighting:
    """Test syntax highlighting functionality with theme support."""
    
    def setup_method(self):
        """Setup test instance."""
        self.parser = HTMLtoLines()
    
    def test_token_colors_are_dual_format(self):
        """Test that token colors return dual format (dark_theme, light_theme)."""
        test_tokens = [
            'Keyword',
            'Name.Function',
            'Literal.String',
            'Comment',
            'Literal.Number',
            'Name.Class',
            'Operator',
        ]
        
        for token in test_tokens:
            color = self.parser.get_token_color(token)
            assert isinstance(color, tuple), f"Token {token} should return a tuple"
            assert len(color) == 2, f"Token {token} should return tuple of length 2"
            
            dark_color, light_color = color
            assert isinstance(dark_color, tuple) and len(dark_color) == 3, \
                f"Dark color for {token} should be RGB tuple"
            assert isinstance(light_color, tuple) and len(light_color) == 3, \
                f"Light color for {token} should be RGB tuple"
    
    def test_light_theme_uses_dark_colors(self):
        """Test that light theme colors are darker than dark theme colors."""
        # Keywords should be darker blue in light theme
        keyword_color = self.parser.get_token_color('Keyword')
        dark_blue, light_blue = keyword_color
        
        # Light theme blue should be darker (lower RGB values)
        assert light_blue[0] <= dark_blue[0], "Light theme blue R should be darker"
        assert light_blue[1] <= dark_blue[1], "Light theme blue G should be darker"
        assert light_blue[2] <= dark_blue[2], "Light theme blue B should be darker"
        
        # Strings should be darker green in light theme
        string_color = self.parser.get_token_color('Literal.String')
        dark_green, light_green = string_color
        
        assert light_green[1] < dark_green[1], "Light theme green should be darker"
    
    def test_syntax_highlighting_returns_dual_colors(self):
        """Test that syntax highlighting returns dual color format."""
        code_sample = """def hello():
    return "world"
"""
        
        highlighted = self.parser.apply_syntax_highlighting(code_sample, "python")
        
        assert len(highlighted) > 0, "Should return highlighted lines"
        
        # Check that colors are in dual format
        for line_text, line_colors in highlighted:
            if line_colors:  # If there are colors
                for color in line_colors:
                    if color:  # If color is not None
                        assert isinstance(color, tuple), "Color should be a tuple"
                        assert len(color) == 2, "Color should be dual format"
                        dark, light = color
                        assert isinstance(dark, tuple) and len(dark) == 3, \
                            "Dark color should be RGB"
                        assert isinstance(light, tuple) and len(light) == 3, \
                            "Light color should be RGB"
    
    def test_specific_color_values(self):
        """Test specific color values for light and dark themes."""
        # Test keyword colors
        keyword_color = self.parser.get_token_color('Keyword')
        assert keyword_color == ((0, 150, 255), (0, 50, 200)), \
            "Keyword colors should match expected values"
        
        # Test string colors  
        string_color = self.parser.get_token_color('Literal.String')
        assert string_color == ((0, 255, 0), (0, 140, 0)), \
            "String colors should match expected values"
        
        # Test comment colors
        comment_color = self.parser.get_token_color('Comment')
        assert comment_color == ((128, 128, 128), (100, 100, 100)), \
            "Comment colors should match expected values"
    
    def test_unknown_token_returns_default(self):
        """Test that unknown tokens return default dual colors."""
        unknown_color = self.parser.get_token_color('Unknown.Token.Type')
        assert unknown_color == ((255, 255, 255), (50, 50, 50)), \
            "Unknown tokens should return default dual colors"
    
    def test_fallback_highlighting(self):
        """Test fallback when lexer detection fails."""
        # Use a made-up language that won't have a lexer
        highlighted = self.parser.apply_syntax_highlighting(
            "some random text", 
            "nonexistent_language"
        )
        
        assert len(highlighted) > 0, "Should return something even without lexer"
        
        # Check fallback colors are dual format
        for line_text, line_colors in highlighted:
            if line_colors:
                for color in line_colors:
                    if color:
                        assert isinstance(color, tuple) and len(color) == 2, \
                            "Fallback should use dual color format"
                        # Check that it's a valid dual color format
                        dark, light = color
                        assert isinstance(dark, tuple) and len(dark) == 3, \
                            "Dark color should be RGB"
                        assert isinstance(light, tuple) and len(light) == 3, \
                            "Light color should be RGB"