#!/usr/bin/env python3
"""
Simple test script to verify that our input validation is working correctly.
Run with: python test_validation.py
"""

import sys
import traceback

# Mock Flask request context for testing
class MockRequest:
    def __init__(self):
        self.remote_addr = "127.0.0.1"
        self.user_agent = MockUserAgent()
        self.endpoint = "test"
        self.method = "GET"

class MockUserAgent:
    def __init__(self):
        self.string = "Test User Agent"

# Mock request object
request = MockRequest()

# Create a minimal validators module for testing
sys.path.insert(0, '.')

from validators import (
    ValidationError,
    validate_integer,
    validate_positive_integer,
    validate_non_negative_integer,
    validate_product_id,
    validate_location_id,
    validate_quantity
)


def run_test(test_name, test_func):
    """Helper to run a test and report results."""
    try:
        test_func()
        print(f"✅ {test_name}")
        return True
    except Exception as e:
        print(f"❌ {test_name}: {e}")
        traceback.print_exc()
        return False


def test_basic_validation():
    """Test basic integer validation."""
    # Valid cases
    assert validate_integer("42", "test") == 42
    assert validate_integer("0", "test") == 0
    assert validate_integer("-5", "test") == -5
    assert validate_integer("  100  ", "test") == 100  # Whitespace
    
    # Invalid cases should raise ValidationError
    try:
        validate_integer("abc", "test")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass
    
    try:
        validate_integer("3.14", "test")
        assert False, "Should have raised ValidationError for float"
    except ValidationError:
        pass
    
    try:
        validate_integer("", "test")
        assert False, "Should have raised ValidationError for empty"
    except ValidationError:
        pass


def test_suspicious_input_detection():
    """Test that suspicious SQL injection patterns are detected."""
    suspicious_inputs = [
        "1; DROP TABLE users--",
        "1 OR 1=1",
        "1/*comment*/",
        "1 AND 1=1",
        "1; DELETE FROM products"
    ]
    
    for malicious_input in suspicious_inputs:
        try:
            validate_integer(malicious_input, "test")
            assert False, f"Should have rejected suspicious input: {malicious_input}"
        except ValidationError as e:
            assert "invalid characters" in e.message


def test_range_validation():
    """Test range validation."""
    # Valid range
    assert validate_integer("50", "test", min_val=0, max_val=100) == 50
    assert validate_integer("0", "test", min_val=0, max_val=100) == 0
    assert validate_integer("100", "test", min_val=0, max_val=100) == 100
    
    # Out of range
    try:
        validate_integer("-1", "test", min_val=0, max_val=100)
        assert False, "Should reject value below minimum"
    except ValidationError:
        pass
    
    try:
        validate_integer("101", "test", min_val=0, max_val=100)
        assert False, "Should reject value above maximum"
    except ValidationError:
        pass


def test_domain_validators():
    """Test domain-specific validators."""
    # Product ID validation
    assert validate_product_id("1") == 1
    assert validate_product_id("999") == 999
    
    try:
        validate_product_id("0")  # Must be positive
        assert False, "Product ID should reject 0"
    except ValidationError:
        pass
    
    try:
        validate_product_id("-1")  # Must be positive  
        assert False, "Product ID should reject negative"
    except ValidationError:
        pass
    
    # Location ID validation
    assert validate_location_id("1") == 1
    
    try:
        validate_location_id("0")
        assert False, "Location ID should reject 0"
    except ValidationError:
        pass
    
    # Quantity validation
    assert validate_quantity("0") == 0  # Zero allowed by default
    assert validate_quantity("100") == 100
    
    try:
        validate_quantity("-1")  # Negative not allowed
        assert False, "Quantity should reject negative"
    except ValidationError:
        pass
    
    # Quantity with allow_zero=False
    assert validate_quantity("1", allow_zero=False) == 1
    
    try:
        validate_quantity("0", allow_zero=False)
        assert False, "Should reject zero when allow_zero=False"
    except ValidationError:
        pass


def test_allow_none():
    """Test allow_none functionality."""
    # When allow_none=True
    assert validate_integer(None, "test", allow_none=True) is None
    assert validate_integer("", "test", allow_none=True) is None
    assert validate_integer("  ", "test", allow_none=True) is None
    
    # When allow_none=False (default)
    try:
        validate_integer(None, "test", allow_none=False)
        assert False, "Should reject None when allow_none=False"
    except ValidationError:
        pass


def test_edge_cases():
    """Test edge cases and boundary conditions."""
    # Large numbers
    assert validate_integer("2147483647", "test") == 2147483647
    
    # Unicode characters
    try:
        validate_integer("①②③", "test")  # Unicode digits
        assert False, "Should reject unicode digits"
    except ValidationError:
        pass
    
    # Scientific notation
    try:
        validate_integer("1e5", "test")
        assert False, "Should reject scientific notation"
    except ValidationError:
        pass
    
    # Boolean rejection
    try:
        validate_integer(True, "test")
        assert False, "Should reject boolean True"
    except ValidationError:
        pass
    
    try:
        validate_integer(False, "test")
        assert False, "Should reject boolean False"
    except ValidationError:
        pass


def main():
    """Run all tests."""
    print("Running Input Validation Tests...")
    print("=" * 50)
    
    tests = [
        ("Basic validation", test_basic_validation),
        ("Suspicious input detection", test_suspicious_input_detection),
        ("Range validation", test_range_validation),
        ("Domain validators", test_domain_validators),
        ("Allow none functionality", test_allow_none),
        ("Edge cases", test_edge_cases),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        if run_test(test_name, test_func):
            passed += 1
    
    print("=" * 50)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! Input validation is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Check the validation implementation.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)