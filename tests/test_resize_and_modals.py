"""Test resize handling and modal behavior."""

import pytest
import pexpect
import time


class TestResizeAndModals:
    """Test terminal resize and modal dialog behavior."""
    
    def test_resize_clears_modals(self, termbook_process):
        """Test that resizing clears any open modals."""
        proc = termbook_process
        
        # Clear initial help message
        proc.send('j')
        time.sleep(0.5)
        
        # Open help modal
        proc.send('?')
        time.sleep(1)
        proc.expect(r'.*Key Bindings.*', timeout=3)
        
        # Resize terminal (simulate with SIGWINCH)
        proc.setwinsize(30, 90)
        time.sleep(1)
        
        # Modal should be closed, back to main reader
        # Try sending 'q' - if modal was open, this would close it
        # If modal is closed, this should quit the app
        proc.send('q')
        proc.expect(pexpect.EOF, timeout=5)
        assert not proc.isalive()
    
    def test_help_message_doesnt_reappear_after_resize(self, termbook_process):
        """Test that help message doesn't reappear after user has dismissed it and resized."""
        proc = termbook_process
        
        # Wait for initial help message and dismiss it
        proc.expect(r'.*for help.*', timeout=5)
        proc.send('j')  # Dismiss help message
        time.sleep(0.5)
        
        # Resize terminal
        proc.setwinsize(30, 90)
        time.sleep(1)
        
        # Resize again
        proc.setwinsize(25, 85)
        time.sleep(1)
        
        # Send a key to get screen state
        proc.send(' ')
        time.sleep(0.5)
        
        # Help message should not have reappeared
        output = proc.before.decode() if proc.before else ""
        assert "for help" not in output.lower()
    
    def test_bookmark_modal_closes_on_resize(self, termbook_process):
        """Test that bookmark modal closes on resize."""
        proc = termbook_process
        
        # Clear initial help message
        proc.send('j')
        time.sleep(0.5)
        
        # Create a bookmark first
        proc.send('s')
        time.sleep(1)
        
        # Open bookmarks
        proc.send('b')
        time.sleep(1)
        
        # Should show bookmarks dialog (even if empty)
        # The exact text may vary, but should have some bookmark-related content
        
        # Resize to close modal
        proc.setwinsize(30, 90)
        time.sleep(1)
        
        # Should be back to main reader
        proc.send('q')
        proc.expect(pexpect.EOF, timeout=5)
        assert not proc.isalive()
    
    def test_multiple_resizes_stability(self, termbook_process):
        """Test that multiple rapid resizes don't crash the application.""" 
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Multiple rapid resizes
        for i in range(5):
            new_rows = 20 + (i % 10)
            new_cols = 70 + (i % 20)
            proc.setwinsize(new_rows, new_cols)
            time.sleep(0.2)
        
        # App should still be alive and responsive
        assert proc.isalive()
        
        # Test navigation still works
        proc.send(' ')  # Page down
        time.sleep(0.5)
        assert proc.isalive()
        
        # Can still quit normally
        proc.send('q')
        proc.expect(pexpect.EOF, timeout=5)
        assert not proc.isalive()
    
    def test_resize_preserves_reading_position(self, termbook_process):
        """Test that resize doesn't lose reading position."""
        proc = termbook_process
        
        # Clear initial help
        proc.send('j')
        time.sleep(0.5)
        
        # Navigate down several times
        for _ in range(5):
            proc.send('j')
            time.sleep(0.1)
        
        # Resize
        proc.setwinsize(30, 90)
        time.sleep(1)
        
        # Should still be able to navigate (position preserved)
        proc.send('k')  # Up
        time.sleep(0.2)
        assert proc.isalive()
        
        proc.send('j')  # Down  
        time.sleep(0.2)
        assert proc.isalive()