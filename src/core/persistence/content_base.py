"""The content object store port.

Three operations, synchronous, backend-neutral. A ``ContentObject`` is stored
and retrieved as a whole validated snapshot, exactly as a ``CaptureRecord`` is
(:mod:`core.persistence.base`), and for the same reason: the contract already
defines its own serialization, so there is no second definition to maintain.

There is deliberately no ``replace``, ``save``, ``upsert``, or ``delete``. The
system produces exactly one canonical normalization result per capture and has
no reprocessing, so an operation that overwrote canonical content could only
ever be an accident — and *superseding* canonical content is a real decision
about identity, history, and what already read the old object. It gets made
when reprocessing does, explicitly.
"""

from typing import Protocol

from core.contracts import ContentObject


class ContentObjectStore(Protocol):
    """Durably stores canonical content objects and hands them back.

    Two lookups, because there are two questions worth asking: "give me this
    object" and "what did this capture normalize into?". The second is what
    makes a capture's canonical content reachable from the capture alone —
    including for a capture whose lifecycle record says something a later
    reconciliation pass will want to look into.

    There is no listing, query, search, pagination, or caller-visible
    transaction. Each call is its own atomic unit of work.
    """

    def create(self, content: ContentObject) -> None:
        """Store canonical content that is not stored yet.

        Raises ``ContentObjectAlreadyExistsError`` if ``content.id`` is taken
        or if ``content.source.capture_id`` already has canonical content,
        leaving the stored object untouched.
        """
        ...

    def get(self, content_id: str) -> ContentObject:
        """Return a freshly reconstructed content object.

        The result is a new object validated from the stored snapshot, not a
        live handle on it. Raises ``ContentObjectNotFoundError`` if the id is
        not stored.
        """
        ...

    def get_for_capture(self, capture_id: str) -> ContentObject:
        """Return the canonical content a capture normalized into.

        Raises ``ContentObjectNotFoundError`` if the capture has no canonical
        content — which is the ordinary answer for a capture that has not been
        processed, and the interesting one for a capture whose record says it
        was.
        """
        ...
