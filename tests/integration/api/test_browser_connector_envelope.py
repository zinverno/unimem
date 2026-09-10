"""The browser connector's envelope, validated against the real contract.

The connector is JavaScript and this suite is Python, so the thing they share is
a file: ``clients/browser-extension/tests/fixtures/browser-envelopes.json``
holds the inputs a selection arrives as and the envelope the connector builds
from them. The connector's own test asserts that ``buildCaptureEnvelope``
reproduces that envelope exactly; this one asserts that the same document is a
valid ``CaptureEnvelope`` and survives the whole pipeline.

That is the entire cross-language coupling. No Node runtime is started here, no
browser is required, and nothing in ``core`` learns that a connector exists —
but neither side can change the shape without the other's tests failing, which
is the property worth having.
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
    / "browser-envelopes.json"
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
        assert case["envelope"]["schema_version"] == SCHEMA_VERSION == "0.2"

    def test_the_source_type_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.type is CaptureSourceType.BROWSER

    def test_the_payload_type_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.type is CapturePayloadType.TEXT

    def test_the_intent_action_is_an_existing_enum_member(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.intent is not None
        assert envelope.intent.action is IntentAction.SAVE

    def test_the_connector_names_itself_consistently(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.provider == CONNECTOR_NAME
        assert envelope.context.application == CONNECTOR_NAME

    def test_captured_at_is_timezone_aware(self, case: dict[str, Any]) -> None:
        """The connector sends a `Z`-suffixed UTC instant; `AwareDatetime` takes it."""
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.context.captured_at.tzinfo is not None
        assert envelope.context.captured_at.utcoffset() is not None

    def test_it_carries_no_field_the_contract_forbids(self, case: dict[str, Any]) -> None:
        """``extra="forbid"`` is what would catch a connector inventing a field."""
        polluted = {**case["envelope"], "summary": "a derived summary"}

        with pytest.raises(ValidationError, match="summary"):
            CaptureEnvelope.model_validate(polluted)


class TestTheSelectionSurvivesValidation:
    """What the page returned is what the contract holds."""

    def test_the_text_is_the_selection_exactly(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.payload.text == case["inputs"]["selection"]

    def test_nothing_trimmed_it(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])
        selection = case["inputs"]["selection"]

        assert envelope.payload.text is not None
        assert len(envelope.payload.text) == len(selection)

    def test_the_page_url_is_carried_verbatim(self, case: dict[str, Any]) -> None:
        envelope = CaptureEnvelope.model_validate(case["envelope"])

        assert envelope.source.url == case["inputs"]["url"]

    def test_a_non_blank_title_is_carried_and_a_blank_one_is_absent(
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

    def test_it_leaves_a_complete_capture_record(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        record = CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())

        assert record.status is CaptureStatus.COMPLETE
        assert record.source.type is CaptureSourceType.BROWSER

    def test_the_selection_survives_to_canonical_content(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        assert [segment.text for segment in content.segments] == [case["inputs"]["selection"]]

    def test_the_page_title_survives_to_canonical_content(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        capture_id = case["envelope"]["id"]
        supplied = case["inputs"]["title"]
        client.post("/v1/captures", json=case["envelope"])

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{capture_id}/content").json()
        )

        expected = supplied if supplied is not None and supplied.strip() else None
        assert content.title == expected

    def test_a_second_click_carrying_different_text_is_a_conflict(
        self, client: TestClient, case: dict[str, Any]
    ) -> None:
        """The connector mints a fresh id per click, so this cannot happen — but
        if it ever did, a *different* selection under a used id is refused
        rather than answered with the first click's content.

        The other shape of second POST — the identical envelope, resent because
        the connector never saw the reply — is the completed replay this phase
        added, and it is proven end to end in ``test_completed_capture_replay``.
        """
        client.post("/v1/captures", json=case["envelope"])

        conflicting = json.loads(json.dumps(case["envelope"]))
        conflicting["payload"]["text"] = f"{conflicting['payload']['text']} and more"

        repeated = client.post("/v1/captures", json=conflicting)

        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "capture_already_exists"
