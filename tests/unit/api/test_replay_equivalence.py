"""The replay predicate on its own: what counts as *the same request*.

:func:`~unimem_api.replay.is_equivalent_text_replay` is the only thing standing
between a lost response and somebody else's content, so it is tested directly
rather than only through the route. Each case builds the durable record the way
intake would — same fields, same raw digest over the same UTF-8 bytes — and then
changes exactly one thing.

The bias throughout is toward refusing. A false negative costs a client one
spurious ``409`` on a duplicate that was a conflict before this phase existed. A
false positive reports a capture as saved that is not the capture the user took.

Nothing here normalizes. Whitespace, line endings, Unicode composition, and byte
order marks all survive the pipeline unchanged, so all of them make two texts
different — and this suite says so case by case, because "the digest handles it"
is exactly the kind of claim that quietly stops being true.
"""

from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CaptureIntent,
    CapturePayload,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    IntentAction,
    RawObjectRef,
)
from unimem_api.replay import is_equivalent_text_replay

CAPTURE_ID = "cap_replay_01"
TEXT = "  the exact selection  \r\n\ttabbed\n"
MIME = "text/plain"
TITLE = "A page title"
CAPTURED_AT = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 1, 2, 3, 4, 9, tzinfo=UTC)


def digest_of(text: str) -> str:
    """The digest intake would have stored: UTF-8 bytes of the text, untouched."""
    return sha256(text.encode("utf-8")).hexdigest()


def envelope(**overrides: Any) -> CaptureEnvelope:
    fields: dict[str, Any] = {
        "schema_version": "0.2",
        "id": CAPTURE_ID,
        "source": CaptureSource(
            type=CaptureSourceType.BROWSER,
            provider="unimem-browser-extension",
            url="https://example.com/a",
        ),
        "payload": CapturePayload(
            type=CapturePayloadType.TEXT, mime_type=MIME, text=TEXT, title=TITLE
        ),
        "context": CaptureContext(captured_at=CAPTURED_AT, application="unimem-browser-extension"),
        "intent": CaptureIntent(action=IntentAction.SAVE, tags=["reading", "http"]),
    }
    return CaptureEnvelope(**(fields | overrides))


def record_for(source: CaptureEnvelope, **overrides: Any) -> CaptureRecord:
    """The ``COMPLETE`` record intake and the orchestrator would have left behind."""
    text = source.payload.text or ""
    fields: dict[str, Any] = {
        "schema_version": source.schema_version,
        "id": source.id,
        "status": CaptureStatus.COMPLETE,
        "received_at": RECEIVED_AT,
        "updated_at": RECEIVED_AT,
        "source": source.source.model_copy(deep=True),
        "payload_type": source.payload.type,
        "raw_object": RawObjectRef(
            id=digest_of(text),
            mime_type=source.payload.mime_type,
            sha256=digest_of(text),
            ref=f"sha256/{digest_of(text)}",
        ),
        "context": source.context.model_copy(deep=True),
        "intent": None if source.intent is None else source.intent.model_copy(deep=True),
        "title": source.payload.title,
    }
    return CaptureRecord(**(fields | overrides))


def test_an_exact_resubmission_matches() -> None:
    submitted = envelope()

    assert is_equivalent_text_replay(submitted, record_for(submitted))


def test_a_freshly_built_identical_envelope_matches() -> None:
    """Equality is by value, not by object identity: the client rebuilt this."""
    assert is_equivalent_text_replay(envelope(), record_for(envelope()))


