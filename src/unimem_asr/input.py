"""Cheap pre-acceptance checks. Native decoding runs only in the bounded child."""

from typing import BinaryIO, Literal

from unimem_asr.policy import MAX_BYTES, AsrError

MIMES = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/mp3": "audio/mpeg",
    "application/ogg": "audio/ogg",
    "audio/ogg; codecs=opus": "audio/ogg",
}


def inspect_input(stream: BinaryIO, declared: str) -> tuple[Literal["wav", "mp3", "ogg"], str, int]:
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    head = stream.read(16)
    stream.seek(0)
    if not 0 < size <= MAX_BYTES:
        raise AsrError("input_size_limit")
    container: Literal["wav", "mp3", "ogg"]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        container = "wav"
    elif head[:4] == b"OggS":
        container = "ogg"
    elif head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 255 and head[1] & 0xE0 == 0xE0):
        container = "mp3"
    else:
        raise AsrError("unsupported_format")
    # Empty/generic browser types permit signature detection, never filename inference.
    declared = declared.strip().lower()
    if ALIASES.get(declared, declared) not in ("", "application/octet-stream", MIMES[container]):
        raise AsrError("mime_mismatch")
    return container, MIMES[container], size
