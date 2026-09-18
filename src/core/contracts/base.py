"""Shared building blocks for the canonical domain contracts.

This module holds the pieces every contract needs: the schema version and the
rules that depend on it, the common model configuration, and the small
annotated scalar types used across the domain. It deliberately contains no
domain objects of its own.
"""

from typing import Annotated, Final, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    JsonValue,
    StringConstraints,
)

#: The schema version every canonical contract is emitted with today. It names
#: the whole canonical contract *set*, not one model: the contracts are
#: designed, reviewed, and released together, so they carry one version even
#: when a given release changes only one of them.
SCHEMA_VERSION: Final = "0.3"

#: Type of the ``schema_version`` field on versioned contracts. Kept as an
#: explicit ``Literal`` so unknown versions — including newer ones — fail
#: validation instead of being interpreted with today's assumptions. Older
#: versions stay listed for exactly as long as this build can still read
#: documents written with them.
SchemaVersion = Literal["0.1", "0.2", "0.3"]

#: Schema versions this build of the contracts accepts on input, derived from
#: the field type so the two cannot drift apart.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset(get_args(SchemaVersion))

#: The version that introduced ``context``, ``intent``, and ``title`` on
#: :class:`~core.contracts.capture.CaptureRecord`. Named so the rule that
#: depends on it reads as a rule, and so the error a violation raises can say
#: *when* the fields arrived rather than what today happens to be current.
CAPTURE_METADATA_SCHEMA_VERSION: Final = "0.2"

#: Every supported version whose ``CaptureRecord`` carries that metadata.
#:
#: Listed positively and by hand, one entry per version, because that is the
#: whole point of the mechanism. Advancing the current version must not silently
#: re-classify an older one: adding ``"0.3"`` to :data:`SchemaVersion` without
#: adding it here would leave 0.3 records unable to carry ``context``, which
#: fails loudly on the first one written rather than quietly changing what a
#: stored 0.2 document means.
CAPTURE_METADATA_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"0.2", "0.3"})

#: The complement of the set above, derived rather than written out so the two
#: cannot disagree about a version.
SCHEMA_VERSIONS_BEFORE_CAPTURE_METADATA: Final[frozenset[str]] = (
    SUPPORTED_SCHEMA_VERSIONS - CAPTURE_METADATA_SCHEMA_VERSIONS
)

#: The version that made standalone audio a first-class capture and content
#: modality. See `ADR-021
#: <../../docs/ADR/ADR-021-original-first-time-based-media-ingestion.md>`_.
AUDIO_SCHEMA_VERSION: Final = "0.3"

#: Every supported version whose vocabulary contains ``audio``. Listed by hand
#: for the same reason as :data:`CAPTURE_METADATA_SCHEMA_VERSIONS`.
#:
#: ``video`` deliberately has no set of its own: it has been in the vocabulary
#: since 0.1, so a historical ``VIDEO`` document stays valid at every supported
#: version and there is no rule to express.
AUDIO_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"0.3"})

#: The complement, derived like the one above.
SCHEMA_VERSIONS_BEFORE_AUDIO: Final[frozenset[str]] = (
    SUPPORTED_SCHEMA_VERSIONS - AUDIO_SCHEMA_VERSIONS
)


def check_audio_within_schema_version(schema_version: str, *, subject: str) -> None:
    """Refuse an ``audio`` value on a document whose version has no word for it.

    A document that claims a version predating the modality is wrong about its
    own shape, exactly as a 0.1 ``CaptureRecord`` carrying a ``title`` is: a
    reader built against that version would reject the value, so accepting it
    here would mint a document this project's own compatibility promise cannot
    keep.

    ``subject`` names the field being checked — ``"payload type"``,
    ``"content type"`` — so the message points at the offending field rather
    than at the contract as a whole.

    Raises ``ValueError``, which is what a Pydantic model validator turns into a
    ``ValidationError``.
    """
    if schema_version in SCHEMA_VERSIONS_BEFORE_AUDIO:
        raise ValueError(
            f"schema version {schema_version} has no audio {subject}; "
            f"it was added in {AUDIO_SCHEMA_VERSION}"
        )


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


#: A string that carries actual content (not empty, not only whitespace).
#: The value is never trimmed or otherwise rewritten.
NonBlankStr = Annotated[str, StringConstraints(min_length=1), AfterValidator(_reject_blank)]

#: An identifier. Non-empty string, deliberately *not* constrained to UUID or
#: ULID: the system has not yet decided how identifiers are minted.
Identifier = NonBlankStr

#: A lowercase hex SHA-256 digest.
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

#: Free-form metadata mapping. ``JsonValue`` restricts the contents to values
#: that can round-trip through JSON, which keeps every contract serializable.
#: Fields using it are declared as ``metadata: JsonMapping = Field(default_factory=dict)``.
JsonMapping = dict[str, JsonValue]


class DomainModel(BaseModel):
    """Base configuration shared by every domain contract.

    * ``extra="forbid"`` — unknown fields are an error, not silently kept or
      dropped. This is what keeps derived data (summaries, OCR text, ...) out
      of contracts that must not carry it.
    * ``validate_assignment=True`` — assigning to a field re-runs that model's
      validators, including its cross-field ones.

    The assignment guarantee is narrower than it looks, and callers should not
    over-trust it. It covers *direct assignment to a field of this model*
    (``content.segments = [...]``). It does **not** cover:

    * in-place mutation of a mutable container held in a field —
      ``content.segments.append(...)`` or ``content.metadata["k"] = ...``
      never reaches a validator;
    * assignment to a field of a *nested* model — ``segment.provenance
      .capture_id = ...`` revalidates ``Provenance`` alone, and the enclosing
      ``ContentObject``'s invariants are not rechecked;
    * **rollback of a rejected assignment.** A value that fails a field's own
      rules is never written, but a value that passes those and is then
      rejected by a model-level validator has already been assigned when the
      error is raised. Catching the ``ValidationError`` therefore leaves the
      instance holding the offending value.

    Instances are therefore validated snapshots, not continuously enforced
    objects. Build a new instance (or re-validate an existing one with
    ``Model.model_validate(instance.model_dump())``) rather than editing one
    in place and assuming the invariants still hold.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        use_enum_values=False,
    )
