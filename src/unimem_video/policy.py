"""Versioned finite local-video policy, never overridden by browser input."""

MAX_BYTES = 32 * 1024**2
MAX_SECONDS = 120
MAX_PIXELS = 1920 * 1080
MAX_DECODED_PIXELS = 7_500_000_000
MAX_PACKETS = 12000
MAX_VIDEO_FRAMES = 3600
MAX_AUDIO_FRAMES = 6000
MAX_PNG_BYTES = 2 * 1024**2
MAX_DERIVED_BYTES = 6 * 1024**2
DECODE_SECONDS = 60
ASR_SECONDS = 300
FRAME_SECONDS = 210  # includes profile verification, PNG preparation and 180s inference
TOTAL_SECONDS = DECODE_SECONDS + ASR_SECONDS + 3 * FRAME_SECONDS + 30
MEMORY_BYTES = 4 * 1024**3
MAX_RSS_BYTES = 3 * 1024**3
SAMPLING = "quarters-first-pts/1"
DECODER = "mp4-h264-aac/1"
ASR_PROFILE = "faster-whisper-base-int8/1"
VISION_PROFILE = "qwen3-vl-2b-q4-b11146-visible-ru/1"


class VideoError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
