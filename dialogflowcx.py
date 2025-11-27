class AudioIO:
    """Audio Input / Output"""

    def __init__(
        self,
        rate: int,
        chunk_size: int,
        audio_file_path: str | None = None,  # New parameter
    ) -> None:
        self._rate = rate
        self.chunk_size = chunk_size
        self._buff = asyncio.Queue()
        self.closed = False
        self.start_time = None
        self.audio_input = []
        self._audio_interface = pyaudio.PyAudio()
        self._input_audio_stream = None
        self._output_audio_stream = None
        self.audio_file_path = audio_file_path
        self._file_feed_task = None

        # Get default output device info
        try:
            output_device_info = self._audio_interface.get_default_output_device_info()
            self.output_device_name = output_device_info["name"]
            logger.info(f"Using output device: {self.output_device_name}")
        except IOError:
            logger.error("Could not get default output device info. Exiting.")
            sys.exit(1)

        # setup output audio stream
        try:
            self._output_audio_stream = self._audio_interface.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._rate,
                output=True,
                frames_per_buffer=self.chunk_size,
            )
            self._output_audio_stream.stop_stream()
        except OSError as e:
            logger.error(f"Could not open output stream: {e}. Exiting.")
            sys.exit(1)

        # If using file input, log that
        if self.audio_file_path:
            logger.info(f"Using audio file as input: {self.audio_file_path}")
        else:
            # Setup microphone input (original code)
            try:
                input_device_info = self._audio_interface.get_default_input_device_info()
                self.input_device_name = input_device_info["name"]
                logger.info(f"Using input device: {self.input_device_name}")
            except IOError:
                logger.error("Could not get default input device info. Exiting.")
                sys.exit(1)

            try:
                self._input_audio_stream = self._audio_interface.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self._rate,
                    input=True,
                    frames_per_buffer=self.chunk_size,
                    stream_callback=self._fill_buffer,
                )
            except OSError as e:
                logger.error(f"Could not open input stream: {e}. Exiting.")
                sys.exit(1)

    def __enter__(self) -> "AudioIO":
        """Opens the stream."""
        self.closed = False
        # If using file input, start feeding audio from file
        if self.audio_file_path:
            self._file_feed_task = asyncio.create_task(self._feed_audio_from_file())
        return self

    def __exit__(self, *args: any) -> None:
        """Closes the stream and releases resources."""
        self.closed = True
        
        if self._file_feed_task:
            self._file_feed_task.cancel()
            
        if self._input_audio_stream:
            self._input_audio_stream.stop_stream()
            self._input_audio_stream.close()
            self._input_audio_stream = None

        if self._output_audio_stream:
            self._output_audio_stream.stop_stream()
            self._output_audio_stream.close()
            self._output_audio_stream = None

        self._buff.put_nowait(None)
        self._audio_interface.terminate()

    async def _feed_audio_from_file(self) -> None:
        """Reads PCM audio file and feeds chunks to the buffer."""
        try:
            # Capture start time
            if self.start_time is None:
                self.start_time = get_current_time()
            
            with open(self.audio_file_path, 'rb') as audio_file:
                logger.info(f"Starting to read audio from file: {self.audio_file_path}")
                
                while not self.closed:
                    # Read chunk_size bytes from file
                    chunk = audio_file.read(self.chunk_size * 2)  # *2 because paInt16 is 2 bytes per sample
                    
                    if not chunk:
                        logger.info("Reached end of audio file")
                        break
                    
                    # Only add to buffer when output stream is stopped (same logic as microphone)
                    if self._output_audio_stream and self._output_audio_stream.is_stopped():
                        await self._buff.put(chunk)
                    
                    self.audio_input.append(chunk)
                    
                    # Simulate real-time playback timing
                    await asyncio.sleep(self.chunk_size / self._rate)
                
                # Signal end of audio
                await self._buff.put(None)
                logger.info("Finished reading audio file")
                
        except FileNotFoundError:
            logger.error(f"Audio file not found: {self.audio_file_path}")
        except Exception as e:
            logger.error(f"Error reading audio file: {e}")

    def _fill_buffer(
        self, in_data: bytes, frame_count: int, time_info: dict, status_flags: int
    ) -> tuple[None, int]:
        """Continuously collect data from the audio stream, into the buffer."""
        if self.start_time is None:
            self.start_time = get_current_time()

        if self._output_audio_stream and self._output_audio_stream.is_stopped():
            self._buff.put_nowait(in_data)
        self.audio_input.append(in_data)

        return None, pyaudio.paContinue

    async def generator(self) -> AsyncGenerator[bytes, None]:
        """Stream Audio from microphone/file to API and to local buffer."""
        while not self.closed:
            try:
                chunk = await asyncio.wait_for(self._buff.get(), timeout=1)

                if chunk is None:
                    logger.debug("[generator] Received None chunk, ending stream")
                    return

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
                logger.debug("[generator] No audio chunk received within timeout, continuing...")
                continue

    def play_audio(self, audio_data: bytes) -> None:
        """Plays audio from the given bytes data, removing WAV header if needed."""
        if audio_data.startswith(b"RIFF"):
            try:
                header_size = struct.calcsize("<4sI4s4sIHHIIHH4sI")
                header = struct.unpack("<4sI4s4sIHHIIHH4sI", audio_data[:header_size])
                logger.debug(f"WAV header detected: {header}")
                audio_data = audio_data[header_size:]
            except struct.error as e:
                logger.error(f"Error unpacking WAV header: {e}")

        try:
            self._output_audio_stream.start_stream()
            self._output_audio_stream.write(audio_data)
        finally:
            self._output_audio_stream.stop_stream()


