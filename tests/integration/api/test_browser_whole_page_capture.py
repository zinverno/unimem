"""Whole-page capture, end to end, from the real connector to a real server.

This is the acceptance test Phase 2 PR 2 exists for. Nothing below the page read
is faked:

* the **real** connector flow (``runWholePageCapture``), builder
  (``buildWebpageCaptureEnvelope``), and HTTP client (``sendCapture``), imported
  from ``clients/browser-extension/`` by absolute path and run under ``node``;
* the **real** local application, served by **Uvicorn on a real socket** at the
  connector's own fixed address, over a temporary data directory;
* the **real** ``CaptureIntake``, ``WebpageProcessor``, ``ProcessingOrchestrator``,
  ``LocalRawObjectStore``, and both SQLite stores.

The one substitution is the page read. There is no browser in this process, so
``executeScript`` is a stub that returns a representative
``document.documentElement.outerHTML`` string — the same fixture the connector's
own suite and :mod:`test_browser_webpage_envelope` are written against. That is
the boundary this PR could not automate: **the actual right-click on the toolbar
icon is a manual check, and nothing here claims otherwise.** What is proven is
everything downstream of the snapshot arriving.

Two scenarios run:

:class:`TestWholePageThroughTheConnector`
    the ordinary path — one POST, ``201``, ``OK``, one durable web
    ``ContentObject`` whose original is the exact submitted bytes.

:class:`TestTheWholePageLostResponse`
    the interesting one. The server's completed replay is ``TEXT``-only and this
    PR deliberately did not generalize it, so a webpage resent under an id the
    server already holds comes back ``409``. The connector resolves that with the
    one observational GET its bound already allows, and reports a confirmed
    success only because the GET says ``complete``. Two POSTs and one GET, one
    capture, one content object, one raw original. It proves the generic bounded
    recovery is still correct on a modality the replay path knows nothing about.

Prerequisites — a ``node`` runtime and the connector's one fixed port — obey the
shared policy in :mod:`connector_support`: a skip on a developer's machine, a
failure under ``UNIMEM_REQUIRE_CONNECTOR_INTEGRATION``, which the CI job sets.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.contracts import CapturePayloadType, CaptureStatus, ContentType
from tests.integration.api.connector_support import (
    CONNECTOR_PORT,
    REPO_ROOT,
    REQUIRE_CONNECTOR_INTEGRATION_ENV,
    connector_module,
    feedback_for,
    raw_files,
    read_json,
    require_node,
    row_count,
    run_driver,
    serve,
)
from tests.integration.api.test_browser_webpage_envelope import load_cases
from unimem_api import DATABASE_FILENAME

#: The endpoint the connector will not take from anywhere but its own constant.
CAPTURES_URL = f"http://127.0.0.1:{CONNECTOR_PORT}/v1/captures"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def live_server(data_dir: Path) -> Iterator[str]:
    with serve(data_dir) as base_url:
        yield base_url


@pytest.fixture
def snapshot() -> dict[str, Any]:
    """The representative page snapshot, from the shared connector fixture."""
    case: dict[str, Any] = load_cases()[0]
    return case


@pytest.fixture
def tab(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The tab Chrome would hand the context-menu listener."""
    return {
        "id": 7,
        "url": snapshot["inputs"]["url"],
        "title": snapshot["inputs"]["title"],
    }


#: Drives the connector's real whole-page flow against the live server.
#:
#: Everything about the envelope, the request, and the reading of the reply is
#: the connector's own code, reached through the shipped modules. The fake page
#: read is the only substitution, and it is declared here rather than hidden:
#: `executeScript` returns the snapshot string and nothing else, exactly as
#: `chrome.scripting.executeScript` would after a real menu activation.
WHOLE_PAGE_DRIVER = """
const { runWholePageCapture } = await import(process.argv[2]);
const { sendCapture } = await import(process.argv[3]);

const html = JSON.parse(process.argv[4]);
const tab = JSON.parse(process.argv[5]);
const loseTheFirstReply = process.argv[6] === "lose-first-reply";

const real = globalThis.fetch;
const calls = [];
const envelopes = [];
const reports = [];
let posts = 0;

const observed = async (url, options) => {
  calls.push({ url, method: options?.method ?? "GET" });
  if (loseTheFirstReply && options?.method === "POST") {
    posts += 1;
    if (posts === 1) {
      // The server really does receive, process, and complete this capture...
      await real(url, options);
      // ...and then the reply is lost on the way back, exactly as a dropped
      // connection would lose it. The client is left knowing nothing.
      throw new TypeError("Failed to fetch");
    }
  }
  return await real(url, options);
};

const result = await runWholePageCapture(tab, {
  // The one fake: no browser here, so this stands in for the injected read.
  executeScript: async () => html,
  sendCapture: async (envelope) => {
    envelopes.push(envelope);
    return await sendCapture(envelope, { fetch: observed });
  },
  report: (value) => reports.push(value),
});

console.log(JSON.stringify({ result, reports, envelopes, calls }));
"""


