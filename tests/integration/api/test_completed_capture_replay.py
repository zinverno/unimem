"""The lost response, end to end, on a real directory.

This is the acceptance test for the requirement the browser connector actually
produced. Nothing is faked below the HTTP client: ``build_local_app`` wires the
genuine ``LocalRawObjectStore``, ``SqliteCaptureRecordStore``,
``SqliteContentObjectStore``, ``CaptureIntake``, ``TextProcessor``, and
``ProcessingOrchestrator`` over a temporary directory, and the envelope is the
one the connector's own suite is written against — the same fixture file, so
neither side can drift.

The scenario is the one that happens in the field:

1. the extension POSTs a capture and the server completes it;
2. the reply is lost — the client learns nothing;
3. the extension resends the identical envelope, same id;
4. the server answers ``200`` with the result the first attempt produced.

The assertions that matter are made against the database and the raw store, not
against the API's account of itself: **one capture row, one content row, one
raw object, and text nobody touched.** A replay that returned the right JSON
while quietly capturing the selection twice would be the exact failure this
whole design exists to avoid, and only counting rows catches it.

:class:`TestThroughTheConnectorItself` closes the loop by running the
connector's own JavaScript against a live server, with the first response
genuinely thrown away after the server processed it.

That class needs two things this repository cannot assume of every machine: a
``node`` runtime, and the connector's one fixed port. On a laptop running only
the Python suite, missing either is a skip — nothing is gained by failing a
developer's test run over a runtime their change does not touch. In CI it must
be the opposite, because a test that silently skips proves nothing and a green
run would be a lie. ``UNIMEM_REQUIRE_CONNECTOR_INTEGRATION`` is what makes the
difference: set it, and every reason this test could be skipped becomes a
failure instead. The dedicated CI job sets it.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from _pytest.outcomes import Failed, Skipped
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from tests.integration.api.connector_support import (
    CONNECTOR_PORT,
    REPO_ROOT,
    REQUIRE_CONNECTOR_INTEGRATION_ENV,
    connector_integration_required,
    connector_module,
    feedback_for,
    raw_files,
    read_json,
    require_node,
    row_count,
    run_driver,
    serve,
    unavailable,
)
from tests.integration.api.test_browser_connector_envelope import (
    FIXTURE_PATH,
    load_cases,
)
from unimem_api import DATABASE_FILENAME, build_local_app


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(data_dir)) as test_client:
        yield test_client


@pytest.fixture
def envelope() -> dict[str, Any]:
    """The browser connector's own envelope, read from the shared fixture."""
    case: dict[str, Any] = load_cases()[0]
    built: dict[str, Any] = case["envelope"]
    return built


