"""One bounded CPU profile; clients cannot override model or resource budgets."""

from core.contracts.base import JsonMapping

ENGINE_VERSION = "1.2.1"
MODEL_ID = "Systran/faster-whisper-base"
MODEL_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 120
SAMPLE_RATE = 16000
MAX_SAMPLES = MAX_SECONDS * SAMPLE_RATE
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 4000
CPU_THREADS = 2
EXECUTION_SECONDS = 300
MEMORY_BYTES = 3 * 1024**3
MAX_RSS_BYTES = 1536 * 1024**2
PARAMETERS: JsonMapping = {
    "device": "cpu",
    "compute_type": "int8",
    "cpu_threads": CPU_THREADS,
    "num_workers": 1,
    "beam_size": 5,
    "temperature": 0.0,
    "condition_on_previous_text": False,
    "vad_filter": True,
    "min_silence_duration_ms": 500,
    "task": "transcribe",
    "sample_rate": SAMPLE_RATE,
}


class AsrError(Exception):
    """Safe enumerated operation failure; no native diagnostics or input text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