def drive(
    snapshot: dict[str, Any], tab: dict[str, Any], workspace: Path, *, lose: bool
) -> dict[str, Any]:
    arguments = [
        connector_module("lib", "page.js"),
        connector_module("lib", "api.js"),
        json.dumps(snapshot["inputs"]["html"]),
        json.dumps(tab),
        "lose-first-reply" if lose else "keep-every-reply",
    ]
    return run_driver(WHOLE_PAGE_DRIVER, arguments, workspace)


class WholePageAcceptance:
    """What both scenarios share: the runtime check and the submitted envelope."""

    @pytest.fixture(autouse=True)
    def _node_runtime(self) -> None:
        """Refuse the class up front if the connector cannot be run at all.

        Autouse and declared first, so the missing-runtime answer comes before
        the port is probed, and so a prerequisite check cannot be forgotten by a
        test added later.
        """
        require_node()

    @staticmethod
    def submitted(driven: dict[str, Any]) -> dict[str, Any]:
        envelopes: list[dict[str, Any]] = driven["envelopes"]
        assert len(envelopes) == 1, "one gesture is one capture"
        return envelopes[0]

    @staticmethod
    def capture_id(driven: dict[str, Any]) -> str:
        submitted_id: str = WholePageAcceptance.submitted(driven)["id"]
        return submitted_id


