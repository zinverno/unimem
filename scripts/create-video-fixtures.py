"""Create the bounded native acceptance fixtures; no models or network. See LOCAL_VIDEO.md."""

import hashlib
import json
import sys
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Inputs: previously downloaded public Vosk test.wav, empty output directory.
r = Path(sys.argv[2])
r.mkdir(parents=True, exist_ok=False)
source = Path(sys.argv[1])
assert (
    hashlib.sha256(source.read_bytes()).hexdigest()
    == "dcfea5712c43a43ba7ae8083afb39d36993e5a69c46e88b68aaa72b65cb615bb"
)
scenes = []
font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 30)
for i, (name, color) in enumerate(
    [("RED SQUARE", "#d92f36"), ("BLUE CIRCLE", "#2869cc"), ("GREEN TRIANGLE", "#268344")]
):
    im = Image.new("RGB", (768, 432), "white")
    d = ImageDraw.Draw(im)
    if i == 0:
        d.rectangle((280, 105, 490, 315), fill=color)
    elif i == 1:
        d.ellipse((275, 100, 495, 320), fill=color)
    else:
        d.polygon([(384, 90), (255, 320), (513, 320)], fill=color)
    d.text((30, 25), name, fill="black", font=font)
    im.save(r / f"scene-{i + 1}.png")
    scenes.append(np.array(im))
with wave.open(str(source), "rb") as w:
    assert w.getnchannels() == 1
    assert w.getsampwidth() == 2
    rate = w.getframerate()
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()
for silent in (False, True):
    target = r / ("silent.mp4" if silent else "speech-scenes.mp4")
    duration = 3 if silent else 10
    with av.open(str(target), "w", format="mp4") as out:
        v = out.add_stream("libx264", rate=10)
        v.width = 768
        v.height = 432
        v.pix_fmt = "yuv420p"
        v.time_base = Fraction(1, 10)
        v.codec_context.time_base = Fraction(1, 10)
        a = None
        if not silent:
            a = out.add_stream("aac", rate=rate)
            a.layout = "mono"
        for i in range(duration * 10):
            f = av.VideoFrame.from_ndarray(scenes[min(2, i // 34)], format="rgb24")
            f.pts = i
            f.time_base = Fraction(1, 10)
            out.mux(v.encode(f))
        out.mux(v.encode(None))
        if a:
            for i in range(0, len(audio), 1024):
                f = av.AudioFrame.from_ndarray(
                    audio[i : i + 1024].reshape(1, -1), format="s16", layout="mono"
                )
                f.sample_rate = rate
                f.pts = rate + i
                f.time_base = Fraction(1, rate)
                out.mux(a.encode(f))
            out.mux(a.encode(None))
info = {
    "source_audio": "Vosk public Apache-2.0 test.wav (existing approved fixture)",
    "audio_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "audio_offset_requested_seconds": 1,
    "reference": "one zero zero zero one / nine oh two one oh / zero one eight zero three",
    "scenes": [
        {"start": 0, "end": 3.4, "visible": "red square"},
        {"start": 3.4, "end": 6.8, "visible": "blue circle"},
        {"start": 6.8, "end": 10, "visible": "green triangle"},
    ],
    "files": {
        p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in r.glob("*.mp4")
    },
}
(r / "fixture.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
