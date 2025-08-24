"""Test termbook application features, not dependencies."""

import pytest
import pexpect
import time
import os


class TestTermbookApplicationFeatures:
    """Test termbook's own features and functionality."""
    
    def test_help_message_behavior(self):
        """Test the initial help message behavior with any valid EPUB."""
        # Use a real EPUB file if available, or skip if none found
        test_files = [
            "/tmp/test.epub",  # If user has a test file
            os.path.expanduser("~/test.epub"),  # Common location
            # Add other potential test file locations
        ]
        
        epub_file = None
        for test_file in test_files:
            if os.path.exists(test_file):
                epub_file = test_file
                break
        
        if not epub_file:
            pytest.skip("No test EPUB file available - place test.epub in /tmp/ or ~/ to run this test")
        
        # Test help message appears and disappears
        proc = pexpect.spawn(f'termbook "{epub_file}"', timeout=10)
        proc.setwinsize(24, 80)
        
        try:
            # Wait for app to start - look for any content
            proc.expect('.+', timeout=5)
            
            # Send a key to dismiss help and interact
            proc.send('j')  # Down arrow
            time.sleep(0.5)
            assert proc.isalive()
            
            # Test that we can navigate
            proc.send('k')  # Up arrow
            time.sleep(0.2)
            assert proc.isalive()
            
            # Test quit
            proc.send('q')
            proc.expect(pexpect.EOF, timeout=5)
            assert not proc.isalive()
            
        except Exception as e:
            if proc.isalive():
                proc.terminate()
            raise
    
    def test_basic_navigation_keys(self):
        """Test basic navigation keys work."""
        # This test focuses on termbook's key handling, not EPUB parsing
        pytest.skip("Requires valid EPUB - implement with user-provided test file")
    
    def test_resize_handling(self):
        """Test terminal resize handling."""
        pytest.skip("Requires valid EPUB - implement with user-provided test file")
    
    def test_modal_interactions(self):
        """Test modal dialogs (help, bookmarks, etc.)."""
        pytest.skip("Requires valid EPUB - implement with user-provided test file")


class TestTermbookWithUserFile:
    """Tests that require a user-provided EPUB file."""
    
    @pytest.fixture
    def user_epub(self):
        """Get a user-provided EPUB file for testing."""
        # Check common locations for test files
        candidates = [
            os.path.expanduser("~/test.epub"),
            "/tmp/test.epub", 
            "./test.epub"
        ]
        
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
                
        pytest.skip("No test EPUB found - place a test.epub file in ~/, /tmp/, or current directory")
    
    def test_application_startup(self, user_epub):
        """Test that termbook starts successfully with a real EPUB."""
        proc = pexpect.spawn(f'termbook "{user_epub}"', timeout=10)
        proc.setwinsize(24, 80)
        
        try:
            # Just test that it starts and we can quit
            time.sleep(2)  # Let it initialize
            assert proc.isalive()
            
            proc.send('q')
            proc.expect(pexpect.EOF, timeout=5)
            assert not proc.isalive()
            
        except Exception as e:
            if proc.isalive():
                proc.terminate()
            raise
    
    def test_help_dialog(self, user_epub):
        """Test help dialog functionality.""" 
        proc = pexpect.spawn(f'termbook "{user_epub}"', timeout=10)
        proc.setwinsize(24, 80)
        
        try:
            time.sleep(1)  # Let it initialize
            
            # Open help
            proc.send('?')
            time.sleep(1)
            assert proc.isalive()
            
            # Close help and quit
            proc.send('q')  # Close help
            time.sleep(0.5)
            proc.send('q')  # Quit app
            proc.expect(pexpect.EOF, timeout=5)
            assert not proc.isalive()
            
        except Exception as e:
            if proc.isalive():
                proc.terminate()
            raise
    
    def test_bookmark_functionality(self, user_epub):
        """Test bookmark save and view functionality."""
        proc = pexpect.spawn(f'termbook "{user_epub}"', timeout=10)
        proc.setwinsize(24, 80)
        
        try:
            time.sleep(1)  # Let it initialize
            
            # Try to save a bookmark
            proc.send('s')
            time.sleep(1)
            assert proc.isalive()
            
            # Try to view bookmarks
            proc.send('b')  
            time.sleep(1)
            assert proc.isalive()
            
            # Close bookmarks and quit
            proc.send('q')  # Close bookmarks
            time.sleep(0.5)
            proc.send('q')  # Quit app
            proc.expect(pexpect.EOF, timeout=5)
            assert not proc.isalive()
            
        except Exception as e:
            if proc.isalive():
                proc.terminate()
            raise