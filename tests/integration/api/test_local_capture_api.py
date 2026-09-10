"""The real thing: HTTP over the real stack, on a real directory.

Nothing here is faked. ``build_local_app`` wires ``LocalRawObjectStore``,
``SqliteCaptureRecordStore``, ``SqliteContentObjectStore``, ``CaptureIntake``,
``TextProcessor``, ``ProcessorRouter``, and ``ProcessingOrchestrator`` against a
temporary directory, and every assertion is made against what is on disk or what
comes back over the wire.

The test that matters most is :class:`TestSurvivingARestart`: it throws the whole
application away and builds a new one over the same directory. If durability is
a claim rather than a fact, that is where it breaks.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.contracts.base import SCHEMA_VERSION
from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import (
    ProcessingOrchestrator,
    ProcessorRouter,
    TextProcessor,
)
from core.storage import LocalRawObjectStore
from tests.unit.api.builders import AWKWARD_TEXT, CAPTURE_ID, CAPTURED_AT, TITLE, text_envelope
from tests.unit.api.conftest import served_paths
from unimem_api import DATABASE_FILENAME, RAW_DIRNAME, build_local_app, create_app


def table_names(database: Path) -> set[str]:
    """Every table in the file, read without going through either store."""
    with sqlite3.connect(database) as connection:
        found = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    connection.close()
    return {str(row[0]) for row in found}


def overwrite_payload(database: Path, statement: str, payload: object, key: str) -> None:
    """Write a payload no reader can validate, straight into the table.

    Damaging the row rather than faking a corrupt store is the point: the error
    that comes back is the real adapter's own, message and database path
    included, so what the response withholds is withheld from the genuine
    article.
    """
    with sqlite3.connect(database) as connection:
        connection.execute(statement, (json.dumps(payload), key))
    connection.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A directory that does *not* exist yet — composition must prepare it."""
    return tmp_path / "workspace" / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(data_dir)) as test_client:
        yield test_client


