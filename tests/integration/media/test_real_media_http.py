"""A media capture through a real server process, on a real socket, real ffprobe.

``TestClient`` runs the application inside the test's own interpreter, which is
enough for almost everything and not enough for this: ``--media`` is a *command
line*, and "stop the server and start it again over the same directory" is only a
durability claim if the first process is genuinely gone. So these tests run
``python -m unimem_api --media`` as a subprocess on a dynamically allocated port
and talk to it over HTTP.

The probe is the installed ffprobe and the files are built by the system ffmpeg
during the test. **Nothing under ``src/`` invokes ffmpeg**: the server sees an
ordinary upload.

The one thing substituted anywhere in this file is the engine in
:class:`TestARuntimeProbeFailureOverHttp`, which needs an ffprobe that passes
startup validation and then fails on a capture; no real engine can be asked to do
that on demand, and the behaviour it proves — a non-terminal capture behind a safe
503 — is the one a running deployment most needs to be true.

A server that will not start is always a failure here, never a skip.
"""

import hashlib
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import httpx2
import pytest

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.processing.media import (
    AUDIO_STREAM_COUNT_KEY,
    AUDIO_STREAMS_KEY,
    CONTAINER_KEY,
    DURATION_SECONDS_KEY,
    MEDIA_METADATA_KEY,
    VIDEO_STREAM_COUNT_KEY,
    VIDEO_STREAMS_KEY,
)
from tests import media_fixtures as fixtures
from tests import media_support
from unimem_api import DATABASE_FILENAME, RAW_DIRNAME

media_support.require_media_tooling()

CAPTURED_AT: Final = "2026-02-03T04:05:06+00:00"

#: How long one HTTP call may take. Probing happens inside the request.
HTTP_TIMEOUT: Final = 180.0

#: A stub ffprobe that answers the two startup probes exactly as a real build
#: does and then fails on every actual probe. It is written to disk by the test,
#: and it exists to reach a code path no installed engine can be asked for.
BROKEN_FFPROBE: Final = """#!{python}
import sys

arguments = sys.argv[1:]
if arguments == ["-version"]:
    print("ffprobe version 9.9.9-stub Copyright (c) 2007-2026 the FFmpeg developers")
    raise SystemExit(0)
if arguments == ["-protocols"]:
    print("Supported file protocols:")
    print("Input:")
    print("  fd")
    print("Output:")
    print("  fd")
    raise SystemExit(0)
print("stub: refusing to probe", file=sys.stderr)
raise SystemExit(1)
"""


def payload_type_for(fixture: fixtures.MediaFixture) -> str:
    return "video" if fixture.has_video else "audio"


def envelope(capture_id: str, file_ref: str, fixture: fixtures.MediaFixture) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": capture_id,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": payload_type_for(fixture),
            "mime_type": fixture.mime_type,
            "file_ref": file_ref,
        },
        "context": {"captured_at": CAPTURED_AT},
    }


