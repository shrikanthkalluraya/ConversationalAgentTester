"""
Comprehensive tests for JSON flow execution with assertions
"""

import asyncio
import sys
import os
import pytest
import logging
import json
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config.settings import Config
from dialogflow.cx_client import DialogflowCXClient
from audio_processor.gcp_tts import GCPTextToSpeech
from flow_executor.dialogflow_conversation_engine import DialogflowConversationEngine
from flow_parser.flow_json_parser import FlowJSONParser


@pytest.mark.asyncio
async def test_parse_flow_json():
    """Test parsing flow from JSON file"""
    print("\n🎯 Test: Parse Flow JSON")
    
    parser = FlowJSONParser()
    flow = parser.parse_flow_file('test_data/flows/customer_support_flow.json')
    
    assert flow.flow_id == "customer_support_basic"
    assert len(flow.steps) > 0
    assert len(flow.global_validation_rules) > 0
    
    print(f"   ✅ Parsed: {flow.flow_name}")
    print(f"   ✅ Steps: {len(flow.steps)}")
    print(f"   ✅ Global rules: {len(flow.global_validation_rules)}")


@pytest.mark.asyncio
async def test_flow_validation():
    """Test flow validation"""
    print("\n🎯 Test: Flow Validation")
    
    parser = FlowJSONParser()
    flow = parser.parse_flow_file('test_data/flows/customer_support_flow.json')
    
    issues = parser.validate_flow(flow)
    
    if issues:
        print(f"   ⚠️  Issues found: {issues}")
    else:
        print("   ✅ Flow is valid")
    
    assert isinstance(issues, list)


@pytest.mark.asyncio
async def test_execute_json_flow():
    """Test executing a JSON-defined flow"""
    print("\n🎯 Test: Execute JSON Flow")
    
    config = Config()
    if not config.has_gcp_credentials():
        pytest.skip("GCP not configured")
    
    # Initialize components
    tts = GCPTextToSpeech(config.GCP_CREDENTIALS_PATH, config.GCP_PROJECT_ID)
    df = DialogflowCXClient(
        config.GCP_CREDENTIALS_PATH, 
        config.GCP_PROJECT_ID, 
        config.DIALOGFLOW_LOCATION
    )
    engine = DialogflowConversationEngine(df, tts)
    
    # Execute flow from file
    result = await engine.execute_flow_from_file(
        'test_data/flows/customer_support_flow.json'
    )
    
    print(f"   ✅ Flow executed: {result['flow_name']}")
    print(f"   ✅ Steps executed: {len(result['steps'])}")
    print(f"   ✅ Success: {result['success']}")
    print(f"   ✅ Duration: {result['duration']:.2f}s")
    
    # Print validation summary
    report = result['validation_report']
    summary = report['summary']
    print(f"\n   📊 Validation Summary:")
    print(f"      Success rate: {summary['success_rate']:.1f}%")
    print(f"      Critical failures: {summary['critical_failures']}")
    print(f"      Errors: {summary['errors']}")
    print(f"      Warnings: {summary['warnings']}")
    
    if result['stopped_early']:
        print(f"   ⚠️  Stopped early: {result['stop_reason']}")
    
    assert 'validation_report' in result
    assert 'steps' in result


@pytest.mark.asyncio
async def test_assertion_stop_on_failure():
    """Test that execution stops on critical assertion failure"""
    print("\n🎯 Test: Stop on Critical Failure")
    
    config = Config()
    if not config.has_gcp_credentials():
        pytest.skip("GCP not configured")
    
    # Create flow with failing assertion
    failing_flow = {
        "flow_id": "test_failure",
        "flow_name": "Test Failure Handling",
        "agent_id": None,  # Will use default
        "steps": [
            {
                "step_id": "step1",
                "user_input": "Hello",
                "validation_rules": [
                    {
                        "field": "intent_confidence",
                        "operator": "gte",
                        "expected_value": 0.99,  # Intentionally high
                        "assertion_level": "critical"
                    }
                ]
            },
            {
                "step_id": "step2",
                "user_input": "This should not execute",
                "validation_rules": []
            }
        ]
    }
    
    tts = GCPTextToSpeech(config.GCP_CREDENTIALS_PATH, config.GCP_PROJECT_ID)
    df = DialogflowCXClient(
        config.GCP_CREDENTIALS_PATH,
        config.GCP_PROJECT_ID,
        config.DIALOGFLOW_LOCATION
    )
    engine = DialogflowConversationEngine(df, tts)
    
    parser = FlowJSONParser()
    flow = parser.parse_flow_dict(failing_flow)
    result = await engine.execute_flow(flow)
    
    print(f"   ✅ Stopped early: {result['stopped_early']}")
    print(f"   ✅ Steps executed: {len(result['steps'])}")
    print(f"   ✅ Stop reason: {result.get('stop_reason', 'N/A')}")
    
    # Should stop after first step
    assert result['stopped_early'] == True
    assert len(result['steps']) == 1


