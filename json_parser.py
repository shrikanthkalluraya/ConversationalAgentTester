"""
Flow JSON Parser - Parses and validates conversation flow definitions
Supports step-by-step execution with assertions
"""

import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


class ValidationOperator(Enum):
    """Operators for validation rules"""
    EQUALS = "equals"
    CONTAINS = "contains"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    GREATER_EQUAL = "gte"
    LESS_EQUAL = "lte"
    REGEX_MATCH = "regex"
    NOT_EQUALS = "not_equals"
    IN_LIST = "in"


class AssertionLevel(Enum):
    """Severity levels for assertions"""
    CRITICAL = "critical"  # Stop execution on failure
    ERROR = "error"        # Log error, continue execution
    WARNING = "warning"    # Log warning, continue execution


@dataclass
class ValidationRule:
    """Single validation rule"""
    field: str
    operator: ValidationOperator
    expected_value: Any
    assertion_level: AssertionLevel = AssertionLevel.CRITICAL
    custom_message: Optional[str] = None


@dataclass
class FlowStep:
    """Single step in conversation flow"""
    step_id: str
    user_input: str
    expected_intent: Optional[str] = None
    expected_entities: Optional[Dict[str, Any]] = None
    expected_response_contains: Optional[List[str]] = None
    validation_rules: List[ValidationRule] = None
    continue_on_failure: bool = False
    timeout_seconds: int = 30
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.validation_rules is None:
            self.validation_rules = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class FlowDefinition:
    """Complete flow definition"""
    flow_id: str
    flow_name: str
    description: str
    agent_id: Optional[str]
    language_code: str = "en"
    steps: List[FlowStep] = None
    global_validation_rules: List[ValidationRule] = None
    success_criteria: Dict[str, Any] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.steps is None:
            self.steps = []
        if self.global_validation_rules is None:
            self.global_validation_rules = []
        if self.success_criteria is None:
            self.success_criteria = {}
        if self.metadata is None:
            self.metadata = {}


