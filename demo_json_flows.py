"""
Demo script showing JSON flow execution with assertions
"""

import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config.settings import Config
from dialogflow.cx_client import DialogflowCXClient
from audio_processor.gcp_tts import GCPTextToSpeech
from flow_executor.dialogflow_conversation_engine import DialogflowConversationEngine


def print_step_result(step, validation_result):
    """Print formatted step result"""
    status = "✅" if validation_result.passed else "❌"
    print(f"\n{status} Step: {step['step_id']}")
    print(f"  👤 User: {step['user_input']}")
    print(f"  🎯 Intent: {step['intent']} ({step['intent_confidence']:.2f})")
    print(f"  ⏱️  Time: {step['execution_time_ms']:.0f}ms")
    
    if not validation_result.passed:
        print(f"  ❌ Critical: {validation_result.critical_failures}")
        print(f"  ⚠️  Errors: {validation_result.errors}")
        print(f"  ⚡ Warnings: {validation_result.warnings}")
        
        # Print failed assertions
        for assertion in validation_result.assertions:
            if not assertion.passed:
                level_icon = {
                    'critical': '❌',
                    'error': '⚠️',
                    'warning': '⚡'
                }.get(assertion.assertion_level.value, '•')
                print(f"    {level_icon} {assertion.message}")


async def demo_customer_support_flow():
    """Demo customer support flow with validation"""
    print("\n🤖 Demo: Customer Support Flow with Assertions")
    print("="*70)
    
    config = Config()
    if not config.has_gcp_credentials():
        print("❌ GCP not configured")
        return
    
    # Initialize
    tts = GCPTextToSpeech(config.GCP_CREDENTIALS_PATH, config.GCP_PROJECT_ID)
    df = DialogflowCXClient(
        config.GCP_CREDENTIALS_PATH,
        config.GCP_PROJECT_ID,
        config.DIALOGFLOW_LOCATION
    )
    engine = DialogflowConversationEngine(df, tts)
    
    # Execute flow from JSON file
    result = await engine.execute_flow_from_file(
        'test_data/flows/customer_support_flow.json'
    )
    
    print(f"\n📋 Flow: {result['flow_name']}")
    print(f"🆔 Session: {result['session_id']}")
    print(f"⏱️  Duration: {result['duration']:.2f}s")
    
    # Print each step with validation
    for idx, step in enumerate(result['steps']):
        validation_result = result['validation_results'][idx]
        print_step_result(step, validation_result)
        
        # Show agent responses
        for msg_idx, msg in enumerate(step['response_messages']):
            if msg['type'] == 'text':
                print(f"  🤖 Agent: {msg['text']}")
    
    # Print final summary
    print("\n" + "="*70)
    report = result['validation_report']
    summary = report['summary']
    
    final_status = "✅ PASSED" if result['success'] else "❌ FAILED"
    print(f"\n{final_status}")
    print(f"  Success Rate: {summary['success_rate']:.1f}%")
    print(f"  Steps: {summary['passed_steps']}/{summary['total_steps']}")
    print(f"  Assertions: {summary['passed_assertions']}/{summary['total_assertions']}")
    
    if summary['critical_failures'] > 0:
        print(f"  ❌ Critical Failures: {summary['critical_failures']}")
    if summary['errors'] > 0:
        print(f"  ⚠️  Errors: {summary['errors']}")
    if summary['warnings'] > 0:
        print(f"  ⚡ Warnings: {summary['warnings']}")
    
    if result['stopped_early']:
        print(f"\n⚠️  Execution stopped early: {result['stop_reason']}")
    
    print("\n" + "="*70)


async def demo_all_flows():
    """Demo all available flows"""
    print("\n🎭 Demo: All Flows\n")
    
    config = Config()
    if not config.has_gcp_credentials():
        print("❌ GCP not configured")
        return
    
    tts = GCPTextToSpeech(config.GCP_CREDENTIALS_PATH, config.GCP_PROJECT_ID)
    df = DialogflowCXClient(
        config.GCP_CREDENTIALS_PATH,
        config.GCP_PROJECT_ID,
        config.DIALOGFLOW_LOCATION
    )
    engine = DialogflowConversationEngine(df, tts)
    
    # Find all flow files
    from pathlib import Path
    flow_dir = Path('test_data/flows')
    flow_files = list(flow_dir.glob('*_flow.json'))
    
    results = []
    for flow_file in flow_files:
        print(f"\n▶️  Executing: {flow_file.name}")
        try:
            result = await engine.execute_flow_from_file(str(flow_file))
            results.append((flow_file.name, result))
            
            status = "✅" if result['success'] else "❌"
            print(f"   {status} {result['flow_name']}")
            print(f"   Steps: {len(result['steps'])}, Duration: {result['duration']:.2f}s")
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    # Summary
    print("\n" + "="*70)
    print("📊 Summary")
    print("="*70)
    for name, result in results:
        status = "✅" if result['success'] else "❌"
        report = result['validation_report']['summary']
        print(f"{status} {name}")
        print(f"   Success: {report['success_rate']:.1f}%, Steps: {report['passed_steps']}/{report['total_steps']}")


if __name__ == "__main__":
    print("🚀 JSON Flow Demo\n")
    asyncio.run(demo_customer_support_flow())
    # asyncio.run(demo_all_flows())  # Uncomment to run all flows