@pytest.mark.asyncio
async def test_continue_on_failure():
    """Test continue_on_failure flag"""
    print("\n🎯 Test: Continue on Failure")
    
    config = Config()
    if not config.has_gcp_credentials():
        pytest.skip("GCP not configured")
    
    # Create flow with continue_on_failure = true
    flow_data = {
        "flow_id": "test_continue",
        "flow_name": "Test Continue on Failure",
        "agent_id": None,
        "steps": [
            {
                "step_id": "step1",
                "user_input": "Hello",
                "validation_rules": [
                    {
                        "field": "intent_confidence",
                        "operator": "gte",
                        "expected_value": 0.99,
                        "assertion_level": "critical"
                    }
                ],
                "continue_on_failure": True  # Should continue despite failure
            },
            {
                "step_id": "step2",
                "user_input": "Thank you",
                "validation_rules": []
            }
        ]
    }
    
    tts = GCPTextToSpeech(config.GCP_CREDENTIALS_PATH, config.GCP_PROJECT_ID)
    df = DialogflowCXClient(
        config.GCP_CREDENTIALS_PATH,
        config.GCP_PROJECT_ID,
        config.DIALOGFLOW_LOCATION
    )
    engine = DialogflowConversationEngine(df, tts)
    
    parser = FlowJSONParser()
    flow = parser.parse_flow_dict(flow_data)
    result = await engine.execute_flow(flow)
    
    print(f"   ✅ Stopped early: {result['stopped_early']}")
    print(f"   ✅ Steps executed: {len(result['steps'])}")
    
    # Should execute both steps
    assert len(result['steps']) == 2


def print_detailed_report(result: dict):
    """Print detailed execution report"""
    print("\n" + "="*70)
    print(f"📋 FLOW EXECUTION REPORT: {result['flow_name']}")
    print("="*70)
    
    print(f"\n⏱️  Duration: {result['duration']:.2f}s")
    print(f"✅ Success: {result['success']}")
    
    if result['stopped_early']:
        print(f"⚠️  Stopped Early: {result['stop_reason']}")
    
    print(f"\n📊 Steps Executed: {len(result['steps'])}/{len(result.get('validation_results', []))}")
    
    # Print each step
    for idx, step in enumerate(result['steps']):
        print(f"\n  Step {idx + 1}: {step['step_id']}")
        print(f"    User: {step['user_input']}")
        print(f"    Intent: {step.get('intent', 'N/A')} ({step.get('intent_confidence', 0):.2f})")
        print(f"    Time: {step['execution_time_ms']:.0f}ms")
        
        # Print validation result
        if idx < len(result['validation_results']):
            val_result = result['validation_results'][idx]
            status = "✅" if val_result.passed else "❌"
            print(f"    Validation: {status}")
            if val_result.critical_failures > 0:
                print(f"      ❌ Critical: {val_result.critical_failures}")
            if val_result.errors > 0:
                print(f"      ⚠️  Errors: {val_result.errors}")
            if val_result.warnings > 0:
                print(f"      ⚡ Warnings: {val_result.warnings}")
    
    # Print validation summary
    report = result.get('validation_report', {})
    if report:
        summary = report['summary']
        print(f"\n📈 Validation Summary:")
        print(f"    Success Rate: {summary['success_rate']:.1f}%")
        print(f"    Passed Steps: {summary['passed_steps']}/{summary['total_steps']}")
        print(f"    Assertions: {summary['passed_assertions']}/{summary['total_assertions']}")
        print(f"    Critical Failures: {summary['critical_failures']}")
        print(f"    Errors: {summary['errors']}")
        print(f"    Warnings: {summary['warnings']}")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    print("🚀 Running Phase 4 JSON Flow Tests\n")
    
    asyncio.run(test_parse_flow_json())
    asyncio.run(test_flow_validation())
    asyncio.run(test_execute_json_flow())
    asyncio.run(test_assertion_stop_on_failure())
    asyncio.run(test_continue_on_failure())
    
    print("\n✅ All tests completed!")