class FlowJSONParser:
    """
    Parser for conversation flow JSON definitions
    
    Supports:
    - Validation of flow structure
    - Step-by-step parsing
    - Assertion rule extraction
    - Flow merging and inheritance
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def parse_flow_file(self, file_path: str) -> FlowDefinition:
        """
        Parse flow from JSON file
        
        Args:
            file_path: Path to JSON file
            
        Returns:
            FlowDefinition object
            
        Raises:
            ValueError: If file format is invalid
            FileNotFoundError: If file doesn't exist
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Flow file not found: {file_path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            flow_data = json.load(f)
        
        return self.parse_flow_dict(flow_data)
    
    def parse_flow_dict(self, flow_data: Dict[str, Any]) -> FlowDefinition:
        """
        Parse flow from dictionary
        
        Args:
            flow_data: Dictionary containing flow definition
            
        Returns:
            FlowDefinition object
            
        Raises:
            ValueError: If structure is invalid
        """
        self._validate_flow_structure(flow_data)
        
        # Parse basic info
        flow_id = flow_data.get('flow_id', flow_data.get('id'))
        flow_name = flow_data.get('flow_name', flow_data.get('name', flow_id))
        description = flow_data.get('description', '')
        agent_id = flow_data.get('agent_id')
        language_code = flow_data.get('language_code', 'en')
        
        # Parse steps
        steps = []
        for step_data in flow_data.get('steps', []):
            step = self._parse_step(step_data)
            steps.append(step)
        
        # Parse global validation rules
        global_rules = []
        for rule_data in flow_data.get('global_validation_rules', []):
            rule = self._parse_validation_rule(rule_data)
            global_rules.append(rule)
        
        # Parse success criteria
        success_criteria = flow_data.get('success_criteria', {})
        
        # Parse metadata
        metadata = flow_data.get('metadata', {})
        
        flow = FlowDefinition(
            flow_id=flow_id,
            flow_name=flow_name,
            description=description,
            agent_id=agent_id,
            language_code=language_code,
            steps=steps,
            global_validation_rules=global_rules,
            success_criteria=success_criteria,
            metadata=metadata
        )
        
        self.logger.info(f"Parsed flow: {flow_name} with {len(steps)} steps")
        return flow
    
    def _validate_flow_structure(self, flow_data: Dict[str, Any]):
        """Validate basic flow structure"""
        required_fields = ['steps']
        for field in required_fields:
            if field not in flow_data:
                raise ValueError(f"Missing required field: {field}")
        
        if not isinstance(flow_data['steps'], list):
            raise ValueError("'steps' must be a list")
        
        if len(flow_data['steps']) == 0:
            raise ValueError("Flow must have at least one step")
    
    def _parse_step(self, step_data: Dict[str, Any]) -> FlowStep:
        """Parse a single step"""
        # Extract user input
        user_input_data = step_data.get('user_input', {})
        if isinstance(user_input_data, str):
            user_input = user_input_data
        elif isinstance(user_input_data, dict):
            user_input = user_input_data.get('content', user_input_data.get('text', ''))
        else:
            user_input = str(user_input_data)
        
        # Parse validation rules
        validation_rules = []
        
        # Add validation criteria as rules
        validation_criteria = step_data.get('validation_criteria', {})
        for field, criteria in validation_criteria.items():
            if isinstance(criteria, dict):
                rule = self._parse_validation_rule({
                    'field': field,
                    **criteria
                })
                validation_rules.append(rule)
            else:
                # Simple equality check
                rule = ValidationRule(
                    field=field,
                    operator=ValidationOperator.EQUALS,
                    expected_value=criteria,
                    assertion_level=AssertionLevel.CRITICAL
                )
                validation_rules.append(rule)
        
        # Parse explicit validation rules
        for rule_data in step_data.get('validation_rules', []):
            rule = self._parse_validation_rule(rule_data)
            validation_rules.append(rule)
        
        step = FlowStep(
            step_id=step_data.get('step_id', f"step_{len(validation_rules)}"),
            user_input=user_input,
            expected_intent=step_data.get('expected_intent'),
            expected_entities=step_data.get('expected_entities', {}),
            expected_response_contains=step_data.get('expected_response_contains', []),
            validation_rules=validation_rules,
            continue_on_failure=step_data.get('continue_on_failure', False),
            timeout_seconds=step_data.get('timeout_seconds', 30),
            metadata=step_data.get('metadata', {})
        )
        
        return step
    
    def _parse_validation_rule(self, rule_data: Dict[str, Any]) -> ValidationRule:
        """Parse a validation rule"""
        field = rule_data.get('field')
        if not field:
            raise ValueError("Validation rule must have 'field'")
        
        # Determine operator
        operator_str = rule_data.get('operator', 'equals')
        try:
            operator = ValidationOperator(operator_str)
        except ValueError:
            self.logger.warning(f"Unknown operator: {operator_str}, using EQUALS")
            operator = ValidationOperator.EQUALS
        
        # Get expected value
        expected_value = rule_data.get('expected_value', rule_data.get('value'))
        
        # Determine assertion level
        level_str = rule_data.get('assertion_level', rule_data.get('level', 'critical'))
        try:
            assertion_level = AssertionLevel(level_str)
        except ValueError:
            self.logger.warning(f"Unknown assertion level: {level_str}, using CRITICAL")
            assertion_level = AssertionLevel.CRITICAL
        
        custom_message = rule_data.get('message', rule_data.get('custom_message'))
        
        return ValidationRule(
            field=field,
            operator=operator,
            expected_value=expected_value,
            assertion_level=assertion_level,
            custom_message=custom_message
        )
    
    def merge_flows(self, base_flow: FlowDefinition, 
                   override_flow: FlowDefinition) -> FlowDefinition:
        """
        Merge two flows (for inheritance/templates)
        
        Args:
            base_flow: Base flow definition
            override_flow: Override flow (takes precedence)
            
        Returns:
            Merged FlowDefinition
        """
        merged_steps = base_flow.steps.copy()
        
        # Override or append steps
        for override_step in override_flow.steps:
            existing_idx = next(
                (i for i, s in enumerate(merged_steps) if s.step_id == override_step.step_id),
                None
            )
            if existing_idx is not None:
                merged_steps[existing_idx] = override_step
            else:
                merged_steps.append(override_step)
        
        # Merge global rules
        merged_rules = base_flow.global_validation_rules + override_flow.global_validation_rules
        
        # Merge success criteria
        merged_criteria = {**base_flow.success_criteria, **override_flow.success_criteria}
        
        # Merge metadata
        merged_metadata = {**base_flow.metadata, **override_flow.metadata}
        
        return FlowDefinition(
            flow_id=override_flow.flow_id,
            flow_name=override_flow.flow_name,
            description=override_flow.description or base_flow.description,
            agent_id=override_flow.agent_id or base_flow.agent_id,
            language_code=override_flow.language_code,
            steps=merged_steps,
            global_validation_rules=merged_rules,
            success_criteria=merged_criteria,
            metadata=merged_metadata
        )
    
    def validate_flow(self, flow: FlowDefinition) -> List[str]:
        """
        Validate flow definition for common issues
        
        Returns:
            List of warning/error messages (empty if valid)
        """
        issues = []
        
        # Check for duplicate step IDs
        step_ids = [step.step_id for step in flow.steps]
        if len(step_ids) != len(set(step_ids)):
            issues.append("Duplicate step IDs found")
        
        # Check for empty user inputs
        for step in flow.steps:
            if not step.user_input or not step.user_input.strip():
                issues.append(f"Step {step.step_id}: Empty user input")
        
        # Check if agent_id is set
        if not flow.agent_id:
            issues.append("No agent_id specified")
        
        return issues
    
    def to_simple_format(self, flow: FlowDefinition) -> Dict[str, Any]:
        """
        Convert FlowDefinition back to simple dict format
        Compatible with existing conversation engine
        
        Returns:
            Dictionary suitable for execute_conversation_flow()
        """
        return {
            'id': flow.flow_id,
            'name': flow.flow_name,
            'agent_id': flow.agent_id,
            'language_code': flow.language_code,
            'user_inputs': [step.user_input for step in flow.steps],
            'metadata': flow.metadata
        }


# Example usage and tests
if __name__ == "__main__":
    # Example flow JSON
    example_flow = {
        "flow_id": "customer_support_flow",
        "flow_name": "Customer Support Test",
        "description": "Test customer support conversation",
        "agent_id": "customer_service",
        "language_code": "en",
        "steps": [
            {
                "step_id": "greeting",
                "user_input": "Hello, I need help with my account",
                "expected_intent": "account_help",
                "expected_entities": {"topic": "account"},
                "validation_criteria": {
                    "intent_confidence": {"operator": "gte", "value": 0.8},
                    "response_time_ms": {"operator": "lt", "value": 2000}
                },
                "expected_response_contains": ["help", "account"]
            },
            {
                "step_id": "account_number",
                "user_input": "My account number is 12345678",
                "expected_intent": "provide_account_number",
                "expected_entities": {"account_number": "12345678"},
                "continue_on_failure": False
            }
        ],
        "global_validation_rules": [
            {
                "field": "response_time_ms",
                "operator": "lt",
                "expected_value": 3000,
                "assertion_level": "warning",
                "message": "Response time exceeded 3 seconds"
            }
        ],
        "success_criteria": {
            "completion_rate": 0.95,
            "average_response_time": 1500,
            "intent_accuracy": 0.90
        }
    }
    
    # Test parsing
    parser = FlowJSONParser()
    flow = parser.parse_flow_dict(example_flow)
    
    print(f"Parsed flow: {flow.flow_name}")
    print(f"Steps: {len(flow.steps)}")
    print(f"Global rules: {len(flow.global_validation_rules)}")
    
    # Validate
    issues = parser.validate_flow(flow)
    if issues:
        print(f"Issues found: {issues}")
    else:
        print("Flow is valid!")
    
    # Convert to simple format
    simple = parser.to_simple_format(flow)
    print(f"Simple format: {simple}")