async def main(
    agent_name: str,
    language_code: str = DEFAULT_LANGUAGE_CODE,
    single_utterance: bool = False,
    model: str | None = None,
    voice: str | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    dialogflow_timeout: float = DEFAULT_DIALOGFLOW_TIMEOUT,
    debug: bool = False,
    audio_file: str | None = None,  # New parameter
) -> None:
    """Start bidirectional streaming from microphone input or audio file to speech API"""

    chunk_size = int(sample_rate * CHUNK_SECONDS)

    audioIO = AudioIO(sample_rate, chunk_size, audio_file_path=audio_file)
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

    logger.info(f"Chunk size: {audioIO.chunk_size}")
    if audio_file:
        logger.info(f"Using audio file: {audio_file}")
    else:
        logger.info(f"Using input device: {audioIO.input_device_name}")
    logger.info(f"Using output device: {audioIO.output_device_name}")

    def signal_handler(sig: int, frame: any) -> None:
        print(colored("\nExiting gracefully...", "yellow"))
        audioIO.closed = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    with audioIO:
        logger.info(f"NEW REQUEST: {get_current_time() / 1000}")
        audio_queue = asyncio.Queue()

        try:
            await asyncio.wait_for(
                handle_audio_input_output(dialogflow_streaming, audioIO, audio_queue),
                timeout=dialogflow_streaming.dialogflow_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Dialogflow interaction timed out after {dialogflow_streaming.dialogflow_timeout} seconds."
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("agent_name", help="Agent Name")
    parser.add_argument(
        "--language_code",
        type=str,
        default=DEFAULT_LANGUAGE_CODE,
        help="Specify the language code (default: en-US)",
    )
    parser.add_argument(
        "--single_utterance",
        action="store_true",
        help="Enable single utterance mode (default: False)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Specify the speech recognition model to use (default: None)",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="Specify the voice for output audio (default: None)",
    )
    parser.add_argument(
        "--sample_rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help="Specify the sample rate in Hz (default: 16000)",
    )
    parser.add_argument(
        "--dialogflow_timeout",
        type=float,
        default=DEFAULT_DIALOGFLOW_TIMEOUT,
        help="Specify the Dialogflow API timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--audio_file",
        type=str,
        default=None,
        help="Path to PCM audio file to use as input instead of microphone",
    )

    args = parser.parse_args()
    asyncio.run(
        main(
            args.agent_name,
            args.language_code,
            args.single_utterance,
            args.model,
            args.voice,
            args.sample_rate,
            args.dialogflow_timeout,
            args.debug,
            args.audio_file,
        )
    )

