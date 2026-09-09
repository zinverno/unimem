"""The capture record store port.

Three operations, synchronous, backend-neutral. A ``CaptureRecord`` is stored
and retrieved as a whole validated snapshot; nothing about databases, files,
connections, or query languages appears in this signature, and no domain
contract learns that a store exists.

The split between ``create`` and ``replace`` is the point of the port. There is
deliberately no ``save`` or ``upsert``: "this is a new capture" and "this
capture's lifecycle snapshot has moved on" are different intentions, and a
single method that guesses from whether a row happens to exist turns a
duplicate-id bug into a silent overwrite.
"""

from typing import Protocol

from core.contracts import CaptureRecord


class CaptureRecordStore(Protocol):
    """Durably stores capture record snapshots and hands them back.

    Implementations store exactly what they are given. They never mint an id,
    set or advance ``updated_at``, choose a ``status``, invent an ``error``, or
    mutate the record they were handed. They enforce no lifecycle rule either:
    any valid ``CaptureRecord`` may be stored in any state, because whether
    ``stored -> processing -> complete`` is a legal move is orchestration's
    decision and orchestration does not exist yet.

    There is no delete, no listing, no query, and no transaction a caller can
    see. Each call is its own atomic unit of work.
    """

    def create(self, record: CaptureRecord) -> None:
        """Store a record that is not stored yet.

        Raises ``CaptureRecordAlreadyExistsError`` if ``record.id`` is already
        taken, leaving the stored snapshot untouched.
        """
        ...

    def get(self, capture_id: str) -> CaptureRecord:
        """Return a freshly reconstructed record.

        The result is a new object validated from the stored snapshot, not a
        live handle on it: mutating it changes nothing until it is passed back
        to ``replace``. Raises ``CaptureRecordNotFoundError`` if the id is not
        stored.
        """
        ...

    def replace(self, record: CaptureRecord) -> None:
        """Replace the stored snapshot for ``record.id`` with this one.

        Raises ``CaptureRecordNotFoundError`` if nothing is stored under that
        id. Replace never creates.
        """
        ...
