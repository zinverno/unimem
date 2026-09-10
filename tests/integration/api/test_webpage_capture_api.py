"""A real HTML page, through the real application, on a real directory.

Nothing is faked. ``build_local_app`` wires ``LocalRawObjectStore``,
``SqliteCaptureRecordStore``, ``SqliteContentObjectStore``, ``CaptureIntake``,
``TextProcessor``, ``WebpageProcessor``, ``ProcessorRouter``, and
``ProcessingOrchestrator`` against a temporary directory, and every assertion is
made against what is on disk or what came back over the wire.

This is the file that has to be true for Phase 2 PR 1 to mean anything:

    HTML submitted through the existing HTTP endpoint becomes a durable
    canonical web ``ContentObject``, while the exact original HTML remains
    immutable and retrievable.

:class:`TestSurvivingARestart` is where durability stops being a claim: the whole
application is thrown away and rebuilt over the same directory.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.contracts.enums import AssetRole, ProvenanceSourceType, SegmentType
from core.processing import TextProcessor, WebpageProcessor
from tests.unit.api.builders import (
    HTML_PAGE,
    HTML_PAGE_TEXT,
    HTML_PAGE_TITLE,
    WEBPAGE_CAPTURE_ID,
    text_envelope,
    webpage_envelope,
)
from unimem_api import RAW_DIRNAME, build_local_app


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(data_dir)) as running:
        yield running


def raw_path(data_dir: Path, digest: str) -> Path:
    """Where ``LocalRawObjectStore`` keeps one object, addressed by its digest."""
    return data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest


def record_of(client: TestClient, capture_id: str = WEBPAGE_CAPTURE_ID) -> CaptureRecord:
    return CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())


def content_of(client: TestClient, capture_id: str = WEBPAGE_CAPTURE_ID) -> ContentObject:
    return ContentObject.model_validate(client.get(f"/v1/captures/{capture_id}/content").json())


class TestTheRealSmokeTest:
    """One representative page, and every claim this PR makes about it."""

    @pytest.fixture(autouse=True)
    def submitted(self, client: TestClient) -> None:
        response = client.post("/v1/captures", json=webpage_envelope())
        assert response.status_code == 201, response.text

    def test_the_post_reports_complete(self, client: TestClient) -> None:
        body = client.post("/v1/captures", json=webpage_envelope(id="cap_smoke_02")).json()

        assert body["status"] == "complete"
        assert body["capture_id"] == "cap_smoke_02"

    def test_the_capture_record_is_complete(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_content_object_is_a_web_object(self, client: TestClient) -> None:
        assert content_of(client).type is ContentType.WEB

    def test_the_title_comes_from_the_html(self, client: TestClient) -> None:
        assert content_of(client).title == HTML_PAGE_TITLE
        assert content_of(client).title == "Example & Test"

    def test_the_segment_holds_the_extracted_page_text(self, client: TestClient) -> None:
        (segment,) = content_of(client).segments

        assert segment.type is SegmentType.TEXT
        assert segment.position == 0
        assert segment.text == HTML_PAGE_TEXT

    def test_the_segment_contains_each_piece_of_prose(self, client: TestClient) -> None:
        """Every semantic piece of the page, named exactly.

        The third assertion is the one that matters and the one that is easy to
        get wrong: ``"paragraph." in text`` would pass on the strength of the
        *first* paragraph alone, and would keep passing if the ``<script>``
        before the second paragraph swallowed everything after it. It is written
        as the full string, with the ``&nbsp;`` as the U+00A0 it decodes to.
        """
        (segment,) = content_of(client).segments
        assert segment.text is not None

        assert "Hello" in segment.text
        assert "First paragraph." in segment.text
        assert "Second\u00a0paragraph." in segment.text

    def test_content_after_the_script_survives(self, client: TestClient) -> None:
        """The page's second paragraph sits *after* an ignored ``<script>``.

        An ignored subtree must end at its own end tag and never consume the
        siblings that follow it.
        """
        (segment,) = content_of(client).segments
        assert segment.text is not None

        assert segment.text.count("paragraph.") == 2
        assert segment.text.endswith("Second\u00a0paragraph.")

    def test_the_nbsp_is_not_collapsed_to_a_plain_space(self, client: TestClient) -> None:
        """Entity meaning is preserved end to end, over real HTTP."""
        (segment,) = content_of(client).segments
        assert segment.text is not None

        assert "Second\u00a0paragraph." in segment.text
        assert "Second paragraph." not in segment.text

    def test_the_segment_excludes_javascript_and_css(self, client: TestClient) -> None:
        (segment,) = content_of(client).segments
        assert segment.text is not None

        for excluded in ("window.secret", "not content", "display: none", ".x {", "<script"):
            assert excluded not in segment.text

    def test_the_raw_bytes_on_disk_are_the_submitted_html_exactly(
        self, client: TestClient, data_dir: Path
    ) -> None:
        record = record_of(client)
        assert record.raw_object is not None
        digest = record.raw_object.sha256
        assert digest is not None

        assert raw_path(data_dir, digest).read_bytes() == HTML_PAGE.encode("utf-8")

    def test_the_original_asset_addresses_those_bytes(self, client: TestClient) -> None:
        content = content_of(client)
        (asset,) = content.assets
        record = record_of(client)

        assert asset.role is AssetRole.ORIGINAL
        assert record.raw_object is not None
        assert asset.ref == record.raw_object.ref
        assert asset.sha256 == record.raw_object.sha256
        assert content.original.asset_id == asset.id

    def test_the_provenance_names_html_and_the_webpage_processor(self, client: TestClient) -> None:
        (segment,) = content_of(client).segments

        assert segment.provenance.source_type is ProvenanceSourceType.HTML
        assert segment.provenance.processor == WebpageProcessor.name
        assert segment.provenance.processor_version == WebpageProcessor.version

    def test_the_submitted_source_survives(self, client: TestClient) -> None:
        content = content_of(client)

        assert content.source.provider == "chromium"
        assert content.source.url == "https://example.com/article"

    def test_the_url_was_never_fetched(self, client: TestClient) -> None:
        """``source.url`` is metadata. The only bytes here are the submitted ones.

        There is no HTTP client in the processing path at all, so this asserts
        the observable consequence: the stored original is exactly what was
        posted, and nothing from ``example.com`` appears anywhere.
        """
        content = content_of(client)
        (segment,) = content.segments
        assert segment.text is not None

        assert segment.text == HTML_PAGE_TEXT
        assert "example.com" not in segment.text

    def test_the_capture_metadata_survived(self, client: TestClient) -> None:
        record = record_of(client)

        assert record.context is not None
        assert record.context.device == "laptop"
        assert record.intent is not None
        assert record.intent.tags == ["architecture", "http"]

    def test_the_schema_is_still_0_2(self, client: TestClient) -> None:
        assert record_of(client).schema_version == SCHEMA_VERSION
        assert content_of(client).schema_version == "0.2"


class TestSurvivingARestart:
    """Throw the application away; the webpage capture is still there."""

    @staticmethod
    def submit(data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            assert first.post("/v1/captures", json=webpage_envelope()).status_code == 201

    def test_the_capture_and_its_content_outlive_the_app(self, data_dir: Path) -> None:
        self.submit(data_dir)

        with TestClient(build_local_app(data_dir)) as second:
            assert record_of(second).status is CaptureStatus.COMPLETE
            assert content_of(second).type is ContentType.WEB

    def test_the_content_is_byte_identical_after_a_restart(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            first.post("/v1/captures", json=webpage_envelope())
            before = first.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}/content").text

        with TestClient(build_local_app(data_dir)) as second:
            after = second.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}/content").text

        assert after == before

    def test_the_extracted_text_is_unchanged(self, data_dir: Path) -> None:
        self.submit(data_dir)

        with TestClient(build_local_app(data_dir)) as second:
            (segment,) = content_of(second).segments

        assert segment.text == HTML_PAGE_TEXT

    def test_the_original_html_is_still_exact(self, data_dir: Path) -> None:
        self.submit(data_dir)

        with TestClient(build_local_app(data_dir)) as second:
            record = record_of(second)
        assert record.raw_object is not None
        digest = record.raw_object.sha256
        assert digest is not None

        assert raw_path(data_dir, digest).read_bytes() == HTML_PAGE.encode("utf-8")

    def test_a_fresh_app_still_accepts_new_webpage_captures(self, data_dir: Path) -> None:
        self.submit(data_dir)

        with TestClient(build_local_app(data_dir)) as second:
            response = second.post("/v1/captures", json=webpage_envelope(id="cap_web_after"))

        assert response.status_code == 201


class TestDeduplicationOnDisk:
    def test_two_captures_of_one_page_share_one_file(
        self, client: TestClient, data_dir: Path
    ) -> None:
        first = client.post("/v1/captures", json=webpage_envelope(id="cap_web_a")).json()
        second = client.post("/v1/captures", json=webpage_envelope(id="cap_web_b")).json()

        assert first["content_id"] != second["content_id"]
        objects = [path for path in (data_dir / RAW_DIRNAME).rglob("*") if path.is_file()]
        assert len(objects) == 1

    def test_the_two_content_objects_are_independent(self, client: TestClient) -> None:
        client.post("/v1/captures", json=webpage_envelope(id="cap_web_a"))
        client.post("/v1/captures", json=webpage_envelope(id="cap_web_b"))

        first = content_of(client, "cap_web_a")
        second = content_of(client, "cap_web_b")

        assert first.id != second.id
        assert first.segments[0].id != second.segments[0].id
        assert first.assets[0].id != second.assets[0].id
        assert first.original.sha256 == second.original.sha256

    def test_an_identical_duplicate_id_is_still_a_409(self, client: TestClient) -> None:
        """Completed replay stays TEXT-only in this PR — deliberately."""
        client.post("/v1/captures", json=webpage_envelope())

        second = client.post("/v1/captures", json=webpage_envelope())

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "capture_already_exists"


class TestTitlePrecedenceOverRealHttp:
    def test_a_submitted_title_wins(self, client: TestClient) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"title": "My Own Name For It"}

        client.post("/v1/captures", json=body)

        assert content_of(client).title == "My Own Name For It"
        assert record_of(client).title == "My Own Name For It"

    def test_no_submitted_title_uses_the_html_title(self, client: TestClient) -> None:
        client.post("/v1/captures", json=webpage_envelope())

        assert content_of(client).title == HTML_PAGE_TITLE
        assert record_of(client).title is None


class TestRefusalsOverRealHttp:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"type": "webpage", "html": "<p>hi</p>", "text": "hi"}, id="html+text"),
            pytest.param({"type": "webpage", "text": "extracted"}, id="text-only"),
        ],
    )
    def test_an_unsupported_shape_is_a_safe_422(
        self, client: TestClient, payload: dict[str, str]
    ) -> None:
        response = client.post("/v1/captures", json=webpage_envelope(payload=payload))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").status_code == 404

    def test_a_page_with_nothing_to_extract_is_a_safe_422(self, client: TestClient) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"html": '<script>var s = "s3cr3t"</script>'}

        response = client.post("/v1/captures", json=body)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert "s3cr3t" not in response.text

    def test_that_failure_is_observable_and_truthful(self, client: TestClient) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"html": "<script>var s = 1</script>"}
        client.post("/v1/captures", json=body)

        assert record_of(client).status is CaptureStatus.FAILED
        assert client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}/content").status_code == 404


class TestBothProcessorsAreWired:
    def test_a_text_capture_still_reaches_the_text_processor(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        content = content_of(client, "cap_http_01")

        assert content.type is ContentType.TEXT
        assert [record.processor for record in content.processing] == [TextProcessor.name]

    def test_a_webpage_capture_reaches_the_webpage_processor(self, client: TestClient) -> None:
        client.post("/v1/captures", json=webpage_envelope())

        content = content_of(client)

        assert [record.processor for record in content.processing] == [WebpageProcessor.name]
        assert [record.processor_version for record in content.processing] == ["0.1"]

    def test_both_modalities_coexist_in_one_database(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())
        client.post("/v1/captures", json=webpage_envelope())

        assert content_of(client, "cap_http_01").type is ContentType.TEXT
        assert content_of(client).type is ContentType.WEB

    def test_the_text_processor_version_is_unchanged(self) -> None:
        assert TextProcessor.version == "0.2"


class TestNoNewSurface:
    def test_the_openapi_post_body_is_still_the_canonical_envelope(
        self, client: TestClient
    ) -> None:
        body = client.get("/openapi.json").json()["paths"]["/v1/captures"]["post"]["requestBody"]

        assert body["content"]["application/json"]["schema"]["$ref"].endswith("/CaptureEnvelope")

    def test_only_the_four_routes_are_served(self, client: TestClient) -> None:
        paths = set(client.get("/openapi.json").json()["paths"])

        assert paths == {
            "/health",
            "/v1/captures",
            "/v1/captures/{capture_id}",
            "/v1/captures/{capture_id}/content",
        }
