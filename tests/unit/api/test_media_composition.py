"""Wiring the media capability, and the two halves that must move together.

``media_probe`` is unlike ``pdf_ocr`` and ``image_ocr``. Those choose *which*
processor handles a capture the build already accepts; this one decides whether
a whole modality is accepted at all, so it reaches two places — intake's
``media_enabled`` and the router's processor list — and both are derived from
the single argument.

The states that must be impossible are the point: media accepted at the door
with no processor behind it, or media processors registered behind an intake
that refuses every such capture. Either would strand captures mid-lifecycle for
a reason nobody could act on.

Nothing here imports a media engine, because there is none to import.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.intake import AUDIO_MIME_TYPES as INTAKE_AUDIO_MIME_TYPES
from core.intake import VIDEO_MIME_TYPES as INTAKE_VIDEO_MIME_TYPES
from core.processing import AUDIO_MIME_TYPES, VIDEO_MIME_TYPES, AudioProcessor, VideoProcessor
from core.storage import LocalRawObjectStore, build_raw_ref
from tests.unit.processing.doubles import (
    FakeMediaProbe,
    audio_stream,
    probe_result,
    video_stream,
)
from unimem_api import build_local_app
from unimem_api.wiring import _media_processors

MP3 = b"\xff\xfb\x90\x00fake mp3 payload"
MP3_DIGEST = hashlib.sha256(MP3).hexdigest()
MP3_FILE_REF = build_raw_ref(MP3_DIGEST)

CAPTURED_AT = "2026-05-06T07:08:09+00:00"


def working_probe() -> FakeMediaProbe:
    """A probe that describes an ordinary single-stream MP3."""
    return FakeMediaProbe(
        result=probe_result(container_names=("mp3",), audio_streams=(audio_stream(index=0),))
    )


def media_envelope(payload_type: str, mime_type: str, file_ref: str, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "schema_version": "0.3",
        "id": f"cap_{payload_type}_01",
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": payload_type, "mime_type": mime_type, "file_ref": file_ref},
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


def stage(client: TestClient, data: bytes, mime_type: str) -> str:
    """Stage bytes through the real upload route, which stays format-blind."""
    upload = client.post("/v1/uploads", files={"file": ("clip", data, mime_type)})
    assert upload.status_code == 200
    return str(upload.json()["file_ref"])


def media_processors(probe: FakeMediaProbe | None) -> tuple[Any, ...]:
    """The media processors a deployment with this probe would register.

    Asked of the composition root's own helper rather than of a built app: the
    router is a constructor argument rather than app state, so reaching into a
    ``FastAPI`` object for it would be testing an accident of wiring. This
    function *is* the decision.
    """
    return _media_processors(LocalRawObjectStore(Path("unused")), probe)


class TestTheDefaultDeploymentHasNoMedia:
    """No ``media_probe``, and today's build exactly."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> Any:
        with TestClient(build_local_app(tmp_path / "data")) as test_client:
            yield test_client

    @pytest.mark.parametrize(
        ("payload_type", "mime_type"), [("audio", "audio/mpeg"), ("video", "video/mp4")]
    )
    def test_a_media_capture_is_refused(
        self, client: TestClient, payload_type: str, mime_type: str
    ) -> None:
        file_ref = stage(client, MP3, mime_type)

        response = client.post(
            "/v1/captures", json=media_envelope(payload_type, mime_type, file_ref)
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_no_media_processor_is_registered(self) -> None:
        assert media_processors(None) == ()

    def test_staging_media_bytes_still_works(self, client: TestClient) -> None:
        """Upload stays format-blind: only the later capture is refused."""
        file_ref = stage(client, MP3, "audio/mpeg")

        assert file_ref == MP3_FILE_REF


class TestAMediaDeploymentEnablesBothTogether:
    """One probe in, both modalities out."""

    @pytest.fixture
    def probe(self) -> FakeMediaProbe:
        return working_probe()

    @pytest.fixture
    def client(self, tmp_path: Path, probe: FakeMediaProbe) -> Any:
        with TestClient(build_local_app(tmp_path / "data", media_probe=probe)) as test_client:
            yield test_client

    def test_both_processors_are_registered(self, probe: FakeMediaProbe) -> None:
        """Never one. Half a capability strands every capture of the other half."""
        names = {processor.name for processor in media_processors(probe)}

        assert names == {"audio", "video"}

    def test_an_audio_capture_completes(self, client: TestClient) -> None:
        file_ref = stage(client, MP3, "audio/mpeg")

        response = client.post("/v1/captures", json=media_envelope("audio", "audio/mpeg", file_ref))

        assert response.status_code == 201

    def test_a_video_capture_reaches_its_processor(self, tmp_path: Path) -> None:
        """Enabling media enables both, so video is not refused either."""
        probe = FakeMediaProbe(
            result=probe_result(container_names=("mp4",), video_streams=(video_stream(index=0),))
        )
        with TestClient(build_local_app(tmp_path / "data", media_probe=probe)) as client:
            file_ref = stage(client, MP3, "video/mp4")

            response = client.post(
                "/v1/captures", json=media_envelope("video", "video/mp4", file_ref)
            )

        assert response.status_code == 201

    def test_one_probe_instance_serves_both_processors(self, probe: FakeMediaProbe) -> None:
        """The port holds nothing on the caller's behalf, so sharing is ordinary."""
        audio, video = media_processors(probe)

        assert audio._media_probe is probe
        assert video._media_probe is probe

    def test_they_are_the_real_processors(self, probe: FakeMediaProbe) -> None:
        assert {type(p) for p in media_processors(probe)} == {AudioProcessor, VideoProcessor}


class TestTheAllowlistsStayCoherent:
    """Intake's tuples and processing's tuples are restated, never imported."""

    def test_the_audio_allowlists_agree(self) -> None:
        """Duplication is deliberate; drift is not. This is what forbids drift."""
        assert INTAKE_AUDIO_MIME_TYPES == AUDIO_MIME_TYPES

    def test_the_video_allowlists_agree(self) -> None:
        assert INTAKE_VIDEO_MIME_TYPES == VIDEO_MIME_TYPES

    def test_they_name_exactly_the_agreed_formats(self) -> None:
        assert AUDIO_MIME_TYPES == ("audio/mpeg", "audio/wav", "audio/ogg")
        assert VIDEO_MIME_TYPES == ("video/mp4", "video/webm")

    def test_the_two_allowlists_are_disjoint(self) -> None:
        """A merged list would let ``audio/ogg`` be declared on a video capture."""
        assert not set(AUDIO_MIME_TYPES) & set(VIDEO_MIME_TYPES)


class TestNoMediaEngineIsRequired:
    """The default build imports nothing that needs installing."""

    def test_no_media_package_exists(self) -> None:
        import importlib.util

        assert importlib.util.find_spec("unimem_media") is None

    def test_no_cli_media_flag_exists(self) -> None:
        """The capability is programmatic only until Phase 5A-3."""
        import unimem_api.__main__ as cli

        parser = cli.build_parser()
        options = {action.option_strings[0] for action in parser._actions if action.option_strings}
        assert "--media" not in options

    def test_wiring_imports_no_concrete_media_adapter(self) -> None:
        """Checked as imports, not as text: the prose names these to forbid them."""
        import ast

        import unimem_api.wiring as wiring

        tree = ast.parse(Path(wiring.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert not imported & {"subprocess", "tempfile", "unimem_media", "av", "ffmpeg"}
