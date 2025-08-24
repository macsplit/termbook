"""Basic functionality tests for termbook."""

import pytest
import pexpect
import time


class TestBasicFunctionality:
    """Test basic termbook functionality."""
    
    def test_termbook_starts_successfully(self, termbook_process):
        """Test that termbook starts and displays content."""
        proc = termbook_process
        
        # Should show help message initially
        proc.expect(r'.*for help.*', timeout=5)
        assert proc.isalive()
    
    def test_help_message_appears_and_disappears(self, termbook_process):
        """Test that the help message appears initially and disappears on keypress."""
        proc = termbook_process
        
        # Should show help message
        proc.expect(r'.*for help.*', timeout=5)
        
        # Press a key (down arrow)
        proc.send('\x1b[B')  # Down arrow
        time.sleep(0.5)
        
        # Help message should be gone - check screen doesn't contain "for help"
        proc.before  # Clear buffer
        proc.send(' ')  # Send space to trigger screen update
        time.sleep(0.2)
        
        # The help message should not reappear
        output = proc.before.decode() if proc.before else ""
        assert "for help" not in output.lower()
    
    def test_help_message_auto_disappears(self, termbook_process):
        """Test that help message disappears after 5 seconds."""
        proc = termbook_process
        
        # Should show help message initially
        proc.expect(r'.*for help.*', timeout=5)
        
        # Wait 6 seconds for auto-disappear
        time.sleep(6)
        
        # Send a space to get current screen state
        proc.send(' ')
        time.sleep(0.2)
        
        # Help message should be gone
        output = proc.before.decode() if proc.before else ""
        assert "for help" not in output.lower()
    
    def test_navigation_keys(self, termbook_process):
        """Test basic navigation keys work."""
        proc = termbook_process
        
        # Clear help message first
        proc.send('j')  # Down
        time.sleep(0.5)
        
        # Test down navigation
        proc.send('j')  # Down
        time.sleep(0.2)
        assert proc.isalive()
        
        # Test up navigation  
        proc.send('k')  # Up
        time.sleep(0.2)
        assert proc.isalive()
        
        # Test page down
        proc.send(' ')  # Space for page down
        time.sleep(0.2)
        assert proc.isalive()
    
    def test_quit_functionality(self, termbook_process):
        """Test that 'q' quits the application."""
        proc = termbook_process
        
        # Wait for app to be ready
        time.sleep(1)
        
        # Send quit command
        proc.send('q')
        
        # Should exit
        proc.expect(pexpect.EOF, timeout=5)
        assert not proc.isalive()
    
    def test_help_dialog(self, termbook_process):
        """Test that '?' opens help dialog."""
        proc = termbook_process
        
        # Clear initial help message
        proc.send('j')
        time.sleep(0.5)
        
        # Open help dialog
        proc.send('?')
        time.sleep(1)
        
        # Should show help dialog with key bindings
        proc.expect(r'.*Key Bindings.*', timeout=3)
        
        # Close help dialog
        proc.send('q')  # or Escape
        time.sleep(0.5)
        assert proc.isalive()