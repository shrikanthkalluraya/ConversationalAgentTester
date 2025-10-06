"""
Assertion Validator - COMPLETE UPDATED VERSION
Validates conversation results against expectations with array notation support

Features:
- Step-by-step validation with configurable failure handling
- Array notation: response_messages[*].text, items[0].name
- 11 validation operators including CONTAINS_ALL and CONTAINS_ANY
- Three assertion levels: CRITICAL, ERROR, WARNING
"""

import re
import logging
from typing import Dict, Any, List, Tuple, Union, Optional
from dataclasses import dataclass

# Import from flow_json_parser
try:
    from .flow_json_parser import ValidationRule, ValidationOperator, AssertionLevel
except ImportError:
    # For standalone testing
    from flow_json_parser import ValidationRule, ValidationOperator, AssertionLevel


@dataclass
class AssertionResult:
    """Result of a single assertion"""
    rule: ValidationRule
    passed: bool
    actual_value: Any
    expected_value: Any
    message: str
    assertion_level: AssertionLevel


@dataclass
class StepValidationResult:
    """Result of validating a single step"""
    step_id: str
    passed: bool
    assertions: List[AssertionResult]
    critical_failures: int
    errors: int
    warnings: int
    execution_time_ms: float
    should_stop: bool  # Whether to stop execution


