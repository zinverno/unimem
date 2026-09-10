"""The request bodies a client actually sends.

Envelopes are built as **plain dictionaries**, not as ``CaptureEnvelope``
instances that are then dumped. That is the point: these tests exercise the HTTP
surface, and a real client posts JSON it assembled itself. Building the model
first and serializing it would test the model's round trip and quietly skip the
thing under test — whether the canonical contract can validate a document that
was never a Python object.
"""

from typing import Any

#: When the user captured, as a client would write it: an RFC 3339 string with
#: an offset. Deliberately not "now", so a test can tell it apart from the
#: server's own clock.
CAPTURED_AT = "2026-01-02T03:04:05+00:00"

#: Text carrying characters, line endings, and edge whitespace that nothing in
#: the pipeline is allowed to tidy up — combining marks, CJK, an astral-plane
#: emoji, a CRLF, a tab, a trailing run of spaces, NBSP, and a BOM mid-string.
AWKWARD_TEXT = (
    "A\u030a vs \u00c5 \u2014 \u4f60\u597d \U0001f30d\r\n\tindented\n\ntrailing   \n\u00a0\ufeffend"
)

CAPTURE_ID = "cap_http_01"

TITLE = "A note posted over HTTP"


def text_envelope(**overrides: Any) -> dict[str, Any]:
    """A valid inline-text envelope as a JSON-ready dictionary."""
    body: dict[str, Any] = {
        "schema_version": "0.2",
        "id": CAPTURE_ID,
        "source": {
            "type": "api",
            "provider": "curl",
            "url": "https://example.com/notes",
        },
        "payload": {
            "type": "text",
            "mime_type": "text/plain",
            "text": AWKWARD_TEXT,
            "title": TITLE,
        },
        "context": {
            "captured_at": CAPTURED_AT,
            "device": "laptop",
            "application": "terminal",
        },
        "intent": {
            "action": "save",
            "collection": "reading",
            "tags": ["architecture", "http"],
        },
    }
    return body | overrides


#: A representative page: a doctype, a ``<title>`` carrying an entity, CSS and
#: JavaScript that must not become content, headings, and inline markup.
HTML_PAGE = """<!doctype html>
<html>
<head>
  <title>Example &amp; Test</title>
  <style>.x { display: none; }</style>
</head>
<body>
  <main>
    <h1>Hello</h1>
    <p>First <strong>paragraph</strong>.</p>
    <script>window.secret = "not content"</script>
    <p>Second&nbsp;paragraph.</p>
  </main>
</body>
</html>
"""

#: What ``HTML_PAGE`` extracts to. The no-break space is an escape on purpose:
#: ``&nbsp;`` is a character the page asked for, so extraction preserves it.
HTML_PAGE_TEXT = "Hello\n\nFirst paragraph.\n\nSecond\u00a0paragraph."

#: The ``<title>`` of ``HTML_PAGE``, with its entity decoded.
HTML_PAGE_TITLE = "Example & Test"

WEBPAGE_CAPTURE_ID = "cap_http_web_01"


def webpage_envelope(**overrides: Any) -> dict[str, Any]:
    """A valid HTML-backed webpage envelope as a JSON-ready dictionary.

    Built as a dictionary for the same reason the text one is: a real client
    posts JSON it assembled itself, and this must validate as a document that
    was never a Python object.
    """
    body = text_envelope(
        id=WEBPAGE_CAPTURE_ID,
        source={
            "type": "browser",
            "provider": "chromium",
            "url": "https://example.com/article",
        },
        payload={"type": "webpage", "mime_type": "text/html", "html": HTML_PAGE},
    )
    return body | overrides


def image_envelope(**overrides: Any) -> dict[str, Any]:
    """A structurally valid envelope naming a capability this build lacks.

    The envelope is *correct*: it satisfies every rule ``CaptureEnvelope``
    imposes, so it passes HTTP validation and reaches intake, which refuses it
    on capability grounds. A later phase will accept this exact document
    unchanged.
    """
    return text_envelope(
        payload={"type": "image", "mime_type": "image/png", "file_ref": "blob://screenshot"},
        **overrides,
    )