class TestTheLostResponse:
    """The whole requirement, in one sequence."""

    def test_the_first_submission_creates_and_completes_the_capture(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        response = client.post("/v1/captures", json=envelope)

        assert response.status_code == 201
        assert response.json()["status"] == CaptureStatus.COMPLETE.value

    def test_resending_the_identical_envelope_is_answered_with_200(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        created = client.post("/v1/captures", json=envelope)
        assert created.status_code == 201

        # The client never saw that reply. It resends what it still holds.
        replayed = client.post("/v1/captures", json=envelope)

        assert replayed.status_code == 200
        assert replayed.json() == created.json()

    def test_the_replay_returns_the_content_the_first_attempt_made(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        created = client.post("/v1/captures", json=envelope).json()

        replayed = client.post("/v1/captures", json=envelope).json()

        assert replayed["content_id"] == created["content_id"]
        assert replayed["capture_id"] == envelope["id"]

    def test_the_capture_reads_complete_afterwards(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        client.post("/v1/captures", json=envelope)
        client.post("/v1/captures", json=envelope)

        record = CaptureRecord.model_validate(client.get(f"/v1/captures/{envelope['id']}").json())

        assert record.status is CaptureStatus.COMPLETE

    def test_the_content_object_is_the_same_object(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        client.post("/v1/captures", json=envelope)
        before = client.get(f"/v1/captures/{envelope['id']}/content").content

        replayed = client.post("/v1/captures", json=envelope).json()
        after = client.get(f"/v1/captures/{envelope['id']}/content")

        assert after.content == before
        assert ContentObject.model_validate(after.json()).id == replayed["content_id"]

    def test_the_capture_record_is_byte_identical_across_the_replay(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        """No status change, no ``updated_at`` nudge, no new raw reference."""
        client.post("/v1/captures", json=envelope)
        before = client.get(f"/v1/captures/{envelope['id']}").content

        client.post("/v1/captures", json=envelope)

        assert client.get(f"/v1/captures/{envelope['id']}").content == before

    def test_exactly_one_capture_row_and_one_content_row_exist(
        self, client: TestClient, data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        client.post("/v1/captures", json=envelope)
        client.post("/v1/captures", json=envelope)
        client.post("/v1/captures", json=envelope)

        database = data_dir / DATABASE_FILENAME
        assert row_count(database, "capture_records", envelope["id"], "id") == 1
        assert row_count(database, "content_objects", envelope["id"], "capture_id") == 1

    def test_no_second_raw_object_is_written(
        self, client: TestClient, data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        client.post("/v1/captures", json=envelope)
        before = {path: path.read_bytes() for path in raw_files(data_dir)}
        assert len(before) == 1

        client.post("/v1/captures", json=envelope)

        assert {path: path.read_bytes() for path in raw_files(data_dir)} == before

    def test_the_stored_text_is_still_exactly_what_was_selected(
        self, client: TestClient, data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        """The awkward selection — CRLF, tabs, NBSP, a BOM, edge whitespace —
        survives a replay untouched, because a replay writes nothing."""
        client.post("/v1/captures", json=envelope)
        client.post("/v1/captures", json=envelope)

        stored = raw_files(data_dir)[0].read_bytes()
        selected: str = envelope["payload"]["text"]
        assert stored == selected.encode("utf-8")

        content = ContentObject.model_validate(
            client.get(f"/v1/captures/{envelope['id']}/content").json()
        )
        assert [segment.text for segment in content.segments] == [selected]

    def test_the_raw_digest_the_record_names_is_unchanged(
        self, client: TestClient, envelope: dict[str, Any]
    ) -> None:
        client.post("/v1/captures", json=envelope)
        before = CaptureRecord.model_validate(
            client.get(f"/v1/captures/{envelope['id']}").json()
        ).raw_object

        client.post("/v1/captures", json=envelope)

        after = CaptureRecord.model_validate(
            client.get(f"/v1/captures/{envelope['id']}").json()
        ).raw_object
        assert after == before
        assert after is not None
        assert after.sha256 is not None

    def test_it_still_replays_after_the_application_is_thrown_away(
        self, data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        """Replay is a property of the durable capture, not of a live process.

        A service worker that died between attempts, or a server restarted in
        between, changes nothing: the second app reads the same rows and reaches
        the same answer. This is also why no retry state is kept anywhere.
        """
        with TestClient(build_local_app(data_dir)) as first:
            created = first.post("/v1/captures", json=envelope).json()

        with TestClient(build_local_app(data_dir)) as second:
            replayed = second.post("/v1/captures", json=envelope)

        assert replayed.status_code == 200
        assert replayed.json() == created

    def test_a_different_selection_under_the_same_id_is_still_refused(
        self, client: TestClient, data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        """The guarantee is for the same request, and only for the same request."""
        client.post("/v1/captures", json=envelope)

        conflicting = json.loads(json.dumps(envelope))
        conflicting["payload"]["text"] += " plus one more sentence"
        response = client.post("/v1/captures", json=conflicting)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"
        assert len(raw_files(data_dir)) == 1


@pytest.fixture
def live_server(data_dir: Path) -> Iterator[str]:
    """The real application, on a real socket, at the connector's own address."""
    with serve(data_dir) as base_url:
        yield base_url


#: Drives the connector's real `sendCapture` with a `fetch` that loses the first
#: POST's response *after* the server has already handled it — the exact
#: ambiguity the connector cannot otherwise resolve. Everything about the
#: request, the retry, and the reading of the reply is the connector's own code.
CONNECTOR_DRIVER = """
// The client is imported from the repository by absolute path, so this driver
// can live in a scratch directory without a copy of the connector next to it.
const { sendCapture } = await import(process.argv[2]);

const envelope = JSON.parse(process.argv[3]);
const real = globalThis.fetch;
const calls = [];
let posts = 0;

const losingTheFirstReply = async (url, options) => {
  calls.push({ url, method: options?.method ?? "GET" });
  if (options?.method === "POST") {
    posts += 1;
    if (posts === 1) {
      // The server really does receive, process, and complete this capture.
      await real(url, options);
      // ...and then the reply is lost on the way back, exactly as a dropped
      // connection would lose it. The client is left knowing nothing.
      throw new TypeError("Failed to fetch");
    }
  }
  return await real(url, options);
};

const result = await sendCapture(envelope, { fetch: losingTheFirstReply });
console.log(JSON.stringify({ result, calls }));
"""


class TestThroughTheConnectorItself:
    """The connector's own JavaScript, against the real server, over a real socket.

    This is the test the whole PR is for. The first POST is genuinely served and
    its response genuinely thrown away, so the connector faces the real
    ambiguity rather than a simulation of it — and resolves it by resending the
    same envelope under the same id and being handed the capture it already
    made.
    """

    @pytest.fixture(autouse=True)
    def _node_runtime(self) -> None:
        """Refuse the class up front if the connector cannot be run at all.

        Autouse and declared first, so the missing-runtime answer comes before
        the port is probed — and so a prerequisite check cannot be forgotten by
        a test added later. Under
        ``UNIMEM_REQUIRE_CONNECTOR_INTEGRATION`` this is a failure, not a skip.
        """
        require_node()

    @pytest.fixture
    def driven(self, live_server: str, envelope: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
        return run_driver(
            CONNECTOR_DRIVER,
            [connector_module("lib", "api.js"), json.dumps(envelope)],
            tmp_path,
        )

    def test_the_connector_reports_a_complete_capture(self, driven: dict[str, Any]) -> None:
        """The badge the user sees is ``OK``, and it is telling the truth."""
        assert driven["result"]["outcome"] == "complete"
        assert feedback_for(driven["result"]) == "OK"

    def test_it_was_confirmed_by_the_replay_rather_than_by_a_probe(
        self, driven: dict[str, Any]
    ) -> None:
        """A ``200`` from the resend, not a GET afterwards: the server answered
        the question the client was actually asking."""
        assert driven["result"]["confirmedBy"] == "replay"

    def test_it_names_the_capture_and_content_the_server_holds(
        self, driven: dict[str, Any], envelope: dict[str, Any], live_server: str
    ) -> None:
        assert driven["result"]["captureId"] == envelope["id"]

        stored = read_json(f"{live_server}/v1/captures/{envelope['id']}/content")
        assert driven["result"]["contentId"] == stored["id"]

    def test_it_used_exactly_two_posts_and_no_get(self, driven: dict[str, Any]) -> None:
        """The bound is two POSTs and one GET; a resolved replay needs no probe."""
        methods = [call["method"] for call in driven["calls"]]

        assert methods == ["POST", "POST"]

    def test_both_posts_went_to_the_one_constant_endpoint(self, driven: dict[str, Any]) -> None:
        assert {call["url"] for call in driven["calls"]} == {
            f"http://127.0.0.1:{CONNECTOR_PORT}/v1/captures"
        }

    def test_the_server_holds_exactly_one_capture_and_one_content_object(
        self, driven: dict[str, Any], data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        """Two POSTs reached the server. One capture exists."""
        assert driven["result"]["outcome"] == "complete"

        database = data_dir / DATABASE_FILENAME
        assert row_count(database, "capture_records", envelope["id"], "id") == 1
        assert row_count(database, "content_objects", envelope["id"], "capture_id") == 1

    def test_the_selection_was_stored_once_and_unchanged(
        self, driven: dict[str, Any], data_dir: Path, envelope: dict[str, Any]
    ) -> None:
        assert driven["result"]["outcome"] == "complete"

        stored = raw_files(data_dir)
        assert len(stored) == 1
        assert stored[0].read_bytes() == envelope["payload"]["text"].encode("utf-8")

    def test_the_capture_is_durably_complete(
        self, driven: dict[str, Any], live_server: str, envelope: dict[str, Any]
    ) -> None:
        assert driven["result"]["outcome"] == "complete"

        record = read_json(f"{live_server}/v1/captures/{envelope['id']}")
        assert record["status"] == CaptureStatus.COMPLETE.value


def test_the_connector_fixture_is_the_one_both_suites_share() -> None:
    """If the connector changes its envelope, this test changes with it."""
    assert FIXTURE_PATH.is_file()


class TestTheRequiredMode:
    """The skip policy itself, because a wrong one hides a broken acceptance test.

    This is deliberately small: it checks the switch and the two outcomes, and
    builds no framework for testing CI. The point is that "CI cannot silently
    skip this" is a property with a test rather than a comment in a YAML file.
    """

    @pytest.fixture
    def unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(REQUIRE_CONNECTOR_INTEGRATION_ENV, raising=False)

    @pytest.mark.usefixtures("unset")
    def test_it_is_optional_by_default(self) -> None:
        """A developer running only the Python suite is not asked to install node."""
        assert not connector_integration_required()

    @pytest.mark.parametrize("value", ["1", "true", "yes", "required"])
    def test_any_meaningful_value_requires_it(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(REQUIRE_CONNECTOR_INTEGRATION_ENV, value)

        assert connector_integration_required()

    @pytest.mark.parametrize("value", ["", "0"])
    def test_an_off_value_leaves_it_optional(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(REQUIRE_CONNECTOR_INTEGRATION_ENV, value)

        assert not connector_integration_required()

    @pytest.mark.usefixtures("unset")
    def test_a_missing_prerequisite_skips_when_optional(self) -> None:
        with pytest.raises(Skipped) as refused:
            unavailable("node is not available")

        assert "node is not available" in str(refused.value)
        assert REQUIRE_CONNECTOR_INTEGRATION_ENV in str(refused.value)

    def test_a_missing_prerequisite_fails_when_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point: in CI this is a red build, not a quiet skip."""
        monkeypatch.setenv(REQUIRE_CONNECTOR_INTEGRATION_ENV, "1")

        with pytest.raises(Failed) as refused:
            unavailable("port 8765 is already in use")

        assert "port 8765 is already in use" in str(refused.value)
        assert "must run rather than be skipped" in str(refused.value)

    def test_the_two_outcomes_are_genuinely_different(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A skip is not a failure; pytest reports them differently, and so must this."""
        assert not issubclass(Failed, Skipped)
        assert not issubclass(Skipped, Failed)

    def test_the_ci_job_asks_for_the_required_mode(self) -> None:
        """The workflow and this module cannot drift apart on the variable's name.

        Renaming the environment variable without updating the job would leave a
        CI step that looks required and is not — precisely the failure this
        whole mechanism exists to prevent.
        """
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        assert f"{REQUIRE_CONNECTOR_INTEGRATION_ENV}: " in workflow
        assert TestThroughTheConnectorItself.__name__ in workflow
