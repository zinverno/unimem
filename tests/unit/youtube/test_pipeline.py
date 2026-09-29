from pathlib import Path

import pytest

from core.contracts import (
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureStatus,
    ContentObject,
    SegmentType,
)
from core.intake import CaptureIntake
from core.intake.errors import CaptureMaterialUnavailableError, UnsupportedCapturePayloadError
from tests.youtube_fixtures import URL, artifact
from unimem_youtube.artifact import CAPTION_MIME, CaptionArtifact
from unimem_youtube.errors import AcquisitionError, CapturePipelineError, ExportError
from unimem_youtube.service import YoutubeCaptureService


def fixture_acquire(url: str, languages: tuple[str, ...]) -> CaptionArtifact:
    assert url == URL
    assert languages == ("ru", "en")
    return artifact()


def test_persisted_cues_source_and_provenance_survive_reopening(tmp_path: Path) -> None:
    service = YoutubeCaptureService(tmp_path)
    content = service.capture(URL, ("ru", "en"), acquire=fixture_acquire)
    capture_id = content.source.capture_id
    record = service.record_store.get(capture_id)
    assert record.status is CaptureStatus.COMPLETE
    assert record.context is not None
    assert record.context.captured_at == artifact().captured_at
    assert record.raw_object is not None
    assert service.raw_store.read_bytes(record.raw_object) == artifact().model_dump_json().encode()
    assert content.title is None
    assert content.source.url == URL
    assert content.source.provider == "youtube"
    assert [s.position for s in content.segments] == [0, 1, 2, 3]
    assert content.segments[0].text == "Привет & мир — café 😺"
    assert content.segments[1].text == content.segments[2].text
    assert content.segments[3].type is SegmentType.SECTION
    assert content.segments[3].metadata["caption_text"] == " "
    times = [s.temporal for s in content.segments]
    assert times[0] is not None
    assert times[0].start == 1.25
    assert times[0].end == 3.75
    assert times[1] is not None
    assert times[1].start == 4
    assert times[1].end is None
    assert times[2] is not None
    assert times[2].end == 5
    assert "duration_seconds" not in content.segments[1].metadata
    for segment in content.segments:
        assert segment.provenance.capture_id == capture_id
        assert segment.provenance.asset_id == content.assets[0].id
        assert segment.provenance.source_type.value == "original"
        assert segment.provenance.processor == "youtube-caption-xml"
    # SQLite connections close per operation; no in-memory object is needed.
    del service
    reopened = YoutubeCaptureService(tmp_path)
    assert reopened.read(capture_id) == content
    assert reopened.raw_store.read_bytes(record.raw_object) == artifact().model_dump_json().encode()


@pytest.mark.parametrize(
    "xml",
    [
        "not xml",
        "<transcript/>",
        "<other/>",
        '<transcript><text start="NaN">x</text></transcript>',
        '<transcript><text start="-1">x</text></transcript>',
        '<transcript><text start="1" dur="inf">x</text></transcript>',
        '<transcript><text start="1e308" dur="1e308">x</text></transcript>',
        '<transcript><text start="1"><b>x</b></text></transcript>',
        '<transcript><text start="1" unknown="x">x</text></transcript>',
        '<!DOCTYPE transcript [<!ENTITY x "secret">]><transcript><text>&x;</text></transcript>',
        "<transcript><text> </text></transcript>",
        "<transcript>lost<text>x</text></transcript>",
    ],
)
def test_damaged_artifact_is_failed_not_complete(tmp_path: Path, xml: str) -> None:
    service = YoutubeCaptureService(tmp_path)
    with pytest.raises(CapturePipelineError) as failure:
        service.capture(URL, ("ru",), acquire=lambda *_: artifact(xml))
    assert failure.value.code == "processing_failed"
    assert service.record_store.get(failure.value.capture_id).status is CaptureStatus.FAILED


def test_failed_normalization_has_a_truthful_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = YoutubeCaptureService(tmp_path)
    ids: list[str] = []
    original = service.orchestrator.process

    def process(capture_id: str) -> ContentObject:
        ids.append(capture_id)
        return original(capture_id)

    monkeypatch.setattr(service.orchestrator, "process", process)
    with pytest.raises(CapturePipelineError):
        service.capture(URL, ("ru",), acquire=lambda *_: artifact("<broken>"))
    assert service.record_store.get(ids[0]).status is CaptureStatus.FAILED
    with pytest.raises(ExportError, match="failed"):
        YoutubeCaptureService(tmp_path).read(ids[0])


def test_missing_start_is_not_invented(tmp_path: Path) -> None:
    service = YoutubeCaptureService(tmp_path)
    content = service.capture(
        URL,
        ("ru",),
        acquire=lambda *_: artifact('<transcript><text dur="2">untimed</text></transcript>'),
    )
    assert content.segments[0].temporal is None
    assert content.segments[0].metadata["duration_seconds"] == 2


def test_wrong_video_or_language_cannot_be_accepted(tmp_path: Path) -> None:
    service = YoutubeCaptureService(tmp_path)
    with pytest.raises(AcquisitionError, match="different video or language"):
        service.capture(URL, ("en",), acquire=lambda *_: artifact())


def test_intake_is_opt_in_and_staged_only(tmp_path: Path) -> None:
    service = YoutubeCaptureService(tmp_path)
    content = service.capture(URL, ("ru", "en"), acquire=fixture_acquire)
    record = service.record_store.get(content.source.capture_id)
    assert record.context is not None
    assert record.raw_object is not None
    envelope = CaptureEnvelope(
        id="second",
        source=record.source,
        context=record.context,
        payload=CapturePayload(
            type=CapturePayloadType.FILE, mime_type=CAPTION_MIME, file_ref=record.raw_object.ref
        ),
    )
    with pytest.raises(UnsupportedCapturePayloadError):
        CaptureIntake(service.raw_store, service.record_store).accept(envelope)
    for ref in ("/tmp/private", "https://example.test/captions"):
        envelope.payload.file_ref = ref
        with pytest.raises(UnsupportedCapturePayloadError, match="raw object reference"):
            service.intake.accept(envelope)
    envelope.payload.file_ref = "sha256:" + "0" * 64
    with pytest.raises(CaptureMaterialUnavailableError):
        service.intake.accept(envelope)
    envelope.payload.mime_type = "text/plain"
    with pytest.raises(UnsupportedCapturePayloadError, match="MIME"):
        service.intake.accept(envelope)
