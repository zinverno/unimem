"""The browser connector's whole-page envelope, validated against the contract.

The counterpart of :mod:`test_browser_connector_envelope`, for the second thing
the extension can now capture. The connector is JavaScript and this suite is
Python, so what they share is a file:
``clients/browser-extension/tests/fixtures/browser-webpage-envelopes.json``
holds the inputs a page snapshot arrives as and the envelope
``buildWebpageCaptureEnvelope`` builds from them. The connector's own test
asserts that the builder reproduces that envelope exactly; this one asserts that
the same document is a valid ``CaptureEnvelope`` and survives the whole pipeline
into canonical web content.

That is the entire cross-language coupling. No Node runtime is started here, no
browser is required, and nothing in ``core`` learns that a connector exists —
but neither side can change the shape without the other's tests failing.

**What the fixture's ``html`` is, precisely.** It is a capture-time
serialization of a live document through ``document.documentElement.outerHTML``:
what the DOM looked like when the user chose the menu item, including whatever
page script had already changed. It is *not* the HTTP response body, not "view
source", and not an exact server response. It carries no doctype, because the
element being serialized has none and the connector invents nothing. The
guarantee the server makes is the narrow, true one: the string submitted is the
string preserved.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.contracts import (
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentType,
    IntentAction,
)
from core.contracts.base import SCHEMA_VERSION
from unimem_api import build_local_app

#: The file the connector's own tests are written against.
FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "browser-extension"
    / "tests"
    / "fixtures"
    / "browser-webpage-envelopes.json"
)

#: The name the connector puts on both ``source.provider`` and
#: ``context.application``. Duplicated here on purpose: if the connector renames
#: itself, this test should notice rather than follow along.
CONNECTOR_NAME = "unimem-browser-extension"


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return cases


def case_ids() -> list[str]:
    return [case["name"] for case in load_cases()]


@pytest.fixture(params=load_cases(), ids=case_ids())
def case(request: pytest.FixtureRequest) -> dict[str, Any]:
    fixture_case: dict[str, Any] = request.param
    return fixture_case


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(tmp_path / "data")) as test_client:
        yield test_client


def test_the_fixture_file_exists_where_both_suites_look_for_it() -> None:
    assert FIXTURE_PATH.is_file()


def test_the_fixture_covers_more_than_one_shape() -> None:
    assert len(load_cases()) >= 2


class TestTheEnvelopeIsCanonical:
    """It validates as ``CaptureEnvelope`` with no contract change at all."""

    def test_it_validates(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.id == case["inputs"]["id"]

    def test_it_is_schema_0_2(self, case: dict[str, Any]) -> None:
        """Whole-page capture bumped the *extension* to 0.2.0. The contract did
        not move, and these two numbers have nothing to do with each other."""
        assert case["envelope"]["schema_version"] == SCHEMA_VERSION == "0.2"

    def test_the_source_type_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.type is CaptureSourceType.BROWSER

    def test_the_payload_type_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.type is CapturePayloadType.WEBPAGE

    def test_the_mime_type_is_html(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.mime_type == "text/html"

    def test_the_intent_action_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.intent is not None
        assert envelope.intent.action is IntentAction.SAVE

    def test_the_connector_names_itself_consistently(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.provider == CONNECTOR_NAME
        assert envelope.context.application == CONNECTOR_NAME

    def test_captured_at_is_timezone_aware(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.context.captured_at.tzinfo is not None
        assert envelope.context.captured_at.utcoffset() is not None

    def test_it_carries_no_field_the_contract_forbids(self, case: dict[str, Any]) -> None:
        polluted = {**case["envelope"], "summary": "a derived summary"}

        with pytest.raises(ValidationError, match="summary"):
            CaptureEnvelope.model_validate(polluted)


class TestThePayloadIsHtmlOnly:
    """One submitted original, so intake is never asked to choose between two."""

    def test_the_html_is_the_snapshot_exactly(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.html == case["inputs"]["html"]

    def test_nothing_trimmed_or_reserialized_it(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])
        html = case["inputs"]["html"]

        assert envelope.payload.html is not None
        assert len(envelope.payload.html) == len(html)

    def test_no_doctype_was_invented(self, case: dict[str, Any]) -> None:
        """``document.documentElement`` is an element; its serialization has no
        doctype, and the connector does not add one to make it look like a file."""
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.html is not None
        assert not envelope.payload.html.lstrip().lower().startswith("<!doctype")

    def test_text_is_absent(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.text is None
        assert "text" not in case["envelope"]["payload"]

    def test_file_ref_is_absent(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.file_ref is None
        assert "file_ref" not in case["envelope"]["payload"]

    def test_the_page_url_is_carried_verbatim(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.url == case["inputs"]["url"]

    def test_a_non_blank_tab_title_is_carried_and_a_blank_one_is_absent(
        self, case: dict[str, Any]
    ) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])
        supplied = case["inputs"]["title"]

        if supplied is not None and supplied.strip():
            assert envelope.payload.title == supplied
        else:
            assert envelope.payload.title is None
            assert "title" not in case["envelope"]["payload"]


class TestTheEnvelopeSurvivesTheWholePipeline:
    """Posted over the real HTTP surface, against the real local stack."""

    def test_it_is_accepted_and_completes(self, client: TestClient, case: dict[str, Any]) -> None:
        response = client.post("/v1/captures", json=case["envelope"])

        assert response.status_code == 201
        assert response.json()["capture_id"] == case["envelope"]["id"]
        assert response.json()["status"] == "complete"

    def test_it_leaves_a_complete_webpage_capture_record(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        record = CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())

        assert record.status is CaptureStatus.COMPLETE
        assert record.source.type is CaptureSourceType.BROWSER
        assert record.payload_type is CapturePayloadType.WEBPAGE

    def test_it_becomes_canonical_web_content(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        assert content.type is ContentType.WEB

    def test_the_visible_text_survives_to_canonical_content(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        extracted = "\n".join(segment.text or "" for segment in content.segments)
        for expected in case["visible_text_contains"]:
            assert expected in extracted

    def test_script_and_style_contents_do_not(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        extracted = "\n".join(segment.text or "" for segment in content.segments)
        for forbidden in case["never_in_visible_text"]:
            assert forbidden not in extracted

    def test_the_title_is_the_tab_title_or_the_documents_own(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        """The connector submits the tab's title when it has one; otherwise the
        server's existing precedence rule reads the HTML's ``<title>``. The
        extension never extracts a title itself."""
        capture_id = case["envelope"]["id"]
        supplied = case["inputs"]["title"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        if supplied is not None and supplied.strip():
            assert content.title == supplied
        else:
            assert content.title is not None
            assert content.title in case["inputs"]["html"]

    def test_resending_the_same_webpage_id_is_still_a_conflict(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        """Completed replay is defined for ``TEXT`` only, and this PR did not
        change that. A webpage resent under a used id is ``409``, which the
        connector resolves with one observational GET rather than by guessing."""
        client.post("/v1/captures", json=case["envelope"])

        repeated = client.post("/v1/captures", json=case["envelope"])

        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "capture_already_exists"
