import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config.settings import Config
from dialogflow.cx_client import DialogflowCXClient
from audio_processor.gcp_tts import GCPTextToSpeech
from flow_executor.dialogflow_conversation_engine import DialogflowConversationEngine

async def test_new_operators():
    """Test the new CONTAINS_ALL and CONTAINS_ANY operators"""
    print("🧪 Testing new operators...")
    
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
    
    # Run test
    result = await engine.execute_flow_from_file(
        'test_data/flows/test_new_operators.json'
    )
    
    # Print results
    print(f"\n{'='*70}")
    print(f"Test Results: {result['flow_name']}")
    print(f"{'='*70}\n")
    
    for idx, step in enumerate(result['steps']):
        validation = result['validation_results'][idx]
        status = "✅" if validation.passed else "❌"
        
        print(f"{status} Step: {step['step_id']}")
        print(f"   User: {step['user_input']}")
        print(f"   Intent: {step['intent']} ({step['intent_confidence']:.2f})")
        
        # Show assertion results
        for assertion in validation.assertions:
            icon = "✅" if assertion.passed else "❌"
            print(f"   {icon} {assertion.message}")
        print()
    
    # Summary
    report = result['validation_report']
    print(f"{'='*70}")
    print(f"Summary:")
    print(f"  Success rate: {report['summary']['success_rate']:.1f}%")
    print(f"  Warnings: {report['summary']['warnings']}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    asyncio.run(test_new_operators())