class TestWholePageThroughTheConnector(WholePageAcceptance):
    """The ordinary path: a menu activation becomes durable canonical web content."""

    @pytest.fixture
    def driven(
        self, live_server: str, snapshot: dict[str, Any], tab: dict[str, Any], tmp_path: Path
    ) -> dict[str, Any]:
        return drive(snapshot, tab, tmp_path, lose=False)

    def test_the_flow_emits_a_webpage_envelope(self, driven: dict[str, Any]) -> None:
        payload = self.submitted(driven)["payload"]

        assert payload["type"] == CapturePayloadType.WEBPAGE.value
        assert payload["mime_type"] == "text/html"
        assert "text" not in payload
        assert "file_ref" not in payload

    def test_it_is_the_canonical_schema_the_server_already_speaks(
        self, driven: dict[str, Any]
    ) -> None:
        submitted = self.submitted(driven)

        assert submitted["schema_version"] == "0.2"
        assert submitted["source"]["type"] == "browser"
        assert submitted["source"]["provider"] == "unimem-browser-extension"
        assert submitted["intent"] == {"action": "save"}

    def test_it_submits_the_snapshot_exactly_as_the_page_read_returned_it(
        self, driven: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        assert self.submitted(driven)["payload"]["html"] == snapshot["inputs"]["html"]

    def test_one_post_to_the_one_constant_endpoint_answered_created(
        self, driven: dict[str, Any]
    ) -> None:
        """``confirmedBy == "post"`` is only reachable from a ``201``."""
        assert [call["method"] for call in driven["calls"]] == ["POST"]
        assert {call["url"] for call in driven["calls"]} == {CAPTURES_URL}
        assert driven["result"]["confirmedBy"] == "post"

    def test_the_connector_reports_a_complete_capture_and_badges_ok(
        self, driven: dict[str, Any]
    ) -> None:
        """The badge the user sees is ``OK``, and it is telling the truth."""
        assert driven["result"]["outcome"] == "complete"
        assert feedback_for(driven["result"]) == "OK"

    def test_it_showed_busy_first_and_reported_the_result_once(
        self, driven: dict[str, Any]
    ) -> None:
        reports: list[dict[str, Any]] = driven["reports"]

        assert reports[0] == {"busy": True, "kind": "page"}
        assert len(reports) == 2
        assert reports[1] == driven["result"]

    def test_the_capture_record_is_durably_complete(
        self, driven: dict[str, Any], live_server: str
    ) -> None:
        record = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}")

        assert record["status"] == CaptureStatus.COMPLETE.value
        assert record["payload_type"] == CapturePayloadType.WEBPAGE.value

    def test_the_content_object_is_canonical_web_content(
        self, driven: dict[str, Any], live_server: str
    ) -> None:
        content = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}/content")

        assert content["type"] == ContentType.WEB.value
        assert content["id"] == driven["result"]["contentId"]

    def test_the_content_title_is_the_submitted_tab_title(
        self, driven: dict[str, Any], live_server: str, tab: dict[str, Any]
    ) -> None:
        content = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}/content")

        assert content["title"] == tab["title"]

    def test_the_canonical_segment_carries_the_pages_visible_text(
        self, driven: dict[str, Any], live_server: str, snapshot: dict[str, Any]
    ) -> None:
        content = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}/content")

        extracted = "\n".join(segment["text"] or "" for segment in content["segments"])
        for expected in snapshot["visible_text_contains"]:
            assert expected in extracted

    def test_script_and_style_contents_are_excluded(
        self, driven: dict[str, Any], live_server: str, snapshot: dict[str, Any]
    ) -> None:
        content = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}/content")

        extracted = "\n".join(segment["text"] or "" for segment in content["segments"])
        for forbidden in snapshot["never_in_visible_text"]:
            assert forbidden not in extracted

    def test_the_stored_original_is_the_exact_submitted_snapshot(
        self, driven: dict[str, Any], data_dir: Path, snapshot: dict[str, Any]
    ) -> None:
        """Byte for byte, and by digest. The server's "exact raw HTML" invariant
        is about the string *this connector submitted* — not about the page's
        original network response, which nothing here ever saw."""
        html: str = snapshot["inputs"]["html"]
        expected = html.encode("utf-8")

        stored = raw_files(data_dir)
        assert len(stored) == 1
        assert stored[0].read_bytes() == expected

    def test_the_digest_the_record_names_is_that_snapshot_s(
        self, driven: dict[str, Any], live_server: str, snapshot: dict[str, Any]
    ) -> None:
        html: str = snapshot["inputs"]["html"]
        expected = hashlib.sha256(html.encode("utf-8")).hexdigest()

        record = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}")

        assert record["raw_object"]["sha256"] == expected
        assert record["raw_object"]["mime_type"] == "text/html"

    def test_exactly_one_capture_row_and_one_content_row_exist(
        self, driven: dict[str, Any], data_dir: Path
    ) -> None:
        database = data_dir / DATABASE_FILENAME
        capture_id = self.capture_id(driven)

        assert row_count(database, "capture_records", capture_id, "id") == 1
        assert row_count(database, "content_objects", capture_id, "capture_id") == 1


