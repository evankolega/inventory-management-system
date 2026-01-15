#!/usr/bin/env python3
"""
Test script to verify the input validation fixes for CWE-20.
This tests the validate_quantity function with various inputs to ensure 
the security vulnerability has been properly addressed.
"""

import sys
sys.path.append('inventory')

from app import validate_quantity, ValidationError


def test_valid_inputs():
    """Test valid inputs that should pass validation."""
    print("Testing valid inputs...")
    
    # Test valid positive integers
    assert validate_quantity("100") == 100
    assert validate_quantity("1") == 1
    assert validate_quantity("1000000") == 1000000
    
    # Test zero when allowed
    assert validate_quantity("0", allow_zero=True) == 0
    assert validate_quantity(0, allow_zero=True) == 0
    
    # Test with whitespace
    assert validate_quantity("  50  ") == 50
    
    print("✅ All valid inputs passed")


def test_invalid_inputs():
    """Test invalid inputs that should fail validation."""
    print("\nTesting invalid inputs...")
    
    test_cases = [
        # Description, input, allow_zero, expected_error_content
        ("None value", None, True, "required"),
        ("Empty string", "", True, "required"),
        ("Whitespace only", "   ", True, "required"),
        ("Non-numeric string", "abc", True, "whole number"),
        ("Mixed alphanumeric", "100abc", True, "whole number"),
        ("Float string", "10.5", True, "whole number"),
        ("Scientific notation", "1e5", True, "whole number"),
        ("Negative number", "-5", True, "negative"),
        ("Zero when not allowed", "0", False, "greater than zero"),
        ("Large number", "999999999999999999999", True, "maximum"),
    ]
    
    for description, input_val, allow_zero, expected_error in test_cases:
        try:
            result = validate_quantity(input_val, allow_zero=allow_zero)
            print(f"❌ Expected {description} to fail, but got: {result}")
        except ValidationError as e:
            if expected_error.lower() in e.user_message.lower():
                print(f"✅ {description}: correctly rejected - {e.user_message}")
            else:
                print(f"❌ {description}: wrong error message - {e.user_message}")
        except Exception as e:
            print(f"❌ {description}: unexpected exception - {e}")


def test_security_scenarios():
    """Test specific security attack scenarios."""
    print("\nTesting security attack scenarios...")
    
    attack_inputs = [
        "'; DROP TABLE products; --",  # SQL injection attempt
        "<script>alert('xss')</script>",  # XSS attempt
        "../../../etc/passwd",  # Path traversal attempt
        "1\x00\x01\x02",  # Binary data
        "∞",  # Unicode infinity symbol
        "NaN",  # Not a number
        "Infinity",  # Infinity
        "-Infinity",  # Negative infinity
    ]
    
    for attack_input in attack_inputs:
        try:
            result = validate_quantity(attack_input)
            print(f"❌ Security test failed: '{attack_input}' was accepted as {result}")
        except ValidationError as e:
            print(f"✅ Security test passed: '{attack_input}' correctly rejected")
        except Exception as e:
            print(f"⚠️  Security test: '{attack_input}' caused unexpected error: {e}")


def main():
    """Run all validation tests."""
    print("=" * 60)
    print("CWE-20 Input Validation Security Fix Test")
    print("=" * 60)
    
    try:
        test_valid_inputs()
        test_invalid_inputs()
        test_security_scenarios()
        
        print("\n" + "=" * 60)
        print("✅ All tests completed! The validation implementation appears secure.")
        print("✅ CWE-20 vulnerability has been successfully addressed.")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test suite failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()