class TestLocalComposition:
    """The composition root wires the real stack and prepares its workspace."""

    def test_it_creates_the_data_directory(self, data_dir: Path) -> None:
        assert not data_dir.exists()

        build_local_app(data_dir)

        assert data_dir.is_dir()

    def test_an_existing_directory_is_reused_not_replaced(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True)
        (data_dir / "keep-me.txt").write_text("still here")

        build_local_app(data_dir)

        assert (data_dir / "keep-me.txt").read_text() == "still here"

    def test_it_lays_out_the_raw_store_and_the_database(
        self, client: TestClient, data_dir: Path
    ) -> None:
        client.post("/v1/captures", json=text_envelope())

        assert (data_dir / DATABASE_FILENAME).is_file()
        assert (data_dir / RAW_DIRNAME).is_dir()

    def test_both_tables_live_in_one_database_file(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """ADR-010 allows one file, two tables — never a shared transaction."""
        client.post("/v1/captures", json=text_envelope())

        assert {"capture_records", "content_objects"} <= table_names(data_dir / DATABASE_FILENAME)

    def test_the_raw_bytes_are_content_addressed_on_disk(
        self, client: TestClient, data_dir: Path
    ) -> None:
        client.post("/v1/captures", json=text_envelope())
        record = CaptureRecord.model_validate(client.get(f"/v1/captures/{CAPTURE_ID}").json())

        assert record.raw_object is not None
        digest = record.raw_object.sha256
        assert digest is not None
        stored = data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest
        assert stored.read_bytes().decode("utf-8") == AWKWARD_TEXT

    def test_the_wired_processor_is_the_real_text_processor(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )

        assert [record.processor for record in content.processing] == [TextProcessor.name]


class TestTheHappyPathOverRealHttp:
    def test_health(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}

    def test_post_returns_201_with_the_two_ids(self, client: TestClient) -> None:
        response = client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 201
        body = response.json()
        assert body["capture_id"] == CAPTURE_ID
        assert body["status"] == "complete"
        assert body["content_id"]

    def test_get_capture_returns_the_completed_record(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        record = CaptureRecord.model_validate(client.get(f"/v1/captures/{CAPTURE_ID}").json())

        assert record.status is CaptureStatus.COMPLETE
        assert record.title == TITLE
        assert record.context is not None
        assert record.context.captured_at.isoformat() == CAPTURED_AT

    def test_get_content_returns_the_submitted_text_exactly(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )

        assert [segment.text for segment in content.segments] == [AWKWARD_TEXT]

    def test_two_captures_of_identical_text_stay_distinct(self, client: TestClient) -> None:
        """Raw bytes deduplicate; captures and content objects do not."""
        first = client.post("/v1/captures", json=text_envelope(id="cap_one")).json()
        second = client.post("/v1/captures", json=text_envelope(id="cap_two")).json()

        assert first["content_id"] != second["content_id"]

        one = CaptureRecord.model_validate(client.get("/v1/captures/cap_one").json())
        two = CaptureRecord.model_validate(client.get("/v1/captures/cap_two").json())
        assert one.raw_object is not None
        assert two.raw_object is not None
        assert one.raw_object.sha256 == two.raw_object.sha256

    def test_the_same_capture_id_carrying_a_different_request_conflicts(
        self, client: TestClient
    ) -> None:
        """An id is a claim, not proof. Two different requests under one id
        remain a conflict; the *same* request resent is the replay case, and
        lives in ``test_completed_capture_replay.py``."""
        client.post("/v1/captures", json=text_envelope())

        response = client.post(
            "/v1/captures",
            json=text_envelope(
                payload={
                    "type": "text",
                    "mime_type": "text/plain",
                    "text": "not the text the first request carried",
                    "title": TITLE,
                }
            ),
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"

    def test_a_non_text_capture_is_mapped_to_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/captures",
            json=text_envelope(payload={"type": "image", "file_ref": "blob://shot.png"}),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404


class TestSurvivingARestart:
    """Throw the application away; the capture is still there.

    This is the whole claim of Phase 0I made checkable over HTTP: everything up
    to canonical content is durable, and two apps over one directory are
    interchangeable because neither store keeps anything between calls.
    """

    def test_the_capture_and_its_content_outlive_the_app(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            created = first.post("/v1/captures", json=text_envelope()).json()
            record_before = first.get(f"/v1/captures/{CAPTURE_ID}").json()
            content_before = first.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        # A genuinely new application object over the same directory.
        with TestClient(build_local_app(data_dir)) as second:
            record_after = second.get(f"/v1/captures/{CAPTURE_ID}")
            content_after = second.get(f"/v1/captures/{CAPTURE_ID}/content")

        assert record_after.status_code == 200
        assert record_after.json()["status"] == CaptureStatus.COMPLETE.value
        assert record_after.json() == record_before

        assert content_after.status_code == 200
        assert content_after.json() == content_before
        assert content_after.json()["id"] == created["content_id"]

    def test_the_canonical_content_is_byte_identical_after_a_restart(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            first.post("/v1/captures", json=text_envelope())
            before = first.get(f"/v1/captures/{CAPTURE_ID}/content").content

        with TestClient(build_local_app(data_dir)) as second:
            after = second.get(f"/v1/captures/{CAPTURE_ID}/content").content

        assert after == before

    def test_a_conflicting_duplicate_id_still_conflicts_after_a_restart(
        self, data_dir: Path
    ) -> None:
        """Replay is narrow, and a restart does not accidentally widen it.

        A fresh application over the same directory answers a duplicate exactly
        as the first one would: this request is not the request that made the
        capture, so it is still a conflict.
        """
        with TestClient(build_local_app(data_dir)) as first:
            assert first.post("/v1/captures", json=text_envelope()).status_code == 201

        with TestClient(build_local_app(data_dir)) as second:
            conflicting = text_envelope(context={"captured_at": "2026-05-05T05:05:05+00:00"})
            assert second.post("/v1/captures", json=conflicting).status_code == 409

    def test_a_fresh_app_can_still_accept_new_captures(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            first.post("/v1/captures", json=text_envelope(id="cap_before"))

        with TestClient(build_local_app(data_dir)) as second:
            created = second.post("/v1/captures", json=text_envelope(id="cap_after"))
            assert created.status_code == 201
            assert second.get("/v1/captures/cap_before").status_code == 200


class TestCorruptionOverTheRealAdapter:
    """A row this build cannot read back is a safe 500, path and all withheld."""

    def test_a_damaged_record_is_a_safe_500(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as client:
            client.post("/v1/captures", json=text_envelope())

        database = data_dir / DATABASE_FILENAME
        overwrite_payload(
            database,
            "UPDATE capture_records SET payload = ? WHERE id = ?",
            {"not": "a capture record"},
            CAPTURE_ID,
        )

        with TestClient(build_local_app(data_dir)) as client:
            response = client.get(f"/v1/captures/{CAPTURE_ID}")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "data_integrity_error"
        assert str(database) not in response.text
        assert DATABASE_FILENAME not in response.text

    def test_a_damaged_content_row_is_a_safe_500(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as client:
            client.post("/v1/captures", json=text_envelope())

        database = data_dir / DATABASE_FILENAME
        overwrite_payload(
            database,
            "UPDATE content_objects SET payload = ? WHERE capture_id = ?",
            {"not": "a content object"},
            CAPTURE_ID,
        )

        with TestClient(build_local_app(data_dir)) as client:
            response = client.get(f"/v1/captures/{CAPTURE_ID}/content")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "data_integrity_error"
        assert str(database) not in response.text


class TestStorageOutageOverTheRealAdapter:
    """A database the adapter cannot use is a 503 that names nothing."""

    def test_an_unusable_database_is_a_safe_503(self, data_dir: Path) -> None:
        app = build_local_app(data_dir)
        database = data_dir / DATABASE_FILENAME
        # Not a SQLite file any more. The adapter's own error names this path.
        database.write_bytes(b"this is not a sqlite database" * 64)

        with TestClient(app) as client:
            response = client.get(f"/v1/captures/{CAPTURE_ID}")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert str(database) not in response.text
        assert str(data_dir) not in response.text


class TestVersionsAreUnchanged:
    """This PR added a delivery surface and touched no canonical semantics."""

    def test_the_text_processor_is_still_0_2(self) -> None:
        assert (TextProcessor.name, TextProcessor.version) == ("text", "0.2")

    def test_the_canonical_schema_is_still_0_2(self) -> None:
        assert SCHEMA_VERSION == "0.2"

    def test_emitted_documents_carry_0_2(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        record = client.get(f"/v1/captures/{CAPTURE_ID}").json()
        content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert record["schema_version"] == "0.2"
        assert content["schema_version"] == "0.2"

    def test_the_processing_record_names_the_processor_version(self, client: TestClient) -> None:
        client.post("/v1/captures", json=text_envelope())

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )

        assert [record.processor_version for record in content.processing] == ["0.2"]


def test_create_app_and_build_local_app_serve_the_same_routes(data_dir: Path) -> None:
    """The composition root adds no route of its own; it only chooses backends."""
    data_dir.mkdir(parents=True)
    database = data_dir / DATABASE_FILENAME
    raw_store = LocalRawObjectStore(data_dir / RAW_DIRNAME)
    record_store = SqliteCaptureRecordStore(database)
    content_store = SqliteContentObjectStore(database)
    manual = create_app(
        intake=CaptureIntake(raw_store, record_store),
        orchestrator=ProcessingOrchestrator(
            ProcessorRouter([TextProcessor(raw_store)]), record_store, content_store
        ),
        record_store=record_store,
        content_store=content_store,
    )

    composed = build_local_app(data_dir)

    assert served_paths(manual) == served_paths(composed)
