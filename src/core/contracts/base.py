"""Shared building blocks for the canonical domain contracts.

This module holds the pieces every contract needs: the schema version, the
common model configuration, and the small annotated scalar types used across
the domain. It deliberately contains no domain objects of its own.
"""

from typing import Annotated, Final, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    JsonValue,
    StringConstraints,
)

#: The schema version every canonical contract is emitted with today.
SCHEMA_VERSION: Final = "0.1"

#: Type of the ``schema_version`` field on versioned contracts. Kept as an
#: explicit ``Literal`` so unknown versions — including newer ones — fail
#: validation instead of being interpreted with today's assumptions.
SchemaVersion = Literal["0.1"]

#: Schema versions this build of the contracts accepts on input, derived from
#: the field type so the two cannot drift apart.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset(get_args(SchemaVersion))


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
    * ``validate_assignment=True`` — mutating a model re-runs validation, so an
      object cannot be edited into an invalid state after construction.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        use_enum_values=False,
    )