class TestTheTextIsCheckedByte:
    """Only the digest speaks for the text, and it speaks for every byte of it."""

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("a leading space", f" {TEXT}"),
            ("a trailing space", f"{TEXT} "),
            ("the leading spaces trimmed", TEXT.strip()),
            ("CRLF collapsed to LF", TEXT.replace("\r\n", "\n")),
            ("LF expanded to CRLF", TEXT.replace("\n", "\r\n")),
            ("a trailing newline added", f"{TEXT}\n"),
            ("a tab turned into spaces", TEXT.replace("\t", "    ")),
            ("a byte order mark prefixed", f"﻿{TEXT}"),
            ("a byte order mark appended", f"{TEXT}﻿"),
            ("empty instead of the text", "x"),
        ],
        ids=lambda value: value if isinstance(value, str) and " " in value else "",
    )
    def test_a_changed_text_refuses_replay(self, label: str, text: str) -> None:
        stored = record_for(envelope())
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT, mime_type=MIME, text=text, title=TITLE
            )
        )

        assert not is_equivalent_text_replay(resent, stored)

    def test_unicode_composition_differences_refuse_replay(self) -> None:
        """``A`` + combining ring and ``Å`` look identical and are not the same
        bytes. The pipeline stores what was submitted, so the digest disagrees
        — and nothing here normalizes to make them agree."""
        decomposed = "Ångstrom"
        composed = "Ångstrom"
        assert decomposed != composed

        stored = record_for(
            envelope(
                payload=CapturePayload(
                    type=CapturePayloadType.TEXT, mime_type=MIME, text=decomposed, title=TITLE
                )
            )
        )
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT, mime_type=MIME, text=composed, title=TITLE
            )
        )

        assert not is_equivalent_text_replay(resent, stored)

    def test_a_digest_that_does_not_match_the_text_refuses_replay(self) -> None:
        """The stored digest is the authority, not the record's other fields."""
        submitted = envelope()
        stored = record_for(
            submitted,
            raw_object=RawObjectRef(
                id="x", mime_type=MIME, sha256=digest_of("something else"), ref="sha256/x"
            ),
        )

        assert not is_equivalent_text_replay(submitted, stored)

    def test_a_record_with_no_raw_reference_refuses_replay(self) -> None:
        """Nothing proves the text, so nothing is replayed — however complete
        the record claims to be."""
        submitted = envelope()

        assert not is_equivalent_text_replay(submitted, record_for(submitted, raw_object=None))

    def test_a_text_payload_that_lost_its_text_refuses_replay(self) -> None:
        """The one way an envelope can claim ``text`` and carry none.

        ``CapturePayload`` requires text for a text payload, so this state is
        unreachable by construction — but the contracts document that a model
        level validator raises *after* the assignment has been written, so a
        caller who catches the error is left holding exactly this object. Intake
        refuses it in its own vocabulary; replay refuses it in this one, rather
        than hashing an empty string and comparing that to a real digest.
        """
        resent = envelope()
        with pytest.raises(ValidationError):
            resent.payload.text = None
        assert resent.payload.text is None

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_a_raw_reference_with_no_digest_refuses_replay(self) -> None:
        submitted = envelope()
        stored = record_for(
            submitted, raw_object=RawObjectRef(id="x", mime_type=MIME, sha256=None, ref="x")
        )

        assert not is_equivalent_text_replay(submitted, stored)