class TestTheWholePageLostResponse(WholePageAcceptance):
    """The lost reply, resolved through ``409`` plus one observational GET.

    Webpage completed replay is intentionally absent, so the second POST is
    refused rather than replayed. That is not a gap being papered over: the
    connector never treats the ``409`` as success, it goes and *looks*, and the
    look is a GET that reads and never writes.
    """

    @pytest.fixture
    def driven(
        self, live_server: str, snapshot: dict[str, Any], tab: dict[str, Any], tmp_path: Path
    ) -> dict[str, Any]:
        return drive(snapshot, tab, tmp_path, lose=True)

    def test_it_used_exactly_two_posts_and_one_get(self, driven: dict[str, Any]) -> None:
        """The hard bound for one user gesture, on the modality with no replay."""
        assert [call["method"] for call in driven["calls"]] == ["POST", "POST", "GET"]

    def test_the_retry_carried_the_identical_envelope(self, driven: dict[str, Any]) -> None:
        """One logical capture: one id, one ``captured_at``, one snapshot. The
        flow builds the envelope once and the client resends that object."""
        assert len(driven["envelopes"]) == 1

    def test_both_posts_went_to_the_one_constant_endpoint(self, driven: dict[str, Any]) -> None:
        posted = [call["url"] for call in driven["calls"] if call["method"] == "POST"]

        assert posted == [CAPTURES_URL, CAPTURES_URL]

    def test_the_get_asked_about_that_same_capture(self, driven: dict[str, Any]) -> None:
        read = [call["url"] for call in driven["calls"] if call["method"] == "GET"]

        assert read == [f"{CAPTURES_URL}/{self.capture_id(driven)}"]

    def test_the_second_post_was_refused_as_a_duplicate_not_replayed(
        self, driven: dict[str, Any], live_server: str, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        """Stated as a fact about the server, not inferred from the connector:
        resubmitting this exact webpage envelope is ``409``."""
        import urllib.error
        import urllib.request

        submitted = self.submitted(driven)
        request = urllib.request.Request(
            CAPTURES_URL,
            data=json.dumps(submitted).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=10)

        assert refused.value.code == 409
        assert json.loads(refused.value.read())["error"]["code"] == "capture_already_exists"

    def test_the_observational_get_found_it_complete(
        self, driven: dict[str, Any], live_server: str
    ) -> None:
        record = read_json(f"{live_server}/v1/captures/{self.capture_id(driven)}")

        assert record["status"] == CaptureStatus.COMPLETE.value

    def test_the_connector_reports_a_confirmed_success(self, driven: dict[str, Any]) -> None:
        assert driven["result"]["outcome"] == "complete"
        assert driven["result"]["confirmedBy"] == "probe"
        assert driven["result"]["probed"] is True
        assert feedback_for(driven["result"]) == "OK"

    def test_durable_state_holds_exactly_one_of_everything(
        self, driven: dict[str, Any], data_dir: Path, snapshot: dict[str, Any]
    ) -> None:
        """Two POSTs reached the server. One capture, one content object, one
        raw original, and the original is the snapshot nobody touched."""
        assert driven["result"]["outcome"] == "complete"

        database = data_dir / DATABASE_FILENAME
        capture_id = self.capture_id(driven)
        assert row_count(database, "capture_records", capture_id, "id") == 1
        assert row_count(database, "content_objects", capture_id, "capture_id") == 1

        stored = raw_files(data_dir)
        assert len(stored) == 1
        assert stored[0].read_bytes() == snapshot["inputs"]["html"].encode("utf-8")


class TestTheTwoFlowsStayApart:
    """Whole-page capture did not repurpose or weaken the selection path."""

    def test_the_selection_fixture_is_still_a_text_capture(self) -> None:
        from tests.integration.api.test_browser_connector_envelope import (
            load_cases as load_selection_cases,
        )

        for case in load_selection_cases():
            assert case["envelope"]["payload"]["type"] == "text"
            assert "html" not in case["envelope"]["payload"]

    def test_the_two_suites_read_different_fixture_files(self) -> None:
        from tests.integration.api.test_browser_connector_envelope import (
            FIXTURE_PATH as SELECTION_FIXTURE,
        )
        from tests.integration.api.test_browser_webpage_envelope import (
            FIXTURE_PATH as WEBPAGE_FIXTURE,
        )

        assert SELECTION_FIXTURE != WEBPAGE_FIXTURE
        assert SELECTION_FIXTURE.is_file()
        assert WEBPAGE_FIXTURE.is_file()

    def test_no_capture_id_is_shared_between_them(self) -> None:
        from tests.integration.api.test_browser_connector_envelope import (
            load_cases as load_selection_cases,
        )

        selection_ids = {case["envelope"]["id"] for case in load_selection_cases()}
        webpage_ids = {case["envelope"]["id"] for case in load_cases()}

        assert not selection_ids & webpage_ids


def test_the_ci_job_runs_both_whole_page_acceptance_classes() -> None:
    """The workflow and this module cannot drift apart.

    Adding an acceptance class the CI job does not select would leave a job that
    looks like it proves whole-page capture and does not — the same failure the
    required mode exists to prevent, one level up.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert f"{REQUIRE_CONNECTOR_INTEGRATION_ENV}: " in workflow
    assert Path(__file__).name in workflow
    assert TestWholePageThroughTheConnector.__name__ in workflow
    assert TestTheWholePageLostResponse.__name__ in workflow
