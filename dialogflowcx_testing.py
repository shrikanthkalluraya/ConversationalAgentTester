from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncGenerator
import logging
import os
import struct
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Callable, Any

from google.api_core import retry as retries
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPIError, ServiceUnavailable
from google.cloud import dialogflowcx_v3
from google.protobuf.json_format import MessageToDict

from termcolor import colored

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CHUNK_SECONDS = 0.1
DEFAULT_LANGUAGE_CODE = "en-US"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_DIALOGFLOW_TIMEOUT = 60.0
STREAMING_DELAY_RATIO = 1.0  # Real-time streaming (no artificial delay)


def get_current_time() -> int:
    """Return Current Time in MS."""
    return int(round(time.time() * 1000))


class WavFileReader:
    """Reads WAV files and streams them as audio chunks."""

    def __init__(self, wav_file_path: str, chunk_size: int, streaming_delay_ratio: float = 1.0) -> None:
        self.wav_file_path = wav_file_path
        self.chunk_size = chunk_size
        self.streaming_delay_ratio = streaming_delay_ratio
        self._buff = asyncio.Queue(maxsize=10)  # Limit buffer size
        self.closed = False
        self.start_time = None
        self._read_started = asyncio.Event()

    def __enter__(self) -> "WavFileReader":
        """Opens the WAV file."""
        self.closed = False
        return self

    def __exit__(self, *args: any) -> None:
        """Closes resources."""
        self.closed = True
        self._buff.put_nowait(None)

    async def read_wav_file(self) -> None:
        """Reads WAV file and pushes chunks to buffer with precise real-time timing."""
        try:
            with wave.open(self.wav_file_path, "rb") as wf:
                # Validate WAV format
                if wf.getsampwidth() != 2:
                    raise ValueError(f"WAV file must be 16-bit PCM, got {wf.getsampwidth() * 8}-bit")
                if wf.getnchannels() != 1:
                    raise ValueError(f"WAV file must be mono, got {wf.getnchannels()} channels")
                
                logger.info(f"Reading WAV file: {self.wav_file_path}")
                logger.info(f"Sample rate: {wf.getframerate()} Hz, Duration: {wf.getnframes() / wf.getframerate():.2f}s")
                
                self.start_time = get_current_time()
                self._read_started.set()  # Signal that reading has started
                
                # Calculate precise timing for streaming
                chunk_duration = CHUNK_SECONDS * self.streaming_delay_ratio
                start_time = asyncio.get_event_loop().time()
                chunk_index = 0
                
                while not self.closed:
                    # Calculate when this chunk should be sent
                    target_time = start_time + (chunk_index * chunk_duration)
                    current_time = asyncio.get_event_loop().time()
                    
                    # Wait until it's time to send this chunk
                    sleep_time = target_time - current_time
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                    
                    # Read and send the chunk
                    data = wf.readframes(self.chunk_size)
                    if not data:
                        break
                    
                    try:
                        await asyncio.wait_for(self._buff.put(data), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.error("Buffer full, dropping audio chunk")
                        break
                    
                    chunk_index += 1
                
                # Signal end of file
                await self._buff.put(None)
                logger.info(f"Finished reading WAV file: {self.wav_file_path}")
                
        except FileNotFoundError:
            logger.error(f"WAV file not found: {self.wav_file_path}")
            self._read_started.set()
            await self._buff.put(None)
        except Exception as e:
            logger.error(f"Error reading WAV file: {e}")
            self._read_started.set()
            await self._buff.put(None)

    async def generator(self) -> AsyncGenerator[bytes, None]:
        """Stream audio chunks from WAV file."""
        # Wait for reading to start
        await self._read_started.wait()
        
        while not self.closed:
            try:
                chunk = await asyncio.wait_for(self._buff.get(), timeout=1)

                if chunk is None:
                    logger.debug("[generator] Received None chunk, ending stream")
                    return

                # Batch multiple chunks if available for efficiency
                data = [chunk]
                while True:
                    try:
                        chunk = self._buff.get_nowait()
                        if chunk is None:
                            logger.debug("[generator] Received None chunk (nowait), ending stream")
                            return
                        data.append(chunk)
                    except asyncio.QueueEmpty:
                        break

                combined_data = b"".join(data)
                yield combined_data

            except asyncio.TimeoutError:
                # Continue waiting for more audio
                continue


class DialogflowResponse:
    """Container for Dialogflow response data."""
    
    def __init__(self):
        self.transcript: str = ""
        self.is_final: bool = False
        self.intent: str = ""
        self.response_text: str = ""
        self.output_audio: bytes = b""
        self.end_interaction: bool = False
        self.parameters: dict = {}


class DialogflowCXStreaming:
    """Manages the interaction with the Dialogflow CX Streaming API."""

    def __init__(
        self,
        agent_name: str,
        language_code: str,
        single_utterance: bool,
        model: str | None,
        voice: str | None,
        sample_rate: int,
        dialogflow_timeout: float,
        debug: bool,
    ) -> None:
        """Initializes the Dialogflow CX Streaming API client."""
        try:
            _, project, _, location, _, agent_id = agent_name.split("/")
        except ValueError:
            raise ValueError(
                "Invalid agent name format. Expected format: projects/<project>/locations/<location>/agents/<agent_id>"
            )
        if location != "global":
            client_options = ClientOptions(
                api_endpoint=f"{location}-dialogflow.googleapis.com",
                quota_project_id=project,
            )
        else:
            client_options = ClientOptions(quota_project_id=project)

        self.client = dialogflowcx_v3.SessionsAsyncClient(client_options=client_options)
        self.agent_name = agent_name
        self.language_code = language_code
        self.single_utterance = single_utterance
        self.model = model
        self.session_id = str(uuid.uuid4())
        self.dialogflow_timeout = dialogflow_timeout
        self.debug = debug
        self.sample_rate = sample_rate
        self.voice = voice

        if self.debug:
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug logging enabled")

    async def generate_streaming_detect_intent_requests(
        self, audio_queue: asyncio.Queue
    ) -> AsyncGenerator[dialogflowcx_v3.StreamingDetectIntentRequest, None]:
        """Generates the requests for the streaming API."""
        audio_config = dialogflowcx_v3.InputAudioConfig(
            audio_encoding=dialogflowcx_v3.AudioEncoding.AUDIO_ENCODING_LINEAR_16,
            sample_rate_hertz=self.sample_rate,
            model=self.model,
            single_utterance=self.single_utterance,
        )
        query_input = dialogflowcx_v3.QueryInput(
            language_code=self.language_code,
            audio=dialogflowcx_v3.AudioInput(config=audio_config),
        )
        output_audio_config = dialogflowcx_v3.OutputAudioConfig(
            audio_encoding=dialogflowcx_v3.OutputAudioEncoding.OUTPUT_AUDIO_ENCODING_LINEAR_16,
            sample_rate_hertz=self.sample_rate,
            synthesize_speech_config=(
                dialogflowcx_v3.SynthesizeSpeechConfig(
                    voice=dialogflowcx_v3.VoiceSelectionParams(name=self.voice)
                )
                if self.voice
                else None
            ),
        )

        # First request
        request = dialogflowcx_v3.StreamingDetectIntentRequest(
            session=f"{self.agent_name}/sessions/{self.session_id}",
            query_input=query_input,
            enable_partial_response=True,
            output_audio_config=output_audio_config,
        )
        if self.debug:
            logger.debug(f"Sending initial request: {request}")
        yield request

        # Subsequent requests contain audio only
        while True:
            try:
                chunk = await audio_queue.get()
                if chunk is None:
                    logger.debug("[generate_streaming_detect_intent_requests] End of utterance")
                    break

                request = dialogflowcx_v3.StreamingDetectIntentRequest(
                    query_input=dialogflowcx_v3.QueryInput(
                        audio=dialogflowcx_v3.AudioInput(audio=chunk)
                    )
                )
                yield request

            except asyncio.CancelledError:
                logger.debug("[generate_streaming_detect_intent_requests] Cancelled")
                break

    async def streaming_detect_intent(
        self,
        audio_queue: asyncio.Queue,
    ) -> AsyncGenerator[dialogflowcx_v3.StreamingDetectIntentResponse, None]:
        """Transcribes the audio into text and yields each response."""
        requests_generator = self.generate_streaming_detect_intent_requests(audio_queue)

        retry_policy = retries.AsyncRetry(
            predicate=retries.if_exception_type(ServiceUnavailable),
            initial=0.5,
            maximum=60.0,
            multiplier=2.0,
            timeout=300.0,
            on_error=lambda e: logger.warning(f"Retrying due to error: {e}"),
        )

        async def streaming_request_with_retry():
            async def api_call():
                return await self.client.streaming_detect_intent(requests=requests_generator)
            response_stream = await retry_policy(api_call)()
            return response_stream

        try:
            responses = await streaming_request_with_retry()
            response_iterator = responses.__aiter__()
            
            while True:
                try:
                    response = await asyncio.wait_for(
                        response_iterator.__anext__(), timeout=self.dialogflow_timeout
                    )
                    if self.debug and response:
                        response_copy = MessageToDict(response._pb)
                        if response_copy.get("detectIntentResponse"):
                            response_copy["detectIntentResponse"]["outputAudio"] = "REMOVED"
                        logger.debug(f"Received response: {response_copy}")
                    yield response
                except StopAsyncIteration:
                    logger.debug("End of response stream")
                    break
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for response from Dialogflow.")
                    continue
                except GoogleAPIError as e:
                    logger.error(f"Error: {e}")
                    if e.code == 500:
                        logger.warning("Encountered a 500 error during iteration.")

        except GoogleAPIError as e:
            logger.error(f"Error: {e}")


async def push_to_audio_queue(
    audio_generator: AsyncGenerator, audio_queue: asyncio.Queue
) -> None:
    """Pushes audio chunks from a generator to an asyncio queue."""
    try:
        async for chunk in audio_generator:
            await audio_queue.put(chunk)
    except Exception as e:
        logger.error(f"Error in push_to_audio_queue: {e}")


async def process_wav_file(
    dialogflow_streaming: DialogflowCXStreaming,
    wav_file_path: str,
    chunk_size: int,
    streaming_delay_ratio: float = 1.0,
) -> DialogflowResponse:
    """Process a single WAV file and return the response."""
    
    result = DialogflowResponse()
    audio_queue = asyncio.Queue()
    
    with WavFileReader(wav_file_path, chunk_size, streaming_delay_ratio) as wav_reader:
        # Start reading WAV file in background
        read_task = asyncio.create_task(wav_reader.read_wav_file())
        
        # Wait for reading to start
        await wav_reader._read_started.wait()
        
        # Start streaming to API immediately
        push_task = asyncio.create_task(
            push_to_audio_queue(wav_reader.generator(), audio_queue)
        )
        
        try:
            responses = dialogflow_streaming.streaming_detect_intent(audio_queue)
            response_iterator = responses.__aiter__()
            
            while True:
                try:
                    response = await asyncio.wait_for(
                        response_iterator.__anext__(),
                        timeout=dialogflow_streaming.dialogflow_timeout
                    )
                    
                    # Process recognition results
                    if response and response.recognition_result:
                        transcript = response.recognition_result.transcript
                        if transcript:
                            if response.recognition_result.is_final:
                                result.transcript = transcript
                                result.is_final = True
                                logger.info(f"Final transcript: {transcript}")
                            else:
                                print(colored(transcript, "yellow"), end="\r")
                    
                    # Process detect intent response
                    if response and response.detect_intent_response:
                        detect_response = response.detect_intent_response
                        
                        if detect_response.output_audio:
                            result.output_audio += detect_response.output_audio
                        
                        if detect_response.query_result:
                            query_result = detect_response.query_result
                            
                            if query_result.intent and query_result.intent.display_name:
                                result.intent = query_result.intent.display_name
                                logger.info(f"Detected intent: {result.intent}")
                            
                            if query_result.parameters:
                                result.parameters = MessageToDict(query_result.parameters)
                            
                            if query_result.response_messages:
                                for message in query_result.response_messages:
                                    if message.text:
                                        result.response_text = message.text.text[0]
                                        logger.info(f"Dialogflow output: {result.response_text}")
                                    if message._pb.HasField("end_interaction"):
                                        result.end_interaction = True
                                        logger.info("End interaction detected.")
                
                except StopAsyncIteration:
                    logger.debug("End of response stream")
                    break
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for response")
                    continue
        
        finally:
            # Cancel tasks
            wav_reader.closed = True
            for task in [read_task, push_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
    
    return result


class TestFlowManager:
    """Manages test flows with conditional logic based on responses."""
    
    def __init__(
        self,
        dialogflow_streaming: DialogflowCXStreaming,
        chunk_size: int,
        streaming_delay_ratio: float = 1.0,
    ):
        self.dialogflow_streaming = dialogflow_streaming
        self.chunk_size = chunk_size
        self.streaming_delay_ratio = streaming_delay_ratio
        self.responses: list[DialogflowResponse] = []
    
    async def send_audio(self, wav_file_path: str) -> DialogflowResponse:
        """Send a WAV file and wait for response."""
        logger.info(f"\n{'='*60}")
        logger.info(f"Sending audio: {wav_file_path}")
        logger.info(f"{'='*60}")
        
        response = await process_wav_file(
            self.dialogflow_streaming,
            wav_file_path,
            self.chunk_size,
            self.streaming_delay_ratio
        )
        
        self.responses.append(response)
        return response
    
    async def run_linear_flow(self, wav_files: list[str]) -> list[DialogflowResponse]:
        """Run a simple linear flow - send each file in sequence."""
        for wav_file in wav_files:
            response = await self.send_audio(wav_file)
            if response.end_interaction:
                logger.info("Flow ended by Dialogflow agent")
                break
            await asyncio.sleep(0.5)  # Brief pause between interactions
        
        return self.responses
    
    async def run_conditional_flow(
        self,
        flow_definition: list[dict[str, Any]]
    ) -> list[DialogflowResponse]:
        """
        Run a conditional flow based on response conditions.
        
        flow_definition format:
        [
            {
                "audio": "greeting.wav",
                "next": {
                    "intent_equals": {
                        "welcome": "order.wav",
                        "help": "help_response.wav"
                    },
                    "default": "fallback.wav"
                }
            },
            ...
        ]
        """
        current_step = 0
        
        while current_step < len(flow_definition):
            step = flow_definition[current_step]
            audio_file = step.get("audio")
            
            if not audio_file:
                logger.error(f"No audio file specified in step {current_step}")
                break
            
            response = await self.send_audio(audio_file)
            
            if response.end_interaction:
                logger.info("Flow ended by Dialogflow agent")
                break
            
            # Determine next step based on conditions
            next_config = step.get("next")
            if not next_config:
                current_step += 1
                continue
            
            # Check intent-based routing
            if "intent_equals" in next_config:
                intent_routing = next_config["intent_equals"]
                next_audio = intent_routing.get(response.intent)
                
                if next_audio:
                    # Find the step with this audio file
                    for idx, s in enumerate(flow_definition):
                        if s.get("audio") == next_audio:
                            current_step = idx
                            break
                    else:
                        logger.warning(f"Could not find step with audio: {next_audio}")
                        current_step += 1
                elif "default" in next_config:
                    next_audio = next_config["default"]
                    for idx, s in enumerate(flow_definition):
                        if s.get("audio") == next_audio:
                            current_step = idx
                            break
                    else:
                        current_step += 1
                else:
                    current_step += 1
            else:
                current_step += 1
            
            await asyncio.sleep(0.5)
        
        return self.responses
    
    def print_summary(self) -> None:
        """Print a summary of all interactions."""
        logger.info(f"\n{'='*60}")
        logger.info("TEST FLOW SUMMARY")
        logger.info(f"{'='*60}")
        
        for idx, response in enumerate(self.responses, 1):
            logger.info(f"\nInteraction {idx}:")
            logger.info(f"  Transcript: {response.transcript}")
            logger.info(f"  Intent: {response.intent}")
            logger.info(f"  Response: {response.response_text}")
            logger.info(f"  Has Audio: {len(response.output_audio) > 0}")
            if response.parameters:
                logger.info(f"  Parameters: {response.parameters}")


async def main(
    agent_name: str,
    wav_files: list[str],
    flow_config: str | None = None,
    language_code: str = DEFAULT_LANGUAGE_CODE,
    single_utterance: bool = False,
    model: str | None = None,
    voice: str | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    dialogflow_timeout: float = DEFAULT_DIALOGFLOW_TIMEOUT,
    streaming_delay_ratio: float = STREAMING_DELAY_RATIO,
    debug: bool = False,
) -> None:
    """Main function to run WAV file testing."""
    
    chunk_size = int(sample_rate * CHUNK_SECONDS)
    
    dialogflow_streaming = DialogflowCXStreaming(
        agent_name,
        language_code,
        single_utterance,
        model,
        voice,
        sample_rate,
        dialogflow_timeout,
        debug,
    )
    
    flow_manager = TestFlowManager(dialogflow_streaming, chunk_size, streaming_delay_ratio)
    
    logger.info(f"Starting test flow at {get_current_time() / 1000}")
    logger.info(f"Session ID: {dialogflow_streaming.session_id}")
    
    try:
        if flow_config:
            # Load conditional flow from config file
            import json
            with open(flow_config, 'r') as f:
                flow_definition = json.load(f)
            await flow_manager.run_conditional_flow(flow_definition)
        else:
            # Run simple linear flow
            await flow_manager.run_linear_flow(wav_files)
        
        flow_manager.print_summary()
        
    except Exception as e:
        logger.error(f"Error during test flow: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DialogflowCX Testing Framework with WAV Files",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("agent_name", help="Agent Name")
    parser.add_argument(
        "wav_files",
        nargs="+",
        help="WAV files to send (in order for linear flow)"
    )
    parser.add_argument(
        "--flow_config",
        type=str,
        default=None,
        help="JSON file with conditional flow definition"
    )
    parser.add_argument(
        "--language_code",
        type=str,
        default=DEFAULT_LANGUAGE_CODE,
        help="Language code (default: en-US)",
    )
    parser.add_argument(
        "--single_utterance",
        action="store_true",
        help="Enable single utterance mode",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Speech recognition model",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="Voice for output audio",
    )
    parser.add_argument(
        "--sample_rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help="Sample rate in Hz (default: 16000)",
    )
    parser.add_argument(
        "--dialogflow_timeout",
        type=float,
        default=DEFAULT_DIALOGFLOW_TIMEOUT,
        help="Dialogflow API timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--streaming_delay_ratio",
        type=float,
        default=STREAMING_DELAY_RATIO,
        help="Audio streaming speed ratio (default: 1.0 = real-time, <1.0 = faster, >1.0 = slower)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    asyncio.run(
        main(
            args.agent_name,
            args.wav_files,
            args.flow_config,
            args.language_code,
            args.single_utterance,
            args.model,
            args.voice,
            args.sample_rate,
            args.dialogflow_timeout,
            args.streaming_delay_ratio,
            args.debug,
        )
    )