class TestTheRequestMetadata:
    """Every fact the durable record carries has to agree."""

    def test_a_different_capture_id_refuses_replay(self) -> None:
        assert not is_equivalent_text_replay(envelope(id="cap_other"), record_for(envelope()))

    def test_a_different_schema_version_refuses_replay(self) -> None:
        stored = record_for(envelope())

        assert not is_equivalent_text_replay(envelope(schema_version="0.1"), stored)

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "a different url",
                CaptureSource(
                    type=CaptureSourceType.BROWSER,
                    provider="unimem-browser-extension",
                    url="https://example.com/b",
                ),
            ),
            (
                "no url at all",
                CaptureSource(type=CaptureSourceType.BROWSER, provider="unimem-browser-extension"),
            ),
            (
                "a different provider",
                CaptureSource(
                    type=CaptureSourceType.BROWSER,
                    provider="something-else",
                    url="https://example.com/a",
                ),
            ),
            (
                "no provider",
                CaptureSource(type=CaptureSourceType.BROWSER, url="https://example.com/a"),
            ),
            (
                "a different source type",
                CaptureSource(
                    type=CaptureSourceType.API,
                    provider="unimem-browser-extension",
                    url="https://example.com/a",
                ),
            ),
        ],
        ids=["different url", "no url", "different provider", "no provider", "different type"],
    )
    def test_changed_source_metadata_refuses_replay(
        self, label: str, source: CaptureSource
    ) -> None:
        assert not is_equivalent_text_replay(envelope(source=source), record_for(envelope()))

    @pytest.mark.parametrize(
        ("label", "context"),
        [
            (
                "a later capture time",
                CaptureContext(
                    captured_at=CAPTURED_AT + timedelta(seconds=1),
                    application="unimem-browser-extension",
                ),
            ),
            (
                "a sub-second difference",
                CaptureContext(
                    captured_at=CAPTURED_AT + timedelta(milliseconds=1),
                    application="unimem-browser-extension",
                ),
            ),
            (
                "a different application",
                CaptureContext(captured_at=CAPTURED_AT, application="something-else"),
            ),
            (
                "an added device",
                CaptureContext(
                    captured_at=CAPTURED_AT, application="unimem-browser-extension", device="laptop"
                ),
            ),
            ("no application", CaptureContext(captured_at=CAPTURED_AT)),
        ],
        ids=["later time", "sub-second", "different app", "added device", "no app"],
    )
    def test_changed_context_refuses_replay(self, label: str, context: CaptureContext) -> None:
        assert not is_equivalent_text_replay(envelope(context=context), record_for(envelope()))

    def test_the_same_instant_in_another_offset_still_matches(self) -> None:
        """``captured_at`` is compared as a moment in time, not as a string.

        These two are the same instant written two ways, and the contract's own
        datetime equality says so. Building replay identity around the JSON text
        instead would refuse a client that formatted its own timestamp
        differently on the resend — a formatting difference, not a capture
        difference.
        """
        elsewhere = CAPTURED_AT.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert elsewhere.isoformat() != CAPTURED_AT.isoformat()
        assert elsewhere == CAPTURED_AT

        resent = envelope(
            context=CaptureContext(captured_at=elsewhere, application="unimem-browser-extension")
        )

        assert is_equivalent_text_replay(resent, record_for(envelope()))

    @pytest.mark.parametrize(
        ("label", "intent"),
        [
            ("no intent where there was one", None),
            (
                "a different action",
                CaptureIntent(action=IntentAction.ANALYZE, tags=["reading", "http"]),
            ),
            ("no action", CaptureIntent(tags=["reading", "http"])),
            ("a dropped tag", CaptureIntent(action=IntentAction.SAVE, tags=["reading"])),
            (
                "an added tag",
                CaptureIntent(action=IntentAction.SAVE, tags=["reading", "http", "new"]),
            ),
            ("reordered tags", CaptureIntent(action=IntentAction.SAVE, tags=["http", "reading"])),
            (
                "an added collection",
                CaptureIntent(
                    action=IntentAction.SAVE, collection="inbox", tags=["reading", "http"]
                ),
            ),
        ],
        ids=["none", "action", "no action", "dropped tag", "added tag", "reordered", "collection"],
    )
    def test_changed_intent_refuses_replay(self, label: str, intent: CaptureIntent | None) -> None:
        assert not is_equivalent_text_replay(envelope(intent=intent), record_for(envelope()))

    def test_an_intent_appearing_where_there_was_none_refuses_replay(self) -> None:
        stored = record_for(envelope(intent=None))

        assert not is_equivalent_text_replay(envelope(), stored)

    def test_a_capture_that_never_had_an_intent_still_replays(self) -> None:
        submitted = envelope(intent=None)

        assert is_equivalent_text_replay(submitted, record_for(submitted))

    @pytest.mark.parametrize(
        ("label", "title"),
        [("a different title", "Another page"), ("no title", None)],
        ids=["different", "none"],
    )
    def test_a_changed_title_refuses_replay(self, label: str, title: str | None) -> None:
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT, mime_type=MIME, text=TEXT, title=title
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_a_title_appearing_where_there_was_none_refuses_replay(self) -> None:
        untitled = envelope(
            payload=CapturePayload(type=CapturePayloadType.TEXT, mime_type=MIME, text=TEXT)
        )

        assert not is_equivalent_text_replay(envelope(), record_for(untitled))

    def test_a_capture_that_never_had_a_title_still_replays(self) -> None:
        untitled = envelope(
            payload=CapturePayload(type=CapturePayloadType.TEXT, mime_type=MIME, text=TEXT)
        )

        assert is_equivalent_text_replay(untitled, record_for(untitled))

    @pytest.mark.parametrize(
        ("label", "mime_type"),
        [("a different mime type", "text/markdown"), ("no mime type", None)],
        ids=["different", "none"],
    )
    def test_a_changed_mime_type_refuses_replay(self, label: str, mime_type: str | None) -> None:
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT, mime_type=mime_type, text=TEXT, title=TITLE
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_a_mime_type_appearing_where_there_was_none_refuses_replay(self) -> None:
        plain = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT, mime_type=None, text=TEXT, title=TITLE
            )
        )

        assert not is_equivalent_text_replay(envelope(), record_for(plain))


