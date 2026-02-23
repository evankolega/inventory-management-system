"""
Mock Flask module for testing validators without installing Flask.
"""

class MockRequest:
    def __init__(self):
        self.remote_addr = "127.0.0.1"
        self.user_agent = MockUserAgent()
        self.endpoint = "test"
        self.method = "GET"
        self.form = {}
        self.args = {}

class MockUserAgent:
    def __init__(self):
        self.string = "Test User Agent"

# Global request object
request = MockRequest()

def flash(message, category=None):
    """Mock flash function."""
    print(f"Flash: {message} [{category}]")

def redirect(url):
    """Mock redirect function."""
    return f"Redirect to: {url}"

def url_for(endpoint):
    """Mock url_for function."""
    return f"/{endpoint}"