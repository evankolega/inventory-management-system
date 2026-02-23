"""
Input Validation Module for Inventory Application
Addresses CWE-20: Improper Input Validation

This module provides comprehensive validation for all numeric inputs
to prevent injection attacks, type errors, and data corruption.
"""

import logging
from functools import wraps
from typing import Optional, Any

try:
    from flask import request, flash, redirect, url_for
except ImportError:
    # Use mock Flask for testing
    from flask_mock import request, flash, redirect, url_for

# Configure security logging
security_logger = logging.getLogger('security')


class ValidationError(Exception):
    """
    Custom exception for validation failures.
    
    Attributes:
        field: The name of the field that failed validation
        message: Human-readable error message
        value: The invalid value that was provided
    """
    def __init__(self, field: str, message: str, value: Any = None):
        self.field = field
        self.message = message
        self.value = value
        super().__init__(f"Validation failed for '{field}': {message}")
    
    def __repr__(self):
        return f"ValidationError(field='{self.field}', message='{self.message}')"


# ============================================================================
# CORE VALIDATION FUNCTIONS
# ============================================================================

def validate_integer(
    value: Any,
    field_name: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    allow_none: bool = False
) -> Optional[int]:
    """
    Validate and convert a value to an integer with range checking.
    
    Args:
        value: The value to validate (typically string from form/query)
        field_name: Name of the field for error messages
        min_val: Minimum acceptable value (inclusive), None for no minimum
        max_val: Maximum acceptable value (inclusive), None for no maximum
        allow_none: If True, None/empty values return None instead of raising
        
    Returns:
        Validated integer value, or None if allow_none=True and value is empty
        
    Raises:
        ValidationError: If validation fails
    """
    # Handle None/empty cases
    if value is None or (isinstance(value, str) and value.strip() == ''):
        if allow_none:
            return None
        raise ValidationError(
            field_name,
            f"{field_name} is required",
            value
        )
    
    # Convert string to integer
    if isinstance(value, str):
        value = value.strip()
        
        # Security: Check for suspicious patterns before conversion
        # This catches some injection attempts early
        suspicious_patterns = [';', '--', '/*', '*/', 'OR', 'AND', 'DROP', 'DELETE']
        value_upper = value.upper()
        for pattern in suspicious_patterns:
            if pattern in value_upper:
                _log_security_event(
                    f"Suspicious input detected in {field_name}",
                    {'value': value[:100], 'pattern': pattern}  # Truncate for safety
                )
                raise ValidationError(
                    field_name,
                    f"{field_name} contains invalid characters",
                    value
                )
        
        # Attempt conversion
        try:
            value = int(value)
        except ValueError:
            # Check if it's a float string (e.g., "3.14")
            try:
                float_val = float(value)
                raise ValidationError(
                    field_name,
                    f"{field_name} must be a whole number, not a decimal",
                    value
                )
            except ValueError:
                pass
            
            raise ValidationError(
                field_name,
                f"{field_name} must be a valid integer",
                value
            )
    
    # At this point, value should be an integer
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(
            field_name,
            f"{field_name} must be a valid integer",
            value
        )
    
    # Range validation
    if min_val is not None and value < min_val:
        raise ValidationError(
            field_name,
            f"{field_name} must be at least {min_val}",
            value
        )
    
    if max_val is not None and value > max_val:
        raise ValidationError(
            field_name,
            f"{field_name} must be at most {max_val}",
            value
        )
    
    return value


def validate_positive_integer(
    value: Any,
    field_name: str,
    max_val: Optional[int] = None,
    allow_none: bool = False
) -> Optional[int]:
    """
    Validate that a value is a positive integer (> 0).
    Convenience wrapper for validate_integer with min_val=1.
    """
    return validate_integer(value, field_name, min_val=1, max_val=max_val, allow_none=allow_none)


def validate_non_negative_integer(
    value: Any,
    field_name: str,
    max_val: Optional[int] = None,
    allow_none: bool = False
) -> Optional[int]:
    """
    Validate that a value is a non-negative integer (>= 0).
    Convenience wrapper for validate_integer with min_val=0.
    """
    return validate_integer(value, field_name, min_val=0, max_val=max_val, allow_none=allow_none)


# ============================================================================
# DOMAIN-SPECIFIC VALIDATORS
# ============================================================================

# Define reasonable limits for your application
MAX_PRODUCT_ID = 2147483647  # Max 32-bit signed integer (typical DB limit)
MAX_LOCATION_ID = 2147483647
MAX_QUANTITY = 10000000  # 10 million - adjust based on business requirements


def validate_product_id(value: Any, allow_none: bool = False) -> Optional[int]:
    """
    Validate a product ID.
    
    Product IDs must be positive integers within database limits.
    """
    return validate_positive_integer(
        value,
        "Product ID",
        max_val=MAX_PRODUCT_ID,
        allow_none=allow_none
    )


def validate_location_id(value: Any, allow_none: bool = False) -> Optional[int]:
    """
    Validate a location ID.
    
    Location IDs must be positive integers within database limits.
    """
    return validate_positive_integer(
        value,
        "Location ID",
        max_val=MAX_LOCATION_ID,
        allow_none=allow_none
    )


def validate_quantity(value: Any, allow_none: bool = False, allow_zero: bool = True) -> Optional[int]:
    """
    Validate a quantity value.
    
    Quantities must be non-negative integers (or positive if allow_zero=False).
    
    Args:
        value: The quantity to validate
        allow_none: If True, None/empty returns None
        allow_zero: If True, zero is valid; if False, must be positive
    """
    min_val = 0 if allow_zero else 1
    return validate_integer(
        value,
        "Quantity",
        min_val=min_val,
        max_val=MAX_QUANTITY,
        allow_none=allow_none
    )


# ============================================================================
# REQUEST HELPER FUNCTIONS
# ============================================================================

def get_form_int(
    field: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    required: bool = True
) -> Optional[int]:
    """
    Extract and validate an integer from form data.
    
    Args:
        field: Form field name
        min_val: Minimum acceptable value
        max_val: Maximum acceptable value  
        required: If False, missing field returns None
        
    Returns:
        Validated integer or None
        
    Raises:
        ValidationError: If validation fails
    """
    value = request.form.get(field)
    return validate_integer(
        value,
        field,
        min_val=min_val,
        max_val=max_val,
        allow_none=not required
    )


def get_query_int(
    field: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    required: bool = True
) -> Optional[int]:
    """
    Extract and validate an integer from query parameters.
    
    Args:
        field: Query parameter name
        min_val: Minimum acceptable value
        max_val: Maximum acceptable value
        required: If False, missing parameter returns None
        
    Returns:
        Validated integer or None
        
    Raises:
        ValidationError: If validation fails
    """
    value = request.args.get(field)
    return validate_integer(
        value,
        field,
        min_val=min_val,
        max_val=max_val,
        allow_none=not required
    )


# ============================================================================
# SECURITY LOGGING
# ============================================================================

def _log_security_event(event_type: str, details: dict):
    """
    Log security-relevant events for monitoring and alerting.
    
    In production, this should integrate with your SIEM/logging infrastructure.
    """
    import json
    from datetime import datetime
    
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'event_type': event_type,
        'ip_address': request.remote_addr if request else None,
        'user_agent': request.user_agent.string if request else None,
        'endpoint': request.endpoint if request else None,
        'method': request.method if request else None,
        'details': details
    }
    
    security_logger.warning(f"SECURITY_EVENT: {json.dumps(log_entry)}")