class TestFactsTheRecordCannotVouchFor:
    """Absence of proof refuses replay, rather than being read as proof of absence."""

    def test_an_added_html_rendering_refuses_replay(self) -> None:
        """A text ``CaptureRecord`` does not represent ``html``, so it cannot say
        whether the original request carried one. Unprovable is not equivalent."""
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT,
                mime_type=MIME,
                text=TEXT,
                html="<p>the exact selection</p>",
                title=TITLE,
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_an_added_file_reference_refuses_replay(self) -> None:
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT,
                mime_type=MIME,
                text=TEXT,
                file_ref="blob://elsewhere",
                title=TITLE,
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_both_at_once_refuses_replay(self) -> None:
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT,
                mime_type=MIME,
                text=TEXT,
                html="<p>x</p>",
                file_ref="blob://x",
                title=TITLE,
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_the_text_matching_does_not_rescue_an_extra_field(self) -> None:
        """The digest agrees and the answer is still no: the digest speaks for
        the text and for nothing else."""
        stored = record_for(envelope())
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.TEXT,
                mime_type=MIME,
                text=TEXT,
                html="<p>anything</p>",
                title=TITLE,
            )
        )
        assert stored.raw_object is not None
        assert stored.raw_object.sha256 == digest_of(resent.payload.text or "")

        assert not is_equivalent_text_replay(resent, stored)


class TestOnlyTheSupportedMaterialization:
    """Replay is defined for inline text, and claims nothing about anything else."""

    def test_a_non_text_payload_refuses_replay(self) -> None:
        resent = envelope(
            payload=CapturePayload(
                type=CapturePayloadType.IMAGE, mime_type="image/png", file_ref="blob://shot"
            )
        )

        assert not is_equivalent_text_replay(resent, record_for(envelope()))

    def test_a_record_of_another_payload_type_refuses_replay(self) -> None:
        stored = record_for(envelope(), payload_type=CapturePayloadType.WEBPAGE)

        assert not is_equivalent_text_replay(envelope(), stored)


class TestLifecycleFactsAreNotRequestIdentity:
    """Server-generated timestamps are not part of what the client submitted."""

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            (
                "a later receipt",
                {
                    "received_at": RECEIVED_AT + timedelta(hours=3),
                    "updated_at": RECEIVED_AT + timedelta(hours=4),
                },
            ),
            ("no update timestamp", {"updated_at": None}),
        ],
        ids=["later receipt", "no updated_at"],
    )
    def test_they_do_not_affect_equivalence(self, label: str, overrides: dict[str, Any]) -> None:
        submitted = envelope()

        assert is_equivalent_text_replay(submitted, record_for(submitted, **overrides))

    def test_the_predicate_does_not_look_at_status(self) -> None:
        """Whether the capture is *complete* is a separate question, asked by
        :func:`~unimem_api.replay.resolve_completed_replay` before this runs.
        Folding it in here would give one predicate two jobs and make the
        content-store ordering guarantee harder to see."""
        submitted = envelope()

        assert is_equivalent_text_replay(
            submitted, record_for(submitted, status=CaptureStatus.PROCESSING)
        )
