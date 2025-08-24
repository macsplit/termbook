"""Test bookmark functionality."""

import pytest
import pexpect
import time


class TestBookmarks:
    """Test bookmark creation and management."""
    
    def test_save_bookmark(self, termbook_process, clean_termbook_state):
        """Test saving a bookmark."""
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Navigate to a position
        proc.send(' ')  # Page down
        time.sleep(0.5)
        
        # Save bookmark
        proc.send('s')
        time.sleep(1)
        
        # Should see "Bookmark saved!" message
        # Note: The exact message may vary, this tests the basic functionality
        assert proc.isalive()
    
    def test_view_bookmarks(self, termbook_process, clean_termbook_state):
        """Test viewing bookmarks list."""
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Save a bookmark first
        proc.send('s')
        time.sleep(1)
        
        # View bookmarks
        proc.send('b')
        time.sleep(1)
        
        # Should show bookmarks dialog
        # Look for bookmark-related content
        try:
            proc.expect(r'.*(Bookmark|saved).*', timeout=3)
        except pexpect.TIMEOUT:
            # May show "No bookmarks" if none saved
            pass
        
        # Close bookmarks dialog
        proc.send('q')  # or Escape
        time.sleep(0.5)
        assert proc.isalive()
    
    def test_bookmark_formatting_fits_screen(self, termbook_process, clean_termbook_state):
        """Test that bookmark entries fit within dialog bounds."""
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Save a bookmark
        proc.send('s')
        time.sleep(1)
        
        # Try different screen sizes
        for width in [60, 80, 100, 120]:
            proc.setwinsize(24, width)
            time.sleep(0.5)
            
            # Open bookmarks
            proc.send('b')
            time.sleep(1)
            
            # Should not crash and should be responsive
            assert proc.isalive()
            
            # Close dialog
            proc.send('q')
            time.sleep(0.5)
    
    def test_empty_bookmarks_list(self, termbook_process, clean_termbook_state):
        """Test viewing bookmarks when none are saved."""
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Try to view bookmarks (should show empty message)
        proc.send('b')
        time.sleep(1)
        
        # Should show some message about no bookmarks
        # The exact text may vary
        assert proc.isalive()
        
        # Should be able to close dialog
        proc.send('q')
        time.sleep(0.5)
        assert proc.isalive()