"""
Dialogflow Conversation Engine - UPDATED with JSON flow support
Now supports step-by-step assertion validation
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..dialogflow.cx_client import DialogflowCXClient
from ..dialogflow.session_manager import SessionManager
from ..audio_processor.gcp_tts import GCPTextToSpeech
from ..flow_parser.flow_json_parser import FlowDefinition, FlowStep, FlowJSONParser
from ..flow_parser.assertion_validator import AssertionValidator, StepValidationResult
from ..config.settings import Config


class DialogflowConversationEngine:
    """
    Enhanced conversation engine with JSON flow support and assertions
    """
    
    def __init__(self, dialogflow_client: DialogflowCXClient, 
                 tts_processor: Optional[GCPTextToSpeech]):
        self.dialogflow_client = dialogflow_client
        self.tts_processor = tts_processor
        self.session_manager = SessionManager(Config.SESSION_TIMEOUT)
        self.flow_parser = FlowJSONParser()
        self.assertion_validator = AssertionValidator()
        self.logger = logging.getLogger(__name__)
        self.use_tts = tts_processor is not None and Config.ENABLE_TTS
    
    async def execute_flow_from_file(self, flow_file_path: str) -> Dict[str, Any]:
        """
        Execute flow from JSON file
        
        Args:
            flow_file_path: Path to flow JSON file
            
        Returns:
            Complete flow execution result
        """
        flow = self.flow_parser.parse_flow_file(flow_file_path)
        return await self.execute_flow(flow)
    
    async def execute_flow(self, flow: FlowDefinition) -> Dict[str, Any]:
        """
        Execute complete flow with step-by-step validation
        
        Args:
            flow: FlowDefinition object
            
        Returns:
            Complete execution result with validation
        """
        flow_id = flow.flow_id
        agent_config_id = flow.agent_id
        
        # Get agent configuration
        agent_config = Config.get_agent_config(agent_config_id)
        if not agent_config:
            raise ValueError(f"Agent not found: {agent_config_id}")
        
        agent_id = agent_config['agent_id']
        language = flow.language_code
        
        # Create session
        session_id = self.session_manager.create_session(
            agent_id, 
            {'flow_id': flow_id, 'flow_name': flow.flow_name}
        )
        
        flow_result = {
            'flow_id': flow_id,
            'flow_name': flow.flow_name,
            'agent_id': agent_id,
            'session_id': session_id,
            'start_time': datetime.now(),
            'steps': [],
            'validation_results': [],
            'success': True,
            'stopped_early': False,
            'stop_reason': None,
            'audio_files': []
        }
        
        try:
            # Execute steps one by one with validation
            for step_idx, step in enumerate(flow.steps):
                self.logger.info(f"Executing step {step_idx + 1}/{len(flow.steps)}: {step.step_id}")
                
                step_start = datetime.now()
                
                # Execute Dialogflow interaction
                dialogflow_result = await self.dialogflow_client.detect_intent(
                    agent_id, session_id, step.user_input, language
                )
                
                step_end = datetime.now()
                execution_time_ms = (step_end - step_start).total_seconds() * 1000
                
                # Build step result for validation
                step_result = {
                    'step_id': step.step_id,
                    'step_number': step_idx + 1,
                    'user_input': step.user_input,
                    'intent': dialogflow_result.get('intent'),
                    'intent_confidence': dialogflow_result.get('intent_confidence'),
                    'response_messages': dialogflow_result.get('response_messages', []),
                    'parameters': dialogflow_result.get('parameters', {}),
                    'current_page': dialogflow_result.get('current_page'),
                    'execution_time_ms': execution_time_ms,
                    'timestamp': step_end.isoformat()
                }
                
                # Process audio if TTS enabled
                audio_files = await self._generate_step_audio(
                    dialogflow_result, step_idx + 1
                )
                step_result['audio_files'] = audio_files
                flow_result['audio_files'].extend(audio_files)
                
                flow_result['steps'].append(step_result)
                
                # VALIDATE STEP with assertions
                validation_result = self.assertion_validator.validate_step(
                    step_result,
                    step.validation_rules + flow.global_validation_rules,
                    stop_on_critical=not step.continue_on_failure
                )
                
                flow_result['validation_results'].append(validation_result)
                
                # Update session
                self.session_manager.update_session(session_id, step_result)
                
                # CHECK IF WE SHOULD STOP
                if validation_result.should_stop:
                    flow_result['success'] = False
                    flow_result['stopped_early'] = True
                    flow_result['stop_reason'] = f"Critical assertion failed in step: {step.step_id}"
                    self.logger.error(
                        f"❌ Stopping execution at step {step_idx + 1} due to critical failure"
                    )
                    break
                
                # Log step result
                if validation_result.passed:
                    self.logger.info(f"✅ Step {step.step_id} passed")
                else:
                    self.logger.warning(
                        f"⚠️ Step {step.step_id} had {validation_result.errors} errors, "
                        f"{validation_result.warnings} warnings"
                    )
        
        except Exception as e:
            self.logger.error(f"Flow execution error: {e}")
            flow_result['success'] = False
            flow_result['error'] = str(e)
        
        finally:
            flow_result['end_time'] = datetime.now()
            flow_result['duration'] = (
                flow_result['end_time'] - flow_result['start_time']
            ).total_seconds()
            self.session_manager.end_session(session_id)
        
        # Generate validation report
        flow_result['validation_report'] = self.assertion_validator.generate_report(
            flow_result['validation_results']
        )
        
        # Check overall success
        if flow_result['success']:
            failed_steps = flow_result['validation_report']['summary']['failed_steps']
            flow_result['success'] = failed_steps == 0
        
        return flow_result
    
    async def _generate_step_audio(self, dialogflow_result: Dict[str, Any], 
                                   step_number: int) -> List[str]:
        """Generate audio for step responses"""
        if not self.use_tts:
            return []
        
        audio_files = []
        for idx, message in enumerate(dialogflow_result.get('response_messages', [])):
            if message['type'] == 'text':
                try:
                    audio = await self.tts_processor.text_to_speech(message['text'])
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"data/recordings/step{step_number}_msg{idx}_{timestamp}.wav"
                    
                    Path(filename).parent.mkdir(parents=True, exist_ok=True)
                    with open(filename, 'wb') as f:
                        f.write(audio)
                    
                    audio_files.append(filename)
                    self.logger.info(f"Audio saved: {filename}")
                except Exception as e:
                    self.logger.error(f"Audio generation failed: {e}")
        
        return audio_files
    
    async def execute_conversation_flow(self, flow_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Legacy method - converts simple config to FlowDefinition
        Maintains backward compatibility
        """
        # Convert to FlowDefinition
        flow_data = {
            'flow_id': flow_config.get('id', 'legacy_flow'),
            'flow_name': flow_config.get('name', 'Legacy Flow'),
            'description': flow_config.get('description', ''),
            'agent_id': flow_config.get('agent_id'),
            'language_code': flow_config.get('language_code', 'en'),
            'steps': [
                {
                    'step_id': f"step_{idx}",
                    'user_input': user_input
                }
                for idx, user_input in enumerate(flow_config.get('user_inputs', []))
            ]
        }
        
        flow = self.flow_parser.parse_flow_dict(flow_data)
        return await self.execute_flow(flow)
    
    async def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
        stats = {'active_sessions': len(self.session_manager.get_active_sessions())}
        stats.update(self.dialogflow_client.get_usage_stats())
        if self.use_tts:
            stats.update(self.tts_processor.get_usage_stats())
        return stats