class AssertionValidator:
    """
    Validates conversation flow results against expected outcomes
    Supports array notation for flexible field extraction
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def validate_step(self, step_result: Dict[str, Any], 
                     validation_rules: List[ValidationRule],
                     stop_on_critical: bool = True) -> StepValidationResult:
        """
        Validate a step's results against validation rules
        
        Args:
            step_result: Actual results from step execution
            validation_rules: List of validation rules to check
            stop_on_critical: Whether to stop on critical assertion failures
            
        Returns:
            StepValidationResult with detailed validation info
        """
        assertions = []
        critical_failures = 0
        errors = 0
        warnings = 0
        should_stop = False
        
        for rule in validation_rules:
            result = self._validate_rule(step_result, rule)
            assertions.append(result)
            
            if not result.passed:
                if result.assertion_level == AssertionLevel.CRITICAL:
                    critical_failures += 1
                    if stop_on_critical:
                        should_stop = True
                        self.logger.critical(
                            f"CRITICAL ASSERTION FAILED: {result.message}"
                        )
                elif result.assertion_level == AssertionLevel.ERROR:
                    errors += 1
                    self.logger.error(f"Assertion failed: {result.message}")
                else:  # WARNING
                    warnings += 1
                    self.logger.warning(f"Assertion warning: {result.message}")
        
        step_passed = critical_failures == 0 and errors == 0
        
        validation_result = StepValidationResult(
            step_id=step_result.get('step_id', 'unknown'),
            passed=step_passed,
            assertions=assertions,
            critical_failures=critical_failures,
            errors=errors,
            warnings=warnings,
            execution_time_ms=step_result.get('execution_time_ms', 0),
            should_stop=should_stop
        )
        
        return validation_result
    
    def _validate_rule(self, step_result: Dict[str, Any], 
                      rule: ValidationRule) -> AssertionResult:
        """Validate a single rule"""
        # Extract actual value from nested result
        actual_value = self._extract_field_value(step_result, rule.field)
        expected_value = rule.expected_value
        
        # Perform comparison based on operator
        passed = self._compare_values(actual_value, expected_value, rule.operator)
        
        # Generate message
        if rule.custom_message:
            message = rule.custom_message
        else:
            message = self._generate_default_message(
                rule.field, actual_value, expected_value, rule.operator, passed
            )
        
        return AssertionResult(
            rule=rule,
            passed=passed,
            actual_value=actual_value,
            expected_value=expected_value,
            message=message,
            assertion_level=rule.assertion_level
        )
    
    # ========================================================================
    # FIELD EXTRACTION WITH ARRAY NOTATION SUPPORT
    # ========================================================================
    
    def _extract_field_value(self, data: Dict[str, Any], field_path: str) -> Any:
        """
        Extract field value with full array notation support
        
        Supports:
            - Dot notation: 'intent.name'
            - Array wildcard: 'response_messages[*].text'
            - Array shorthand: 'response_messages[].text'
            - Array index: 'response_messages[0].text'
            - Nested: 'pages[*].transitions[*].target'
        
        Args:
            data: Dictionary to extract from
            field_path: Path to field using dot/array notation
            
        Returns:
            Extracted value(s)
        """
        # BACKWARD COMPATIBILITY: Auto-convert common shortcuts
        shortcuts = {
            'response_messages': 'response_messages[*].text',
            'response_text': 'response_messages[*].text',
            'parameters': 'parameters',  # Keep as-is for dict key extraction
        }
        
        # Check if it's a known shortcut
        if field_path in shortcuts:
            field_path = shortcuts[field_path]
        
        # Special handling for 'parameters' without sub-path (return keys for contains_all/any)
        if field_path == 'parameters':
            params = data.get('parameters', {})
            if isinstance(params, dict):
                return list(params.keys())
            return params
        
        # Check for array notation
        if '[' in field_path:
            return self._extract_with_array_notation(data, field_path)
        
        # Regular dot notation
        return self._extract_simple_path(data, field_path)
    
    def _extract_simple_path(self, data: Dict[str, Any], field_path: str) -> Any:
        """Extract value using simple dot notation"""
        parts = field_path.split('.')
        value = data
        
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return None
            elif isinstance(value, list):
                # If we hit a list without array notation, return it
                return value
            else:
                return None
        
        return value
    
    def _extract_with_array_notation(self, data: Dict[str, Any], field_path: str) -> Any:
        """
        Extract value using array notation
        
        Examples:
            'response_messages[*].text' -> extracts text from all messages
            'response_messages[0].text' -> extracts text from first message
            'items[*].nested[*].field' -> handles nested arrays
        """
        # Parse the path into segments
        segments = self._parse_array_path(field_path)
        
        if not segments:
            return None
        
        # Process segments sequentially
        current_values = [data]  # Start with data wrapped in list
        
        for i, segment in enumerate(segments):
            new_values = []
            
            if segment['type'] == 'field':
                # Extract field from all current values
                for value in current_values:
                    if isinstance(value, dict):
                        extracted = value.get(segment['name'])
                        if extracted is not None:
                            new_values.append(extracted)
                current_values = new_values
                
            elif segment['type'] == 'array_wildcard':
                # Flatten arrays - extract all items
                for value in current_values:
                    if isinstance(value, (list, tuple)):
                        new_values.extend(value)
                    else:
                        new_values.append(value)
                current_values = new_values
                
            elif segment['type'] == 'array_index':
                # Extract specific index from arrays
                for value in current_values:
                    if isinstance(value, (list, tuple)):
                        idx = segment['index_value']
                        if 0 <= idx < len(value):
                            new_values.append(value[idx])
                current_values = new_values
        
        # Post-process result
        if not current_values:
            return None
        
        # If extracting 'text' field, combine and lowercase
        if field_path.endswith('.text') or field_path.endswith('[*].text'):
            text_parts = [str(v) for v in current_values if v]
            return ' '.join(text_parts).lower()
        
        # If single value, unwrap
        if len(current_values) == 1:
            return current_values[0]
        
        # Return list
        return current_values
    
    def _parse_array_path(self, field_path: str) -> List[Dict[str, Any]]:
        """
        Parse field path with array notation into segments
        
        Examples:
            'response_messages[*].text' -> [
                {'type': 'field', 'name': 'response_messages'},
                {'type': 'array_wildcard'},
                {'type': 'field', 'name': 'text'}
            ]
            
            'items[0].name' -> [
                {'type': 'field', 'name': 'items'},
                {'type': 'array_index', 'index_value': 0},
                {'type': 'field', 'name': 'name'}
            ]
        """
        segments = []
        
        # Regex to match: field_name, [*], [number], or []
        pattern = r'([^\[\]\.]+)|\[(\*|\d+|)\]'
        matches = re.findall(pattern, field_path)
        
        for match in matches:
            field_name, bracket_content = match
            
            if field_name:
                # Regular field name
                segments.append({
                    'type': 'field',
                    'name': field_name
                })
            elif bracket_content is not None:
                # Array notation
                if bracket_content == '*' or bracket_content == '':
                    # Wildcard [*] or empty []
                    segments.append({
                        'type': 'array_wildcard'
                    })
                else:
                    # Specific index [0], [1], etc
                    segments.append({
                        'type': 'array_index',
                        'index_value': int(bracket_content)
                    })
        
        return segments
    
    # ========================================================================
    # VALUE COMPARISON WITH ALL OPERATORS
    # ========================================================================
    
    def _compare_values(self, actual: Any, expected: Any, 
                       operator: ValidationOperator) -> bool:
        """Compare values based on operator"""
        try:
            if operator == ValidationOperator.EQUALS:
                return actual == expected
            
            elif operator == ValidationOperator.NOT_EQUALS:
                return actual != expected
            
            elif operator == ValidationOperator.CONTAINS:
                if isinstance(actual, str):
                    return str(expected).lower() in actual.lower()
                elif isinstance(actual, (list, tuple)):
                    return expected in actual
                return False
            
            elif operator == ValidationOperator.CONTAINS_ALL:
                """
                Check if actual value contains ALL items from expected list
                """
                if not isinstance(expected, (list, tuple)):
                    self.logger.warning(f"CONTAINS_ALL expects a list, got {type(expected)}")
                    return False
                
                if isinstance(actual, str):
                    actual_lower = actual.lower()
                    return all(str(item).lower() in actual_lower for item in expected)
                
                elif isinstance(actual, (list, tuple)):
                    return all(item in actual for item in expected)
                
                elif isinstance(actual, set):
                    return set(expected).issubset(actual)
                
                else:
                    self.logger.warning(f"CONTAINS_ALL: unsupported type {type(actual)}")
                    return False
            
            elif operator == ValidationOperator.CONTAINS_ANY:
                """
                Check if actual value contains ANY item from expected list
                """
                if not isinstance(expected, (list, tuple)):
                    self.logger.warning(f"CONTAINS_ANY expects a list, got {type(expected)}")
                    return False
                
                if isinstance(actual, str):
                    actual_lower = actual.lower()
                    return any(str(item).lower() in actual_lower for item in expected)
                
                elif isinstance(actual, (list, tuple)):
                    return any(item in actual for item in expected)
                
                elif isinstance(actual, set):
                    return bool(set(expected).intersection(actual))
                
                else:
                    self.logger.warning(f"CONTAINS_ANY: unsupported type {type(actual)}")
                    return False
            
            elif operator == ValidationOperator.GREATER_THAN:
                return float(actual) > float(expected)
            
            elif operator == ValidationOperator.LESS_THAN:
                return float(actual) < float(expected)
            
            elif operator == ValidationOperator.GREATER_EQUAL:
                return float(actual) >= float(expected)
            
            elif operator == ValidationOperator.LESS_EQUAL:
                return float(actual) <= float(expected)
            
            elif operator == ValidationOperator.REGEX_MATCH:
                if isinstance(actual, str):
                    return bool(re.match(str(expected), actual))
                return False
            
            elif operator == ValidationOperator.IN_LIST:
                if isinstance(expected, (list, tuple)):
                    return actual in expected
                return False
            
            else:
                self.logger.warning(f"Unknown operator: {operator}")
                return False
                
        except Exception as e:
            self.logger.error(f"Comparison error: {e}")
            return False
    
    def _generate_default_message(self, field: str, actual: Any, 
                                  expected: Any, operator: ValidationOperator, 
                                  passed: bool) -> str:
        """Generate default assertion message"""
        status = "✓" if passed else "✗"
        
        op_text = {
            ValidationOperator.EQUALS: "should equal",
            ValidationOperator.NOT_EQUALS: "should not equal",
            ValidationOperator.CONTAINS: "should contain",
            ValidationOperator.CONTAINS_ALL: "should contain all of",
            ValidationOperator.CONTAINS_ANY: "should contain any of",
            ValidationOperator.GREATER_THAN: "should be greater than",
            ValidationOperator.LESS_THAN: "should be less than",
            ValidationOperator.GREATER_EQUAL: "should be >= ",
            ValidationOperator.LESS_EQUAL: "should be <=",
            ValidationOperator.REGEX_MATCH: "should match pattern",
            ValidationOperator.IN_LIST: "should be in"
        }.get(operator, "should match")
        
        # Format expected value for display
        if isinstance(expected, (list, tuple)):
            if len(expected) > 5:
                expected_display = f"[{', '.join(repr(x) for x in expected[:5])}, ...]"
            else:
                expected_display = f"[{', '.join(repr(x) for x in expected)}]"
        else:
            expected_display = repr(expected)
        
        # Format actual value for display (truncate if too long)
        if isinstance(actual, str) and len(actual) > 50:
            actual_display = repr(actual[:50] + "...")
        elif isinstance(actual, (list, tuple)) and len(actual) > 5:
            actual_display = f"[{', '.join(repr(x) for x in actual[:5])}, ...]"
        else:
            actual_display = repr(actual)
        
        return (f"{status} {field} {op_text} {expected_display} "
                f"(actual: {actual_display})")
    
    # ========================================================================
    # REPORT GENERATION
    # ========================================================================
    
    def generate_report(self, step_results: List[StepValidationResult]) -> Dict[str, Any]:
        """
        Generate comprehensive validation report
        
        Returns:
            Report dictionary with statistics and details
        """
        total_steps = len(step_results)
        passed_steps = sum(1 for r in step_results if r.passed)
        failed_steps = total_steps - passed_steps
        
        total_assertions = sum(len(r.assertions) for r in step_results)
        passed_assertions = sum(
            sum(1 for a in r.assertions if a.passed) 
            for r in step_results
        )
        
        total_critical = sum(r.critical_failures for r in step_results)
        total_errors = sum(r.errors for r in step_results)
        total_warnings = sum(r.warnings for r in step_results)
        
        # Find first failure
        first_failure = None
        for result in step_results:
            if not result.passed:
                first_failure = result.step_id
                break
        
        report = {
            'summary': {
                'total_steps': total_steps,
                'passed_steps': passed_steps,
                'failed_steps': failed_steps,
                'success_rate': (passed_steps / total_steps * 100) if total_steps > 0 else 0,
                'total_assertions': total_assertions,
                'passed_assertions': passed_assertions,
                'failed_assertions': total_assertions - passed_assertions,
                'critical_failures': total_critical,
                'errors': total_errors,
                'warnings': total_warnings,
                'first_failure_step': first_failure
            },
            'step_details': [
                self._format_step_result(result) 
                for result in step_results
            ]
        }
        
        return report
    
    def _format_step_result(self, result: StepValidationResult) -> Dict[str, Any]:
        """Format step result for report"""
        return {
            'step_id': result.step_id,
            'passed': result.passed,
            'execution_time_ms': result.execution_time_ms,
            'critical_failures': result.critical_failures,
            'errors': result.errors,
            'warnings': result.warnings,
            'assertions': [
                {
                    'field': a.rule.field,
                    'operator': a.rule.operator.value,
                    'expected': a.expected_value,
                    'actual': a.actual_value,
                    'passed': a.passed,
                    'message': a.message,
                    'level': a.assertion_level.value
                }
                for a in result.assertions
            ]
        }


# ============================================================================
# STANDALONE TESTS
# ============================================================================

if __name__ == "__main__":
    """Run standalone tests to verify functionality"""
    import sys
    
    print("="*70)
    print("TESTING ASSERTION VALIDATOR WITH ARRAY NOTATION")
    print("="*70)
    
    # Mock imports for standalone testing
    from enum import Enum
    from dataclasses import dataclass
    
    class ValidationOperator(Enum):
        EQUALS = "equals"
        CONTAINS = "contains"
        CONTAINS_ALL = "contains_all"
        CONTAINS_ANY = "contains_any"
        GREATER_THAN = "gt"
        LESS_THAN = "lt"
        GREATER_EQUAL = "gte"
        LESS_EQUAL = "lte"
        REGEX_MATCH = "regex"
        NOT_EQUALS = "not_equals"
        IN_LIST = "in"
    
    class AssertionLevel(Enum):
        CRITICAL = "critical"
        ERROR = "error"
        WARNING = "warning"
    
    @dataclass
    class ValidationRule:
        field: str
        operator: ValidationOperator
        expected_value: Any
        assertion_level: AssertionLevel = AssertionLevel.CRITICAL
        custom_message: Optional[str] = None
    
    # Create validator
    validator = AssertionValidator()
    
    # Test data mimicking Dialogflow CX response
    test_data = {
        'step_id': 'test_step',
        'intent': 'greeting',
        'intent_confidence': 0.85,
        'response_messages': [
            {'type': 'text', 'text': 'Hello there!'},
            {'type': 'text', 'text': 'I can help with billing and account issues.'},
            {'type': 'payload', 'data': 'custom'}
        ],
        'parameters': {
            'name': 'John',
            'email': 'john@example.com'
        },
        'execution_time_ms': 1250
    }
    
    print("\n📊 Test Data:")
    print(f"  Intent: {test_data['intent']} ({test_data['intent_confidence']})")
    print(f"  Messages: {len(test_data['response_messages'])}")
    print(f"  Parameters: {list(test_data['parameters'].keys())}")
    
    # Test 1: Array wildcard notation
    print("\n🧪 Test 1: Array Wildcard - response_messages[*].text")
    result = validator._extract_field_value(test_data, 'response_messages[*].text')
    print(f"  Extracted: '{result}'")
    assert result == "hello there! i can help with billing and account issues."
    print("  ✅ PASS")
    
    # Test 2: Array index notation
    print("\n🧪 Test 2: Array Index - response_messages[0].text")
    result = validator._extract_field_value(test_data, 'response_messages[0].text')
    print(f"  Extracted: '{result}'")
    assert result == "Hello there!"
    print("  ✅ PASS")
    
    # Test 3: Backward compatibility
    print("\n🧪 Test 3: Backward Compatibility - response_messages")
    result = validator._extract_field_value(test_data, 'response_messages')
    print(f"  Extracted: '{result}'")
    assert result == "hello there! i can help with billing and account issues."
    print("  ✅ PASS")
    
    # Test 4: CONTAINS_ALL operator
    print("\n🧪 Test 4: CONTAINS_ALL Operator")
    rule = ValidationRule(
        field='response_messages[*].text',
        operator=ValidationOperator.CONTAINS_ALL,
        expected_value=['hello', 'billing', 'account'],
        assertion_level=AssertionLevel.CRITICAL
    )
    assertion_result = validator._validate_rule(test_data, rule)
    print(f"  Expected: {rule.expected_value}")
    print(f"  Result: {'✅ PASS' if assertion_result.passed else '❌ FAIL'}")
    print(f"  Message: {assertion_result.message}")
    assert assertion_result.passed
    print("  ✅ PASS")
    
    # Test 5: CONTAINS_ANY operator
    print("\n🧪 Test 5: CONTAINS_ANY Operator")
    rule = ValidationRule(
        field='response_messages[*].text',
        operator=ValidationOperator.CONTAINS_ANY,
        expected_value=['hello', 'goodbye', 'missing'],
        assertion_level=AssertionLevel.WARNING
    )
    assertion_result = validator._validate_rule(test_data, rule)
    print(f"  Expected any of: {rule.expected_value}")
    print(f"  Result: {'✅ PASS' if assertion_result.passed else '❌ FAIL'}")
    assert assertion_result.passed
    print("  ✅ PASS")
    
    # Test 6: Parameters extraction
    print("\n🧪 Test 6: Parameters Extraction")
    result = validator._extract_field_value(test_data, 'parameters')
    print(f"  Extracted keys: {result}")
    assert set(result) == {'name', 'email'}
    print("  ✅ PASS")
    
    # Test 7: Nested field extraction
    print("\n🧪 Test 7: Nested Field - parameters.email")
    result = validator._extract_field_value(test_data, 'parameters.email')
    print(f"  Extracted: '{result}'")
    assert result == 'john@example.com'
    print("  ✅ PASS")
    
    # Test 8: Full validation with multiple rules
    print("\n🧪 Test 8: Full Step Validation")
    rules = [
        ValidationRule('intent', ValidationOperator.EQUALS, 'greeting', AssertionLevel.CRITICAL),
        ValidationRule('intent_confidence', ValidationOperator.GREATER_EQUAL, 0.7, AssertionLevel.CRITICAL),
        ValidationRule('response_messages[*].text', ValidationOperator.CONTAINS_ALL, ['hello', 'help'], AssertionLevel.ERROR),
        ValidationRule('execution_time_ms', ValidationOperator.LESS_THAN, 2000, AssertionLevel.WARNING)
    ]
    
    validation_result = validator.validate_step(test_data, rules, stop_on_critical=True)
    
    print(f"  Step: {validation_result.step_id}")
    print(f"  Passed: {'✅ YES' if validation_result.passed else '❌ NO'}")
    print(f"  Critical failures: {validation_result.critical_failures}")
    print(f"  Errors: {validation_result.errors}")
    print(f"  Warnings: {validation_result.warnings}")
    print(f"  Assertions: {len(validation_result.assertions)}")
    
    for assertion in validation_result.assertions:
        status = '✅' if assertion.passed else '❌'
        print(f"    {status} {assertion.message}")
    
    assert validation_result.passed
    print("  ✅ PASS")
    
    print("\n" + "="*70)
    print("🎉 ALL TESTS PASSED!")
    print("="*70)
    print("\nArray notation features verified:")
    print("  ✅ Wildcard extraction: field[*].subfield")
    print("  ✅ Index extraction: field[0].subfield")
    print("  ✅ Backward compatibility")
    print("  ✅ CONTAINS_ALL operator")
    print("  ✅ CONTAINS_ANY operator")
    print("  ✅ Nested field extraction")
    print("  ✅ Full validation flow")
    print("\n✨ assertion_validator.py is ready to use!")
