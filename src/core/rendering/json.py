"""The lossless JSON projection of a canonical content object.

JSON is the machine-readable derived representation: everything the content
object holds — source, original, metadata, segments, assets, derived analysis,
and processing history — survives the round trip. A consumer that needs
provenance, digests, asset references, or exact machine state reads this, or
the ``ContentObject`` itself. It never reads the Markdown projection.

Serialization is Pydantic's own (``model_dump(mode="json")``), not a
hand-maintained field mapping. A field added to a contract therefore appears
here without anyone remembering to add it, and a field that is removed cannot
linger in a mapping table that nobody rechecked.
"""

import json
from typing import Final

from core.contracts import ContentObject

#: Separators that emit the compact form — no space after ``,`` or ``:``.
_COMPACT_SEPARATORS: Final = (",", ":")


class JsonRenderer:
    """Renders a content object as a single-line UTF-8 JSON document.

    The output is the serialized object itself: there is no envelope, wrapper
    key, or rendering metadata around it, so it feeds straight back into
    ``ContentObject.model_validate_json(...)``.

    Determinism is a property of the semantic object, not of how it was built.
    Mapping keys are sorted, so two content objects whose ``metadata`` dicts
    were populated in different orders render identically. List order is *not*
    touched: segment, asset, topic, entity, and processing order is meaningful
    domain state that the content object already fixed, and reordering it here
    would change the object's meaning rather than normalize its spelling.

    Non-ASCII characters are emitted as themselves rather than ``\\uXXXX``
    escapes. The result is a valid JSON string either way; unescaped text is
    the readable one, and the byte-level encoding is the caller's business.

    This is *not* RFC 8785 / JCS canonical JSON, and does not claim to be. It
    is deterministic for the objects this system produces, which is what
    reproducibility needs; a cryptographic canonicalization is a different
    commitment, to make deliberately if signing ever requires it.

    ``NaN`` and the infinities are refused rather than written. Python's JSON
    encoder emits them as bare ``NaN`` / ``Infinity`` tokens, which are not
    JSON and would not survive the round trip; a ``metadata`` float that
    reached a contract through ``JsonValue`` therefore raises ``ValueError``
    here instead of producing a document that only Python can read back.
    """

    name = "json"
    version = "0.1"
    media_type = "application/json"

    def render(self, content: ContentObject) -> str:
        """Serialize the whole content object. The object is only read."""
        return json.dumps(
            content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=_COMPACT_SEPARATORS,
            allow_nan=False,
        )
