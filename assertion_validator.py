"""
Assertion Validator - Validates conversation results against expectations
Supports step-by-step validation with configurable failure handling
"""

import re
import logging
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
from .flow_json_parser import ValidationRule, ValidationOperator, AssertionLevel


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
    """
    
    def __init__(self, logger: logging.Logger = None):
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
    
    def _extract_field_value(self, data: Dict[str, Any], field_path: str) -> Any:
        """
        Extract field value from nested dictionary using dot notation
        Example: 'intent.name' -> data['intent']['name']
        """
        parts = field_path.split('.')
        value = data
        
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return None
            else:
                return None
        
        return value
    
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
                    return expected in actual
                elif isinstance(actual, (list, tuple)):
                    return expected in actual
                return False
            elif operator == ValidationOperator.CONTAINS_ALL:
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
            ValidationOperator.CONTAINS_ALL: "should contain all of",      # ← ADD
            ValidationOperator.CONTAINS_ANY: "should contain any of",      # ← ADD
            ValidationOperator.GREATER_THAN: "should be greater than",
            ValidationOperator.LESS_THAN: "should be less than",
            ValidationOperator.GREATER_EQUAL: "should be >= ",
            ValidationOperator.LESS_EQUAL: "should be <=",
            ValidationOperator.REGEX_MATCH: "should match pattern",
            ValidationOperator.IN_LIST: "should be in"
        }.get(operator, "should match")
        
        # Format expected value for display
        if isinstance(expected, (list, tuple)):
            expected_display = f"[{', '.join(repr(x) for x in expected)}]"
        else:
            expected_display = repr(expected)
        
        # Format actual value (truncate if too long)
        if isinstance(actual, str) and len(actual) > 50:
            actual_display = repr(actual[:50] + "...")
        else:
            actual_display = repr(actual)
        
        return (f"{status} {field} {op_text} {expected_display} "
                f"(actual: {actual_display})")
    
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
                    'passed': a.passed,
                    'message': a.message,
                    'level': a.assertion_level.value
                }
                for a in result.assertions
            ]
        }