class Api:
    """The four calls these tests make, over a real socket to a real process."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.client = httpx2.Client(timeout=HTTP_TIMEOUT)

    def close(self) -> None:
        self.client.close()

    def upload(self, data: bytes, fixture: fixtures.MediaFixture) -> str:
        response = self.client.post(
            f"{self.base}/v1/uploads",
            files={"file": (fixture.name, data, fixture.mime_type)},
        )
        assert response.status_code == 200, response.text
        file_ref: str = response.json()["file_ref"]
        return file_ref

    def capture(self, capture_id: str, data: bytes, fixture: fixtures.MediaFixture) -> Any:
        return self.client.post(
            f"{self.base}/v1/captures",
            json=envelope(capture_id, self.upload(data, fixture), fixture),
        )

    def record(self, capture_id: str) -> CaptureRecord:
        response = self.client.get(f"{self.base}/v1/captures/{capture_id}")
        assert response.status_code == 200, response.text
        return CaptureRecord.model_validate(response.json())

    def content(self, capture_id: str) -> ContentObject:
        response = self.client.get(f"{self.base}/v1/captures/{capture_id}/content")
        assert response.status_code == 200, response.text
        return ContentObject.model_validate(response.json())

    def status_of(self, path: str) -> int:
        return self.client.get(f"{self.base}{path}").status_code


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


@pytest.fixture
def api(data_dir: Path) -> Iterator[Api]:
    """A server process with ``--media``, and a client pointed at it."""
    with media_support.serve_process(data_dir) as base:
        served = Api(base)
        try:
            yield served
        finally:
            served.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    directory = tmp_path_factory.mktemp("http-media")
    return {fixture.name: fixtures.build(fixture, directory) for fixture in fixtures.ALL}


def media_metadata(content: ContentObject) -> dict[str, Any]:
    """``metadata["media"]``, narrowed so a type checker can follow it.

    ``ContentObject.metadata`` is a JSON mapping, so every read out of it is a
    ``JsonValue`` until something asserts otherwise. Asserting it here once keeps
    the tests below reading like the claims they are.
    """
    media = content.metadata[MEDIA_METADATA_KEY]
    assert isinstance(media, dict)
    return media


def stream_entries(content: ContentObject, key: str) -> list[dict[str, Any]]:
    """The stream list at ``key``, narrowed the same way."""
    listed = content.metadata[key]
    assert isinstance(listed, list)
    entries: list[dict[str, Any]] = []
    for entry in listed:
        assert isinstance(entry, dict)
        entries.append(entry)
    return entries


def raw_path(data_dir: Path, digest: str) -> Path:
    return data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest


def capture_rows(data_dir: Path, table: str, capture_id: str, column: str) -> int:
    connection = sqlite3.connect(data_dir / DATABASE_FILENAME)
    try:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (capture_id,)
        ).fetchone()
    finally:
        connection.close()
    return int(found[0])


class TestEveryFamilyOverRealHttp:
    """One capture per supported family, end to end, through the real stack."""

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_it_is_accepted(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        response = api.capture(f"cap_{fixture.container}_ok", built[fixture.name], fixture)

        assert response.status_code == 201, response.text

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_capture_reaches_complete(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        capture_id = f"cap_{fixture.container}_complete"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201

        assert api.record(capture_id).status is CaptureStatus.COMPLETE

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_container_this_build_routes_on_is_recorded(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        capture_id = f"cap_{fixture.container}_container"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201

        media = media_metadata(api.content(capture_id))

        assert media[CONTAINER_KEY] == fixture.container

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_content_type_matches_the_modality(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        capture_id = f"cap_{fixture.container}_type"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201

        expected = ContentType.VIDEO if fixture.has_video else ContentType.AUDIO
        assert api.content(capture_id).type is expected

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_duration_the_container_declared_survives(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        capture_id = f"cap_{fixture.container}_duration"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201

        media = media_metadata(api.content(capture_id))

        duration = media[DURATION_SECONDS_KEY]
        assert isinstance(duration, float)
        assert 0.9 <= duration <= 1.2

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_stream_counts_match_the_stream_lists(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        capture_id = f"cap_{fixture.container}_counts"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201
        content = api.content(capture_id)

        media = media_metadata(content)
        assert media[AUDIO_STREAM_COUNT_KEY] == len(stream_entries(content, AUDIO_STREAMS_KEY))
        assert media[VIDEO_STREAM_COUNT_KEY] == len(stream_entries(content, VIDEO_STREAMS_KEY))

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_immutable_original_is_the_exact_submitted_bytes(
        self, api: Api, data_dir: Path, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        data = built[fixture.name]
        capture_id = f"cap_{fixture.container}_original"
        assert api.capture(capture_id, data, fixture).status_code == 201

        digest = hashlib.sha256(data).hexdigest()
        assert raw_path(data_dir, digest).read_bytes() == data
        assert api.content(capture_id).original.sha256 == digest

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_nothing_was_interpreted(
        self, api: Api, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        """No transcript, no keyframe, no thumbnail, no extracted audio track."""
        capture_id = f"cap_{fixture.container}_uninterpreted"
        assert api.capture(capture_id, built[fixture.name], fixture).status_code == 201
        content = api.content(capture_id)

        assert content.segments == []
        assert len(content.assets) == 1
        assert content.assets[0].role.value == "original"


class TestTheRealStreamFactsReachCanonicalContent:
    def test_a_wav_records_its_sample_rate_and_channels(
        self, api: Api, built: dict[str, bytes]
    ) -> None:
        assert api.capture("cap_wav_streams", built[fixtures.WAV.name], fixtures.WAV).status_code

        streams = stream_entries(api.content("cap_wav_streams"), AUDIO_STREAMS_KEY)

        assert streams == [
            {
                "index": 0,
                "codec": "pcm_s16le",
                "sample_rate": fixtures.AUDIO_SAMPLE_RATE,
                "channels": fixtures.AUDIO_CHANNELS,
            }
        ]

    def test_a_silent_video_records_dimensions_and_an_exact_frame_rate(
        self, api: Api, built: dict[str, bytes]
    ) -> None:
        assert api.capture("cap_mp4_streams", built[fixtures.MP4.name], fixtures.MP4).status_code
        content = api.content("cap_mp4_streams")

        assert stream_entries(content, VIDEO_STREAMS_KEY) == [
            {
                "index": 0,
                "codec": "h264",
                "width": fixtures.VIDEO_WIDTH,
                "height": fixtures.VIDEO_HEIGHT,
                "frame_rate": fixtures.VIDEO_FRAME_RATE,
            }
        ]
        assert stream_entries(content, AUDIO_STREAMS_KEY) == []

    def test_a_video_with_audio_records_both_in_index_order(
        self, api: Api, built: dict[str, bytes]
    ) -> None:
        fixture = fixtures.MP4_WITH_AUDIO
        assert api.capture("cap_av_streams", built[fixture.name], fixture).status_code == 201
        content = api.content("cap_av_streams")

        assert stream_entries(content, VIDEO_STREAMS_KEY)[0]["index"] == 0
        assert stream_entries(content, AUDIO_STREAMS_KEY)[0]["index"] == 1

    def test_a_webm_records_vp8_and_its_dimensions(self, api: Api, built: dict[str, bytes]) -> None:
        fixture = fixtures.WEBM
        assert api.capture("cap_webm_streams", built[fixture.name], fixture).status_code == 201

        streams = stream_entries(api.content("cap_webm_streams"), VIDEO_STREAMS_KEY)

        assert streams[0]["codec"] == "vp8"
        assert streams[0]["width"] == fixtures.VIDEO_WIDTH

    def test_no_engine_identity_is_recorded_anywhere(
        self, api: Api, built: dict[str, bytes]
    ) -> None:
        """ADR-021: which build read the container is not what the container says."""
        assert api.capture("cap_no_engine", built[fixtures.MP3.name], fixtures.MP3).status_code
        content = api.content("cap_no_engine")

        rendered = content.model_dump_json()
        assert "ffprobe" not in rendered
        assert "ffmpeg" not in rendered

    def test_no_raw_probe_output_is_recorded(self, api: Api, built: dict[str, bytes]) -> None:
        assert api.capture("cap_no_raw", built[fixtures.OGG.name], fixtures.OGG).status_code
        rendered = api.content("cap_no_raw").model_dump_json()

        for engine_vocabulary in ("format_name", "codec_type", "avg_frame_rate", "0/0", "N/A"):
            assert engine_vocabulary not in rendered

    def test_no_embedded_tag_reaches_the_title(self, api: Api, built: dict[str, bytes]) -> None:
        """No title was submitted, so there must be none — not one read out."""
        assert api.capture("cap_no_tags", built[fixtures.MP3.name], fixtures.MP3).status_code

        assert api.content("cap_no_tags").title is None


class TestADeclarationTheBytesContradict:
    def test_an_mp3_declared_as_mp4_is_refused(self, api: Api, built: dict[str, bytes]) -> None:
        """The declared type routes and the probed container verifies."""
        mislabelled = fixtures.MediaFixture("wrong.mp4", "video/mp4", "mp4", False, True)
        response = api.capture("cap_mismatch", built[fixtures.MP3.name], mislabelled)

        assert response.status_code == 422, response.text

    def test_that_capture_is_durably_failed(self, api: Api, built: dict[str, bytes]) -> None:
        """A deterministic verdict about the bytes, so FAILED is truthful."""
        mislabelled = fixtures.MediaFixture("wrong.mp4", "video/mp4", "mp4", False, True)
        assert api.capture("cap_mismatch_state", built[fixtures.MP3.name], mislabelled)

        assert api.record("cap_mismatch_state").status is CaptureStatus.FAILED

    def test_no_content_object_is_written_for_it(
        self, api: Api, data_dir: Path, built: dict[str, bytes]
    ) -> None:
        mislabelled = fixtures.MediaFixture("wrong.mp4", "video/mp4", "mp4", False, True)
        assert api.capture("cap_mismatch_none", built[fixtures.MP3.name], mislabelled)

        assert api.status_of("/v1/captures/cap_mismatch_none/content") == 404
        assert capture_rows(data_dir, "content_objects", "cap_mismatch_none", "capture_id") == 0

    def test_the_refusal_says_nothing_about_what_the_server_saw(
        self, api: Api, built: dict[str, bytes]
    ) -> None:
        mislabelled = fixtures.MediaFixture("wrong.mp4", "video/mp4", "mp4", False, True)
        response = api.capture("cap_mismatch_quiet", built[fixtures.MP3.name], mislabelled)

        body = response.text
        assert "mp3" not in body
        assert "ffprobe" not in body

    def test_unreadable_bytes_declared_as_media_stay_non_terminal(self, api: Api) -> None:
        """No trusted result, so no verdict: 503 and still PROCESSING."""
        response = api.capture("cap_unreadable", fixtures.unreadable_bytes(), fixtures.MP3)

        assert response.status_code == 503, response.text
        assert api.record("cap_unreadable").status is CaptureStatus.PROCESSING


class TestTheDefaultDeploymentStillRefusesMedia:
    def test_a_media_capture_is_refused_at_the_door(
        self, data_dir: Path, built: dict[str, bytes]
    ) -> None:
        with media_support.serve_process(data_dir, media=False) as base:
            api = Api(base)
            try:
                response = api.capture("cap_default", built[fixtures.MP3.name], fixtures.MP3)
            finally:
                api.close()

        assert response.status_code == 422, response.text

    def test_a_default_server_starts_without_ffprobe_on_path(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        """The capability is genuinely optional, proved with the engine hidden."""
        empty = tmp_path / "empty-path"
        empty.mkdir()
        with media_support.serve_process(data_dir, media=False, env={"PATH": str(empty)}) as base:
            assert media_support.read_json(f"{base}/health")["status"] == "ok"


class TestARestartOfTheServerProcess:
    """The first process is stopped, a second starts over the same directory."""

    @pytest.fixture
    def before(self, data_dir: Path, built: dict[str, bytes]) -> dict[str, Any]:
        fixture = fixtures.MP4_WITH_AUDIO
        with media_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                assert api.capture("cap_restart", built[fixture.name], fixture).status_code == 201
                return {
                    "record": api.record("cap_restart"),
                    "content": api.content("cap_restart"),
                }
            finally:
                api.close()

    def test_everything_reads_back_identically_from_a_new_process(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with media_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                after_record = api.record("cap_restart")
                after_content = api.content("cap_restart")
            finally:
                api.close()

        assert after_record == before["record"]
        assert after_content == before["content"]

    def test_the_structural_metadata_survives_unchanged(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with media_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                after = api.content("cap_restart")
            finally:
                api.close()

        original: ContentObject = before["content"]
        assert after.id == original.id
        assert after.metadata == original.metadata
        assert after.assets[0].id == original.assets[0].id
        assert after.segments == []

    def test_a_default_process_can_still_read_what_a_media_one_wrote(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        """Stored canonical content is just content; reading it needs no engine."""
        with media_support.serve_process(data_dir, media=False) as base:
            api = Api(base)
            try:
                after = api.content("cap_restart")
            finally:
                api.close()

        assert after == before["content"]


class TestARuntimeProbeFailureOverHttp:
    """An engine that passes startup and then fails on the capture.

    The only substitution in this file, and it is unavoidable: an installed
    ffprobe cannot be asked to succeed at startup and fail on demand. What it
    proves is the lifecycle a running deployment most needs — a failure that
    reaches no verdict leaves the capture non-terminal behind a fixed 503.
    """

    @pytest.fixture
    def broken_path(self, tmp_path: Path) -> Path:
        directory = tmp_path / "broken-bin"
        directory.mkdir()
        stub = directory / "ffprobe"
        stub.write_text(BROKEN_FFPROBE.format(python=sys.executable))
        stub.chmod(0o755)
        return directory

    @pytest.fixture
    def broken_api(self, data_dir: Path, broken_path: Path) -> Iterator[Api]:
        import os

        with media_support.serve_process(
            data_dir, env={"PATH": f"{broken_path}{os.pathsep}{os.environ['PATH']}"}
        ) as base:
            served = Api(base)
            try:
                yield served
            finally:
                served.close()

    def test_the_server_started_at_all(self, broken_api: Api) -> None:
        """The stub answers both startup probes, so the gate must have passed."""
        assert media_support.read_json(f"{broken_api.base}/health")["status"] == "ok"

    def test_the_capture_is_answered_503(self, broken_api: Api, built: dict[str, bytes]) -> None:
        response = broken_api.capture("cap_broken", built[fixtures.MP3.name], fixtures.MP3)

        assert response.status_code == 503, response.text

    def test_the_error_code_is_the_fixed_media_one(
        self, broken_api: Api, built: dict[str, bytes]
    ) -> None:
        response = broken_api.capture("cap_broken_code", built[fixtures.MP3.name], fixtures.MP3)

        assert response.json()["error"]["code"] == "media_probe_unavailable"

    def test_the_capture_stays_processing(self, broken_api: Api, built: dict[str, bytes]) -> None:
        """Never FAILED: the system never managed to look at the file."""
        assert broken_api.capture("cap_broken_state", built[fixtures.MP3.name], fixtures.MP3)

        assert broken_api.record("cap_broken_state").status is CaptureStatus.PROCESSING

    def test_no_content_object_is_written(
        self, broken_api: Api, data_dir: Path, built: dict[str, bytes]
    ) -> None:
        assert broken_api.capture("cap_broken_none", built[fixtures.MP3.name], fixtures.MP3)

        assert broken_api.status_of("/v1/captures/cap_broken_none/content") == 404
        assert capture_rows(data_dir, "content_objects", "cap_broken_none", "capture_id") == 0

    def test_the_original_is_still_stored(
        self, broken_api: Api, data_dir: Path, built: dict[str, bytes]
    ) -> None:
        """The capture is recoverable: the bytes were accepted before probing."""
        data = built[fixtures.MP3.name]
        assert broken_api.capture("cap_broken_raw", data, fixtures.MP3)

        assert raw_path(data_dir, hashlib.sha256(data).hexdigest()).read_bytes() == data

    def test_no_backend_detail_leaks_into_the_body(
        self, broken_api: Api, built: dict[str, bytes]
    ) -> None:
        response = broken_api.capture("cap_broken_quiet", built[fixtures.MP3.name], fixtures.MP3)

        body = response.text
        assert "ffprobe" not in body
        assert "refusing to probe" not in body
        assert "/tmp" not in body


class TestStartupRefusalIsNotASkip:
    def test_a_media_deployment_without_ffprobe_refuses_to_start(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        completed = media_support.start_without_ffprobe(data_dir, tmp_path / "no-tools")

        assert completed.returncode != 0
        assert "--media was requested" in completed.stderr

    def test_it_fails_before_the_data_directory_is_created(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        media_support.start_without_ffprobe(data_dir, tmp_path / "no-tools-2")

        assert not data_dir.exists()
