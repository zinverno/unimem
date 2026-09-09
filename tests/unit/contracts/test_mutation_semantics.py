"""What validation does and does not cover after construction.

Contract instances are validated snapshots. These tests pin the guarantee
that direct field assignment re-runs the owning model's validators, and record
the two paths that bypass it, so the documentation in ``DomainModel`` and
ARCHITECTURE.md stays honest.
"""

import pytest
from pydantic import ValidationError

from core.contracts import ContentObject, OriginalReference
from tests.unit.contracts.builders import (
    make_asset,
    make_content_object,
    make_provenance,
    make_segment,
)


def test_direct_assignment_rechecks_the_provenance_invariant() -> None:
    content = make_content_object()
    foreign = make_segment(id="seg_foreign", provenance=make_provenance(capture_id="cap_other"))
    with pytest.raises(ValidationError, match="has provenance from capture"):
        content.segments = [foreign]


def test_direct_assignment_rechecks_uniqueness() -> None:
    content = make_content_object()
    with pytest.raises(ValidationError, match="duplicate segment id"):
        content.segments = [make_segment(), make_segment()]


def test_direct_assignment_rechecks_asset_references() -> None:
    content = make_content_object()
    with pytest.raises(ValidationError, match="references unknown asset"):
        content.segments = [make_segment(provenance=make_provenance(asset_id="ast_missing"))]


def test_direct_assignment_of_a_valid_replacement_is_accepted() -> None:
    content = make_content_object()
    replacement = make_segment(id="seg_02", text="replacement")
    content.segments = [replacement]
    assert [segment.id for segment in content.segments] == ["seg_02"]


def test_a_rejection_by_a_field_rule_never_writes_the_value() -> None:
    content = make_content_object()
    with pytest.raises(ValidationError, match="must not be blank"):
        content.title = "   "
    assert content.title == "Architecture"


def test_a_rejection_by_a_model_validator_is_not_rolled_back() -> None:
    """Documents a known limitation: assignment is validated, not atomic.

    The field is written before the model-level validator runs, so an instance
    whose ValidationError was caught is left holding the rejected value.
    """
    content = make_content_object()
    foreign = make_segment(id="seg_foreign", provenance=make_provenance(capture_id="cap_other"))
    with pytest.raises(ValidationError, match="has provenance from capture"):
        content.segments = [foreign]

    assert [segment.id for segment in content.segments] == ["seg_foreign"]
    with pytest.raises(ValidationError, match="has provenance from capture"):
        ContentObject.model_validate(content.model_dump(mode="json"))


def test_assignment_on_a_nested_model_validates_only_that_model() -> None:
    """The child's own rules apply; the parent's invariants are not rechecked."""
    content = make_content_object()

    with pytest.raises(ValidationError, match="must not be blank"):
        content.segments[0].id = "   "

    # The parent invariant "every segment traces back to this capture" is not
    # re-run for a nested assignment, so this succeeds and leaves the object in
    # a state the constructor would have rejected.
    content.segments[0].provenance.capture_id = "cap_other"
    assert content.source.capture_id == "cap_01"
    with pytest.raises(ValidationError, match="has provenance from capture"):
        ContentObject.model_validate(content.model_dump(mode="json"))


def test_in_place_container_mutation_bypasses_parent_validation() -> None:
    """Documents a known limitation: appending never reaches a validator."""
    content = make_content_object()
    foreign = make_segment(id="seg_foreign", provenance=make_provenance(capture_id="cap_other"))
    content.segments.append(foreign)

    # Re-validating the object is how a caller recovers the guarantee.
    with pytest.raises(ValidationError, match="has provenance from capture"):
        ContentObject.model_validate(content.model_dump(mode="json"))


def test_in_place_metadata_mutation_bypasses_json_validation() -> None:
    """Documents a known limitation: metadata is only checked when validated."""
    content = make_content_object()
    # mypy rejects this line for the same reason the contract does; the test is
    # about what happens at runtime when a caller writes it anyway.
    content.metadata["reader"] = object()  # type: ignore[assignment]

    # Dump every field except the poisoned one, then re-validate with the live
    # metadata: serializing the bad value itself is not what is under test.
    data = content.model_dump(mode="json", exclude={"metadata"}) | {"metadata": content.metadata}
    with pytest.raises(ValidationError):
        ContentObject.model_validate(data)


def test_revalidating_an_untouched_object_is_a_no_op() -> None:
    content = make_content_object(
        assets=[make_asset()], original=OriginalReference(asset_id="ast_01")
    )
    assert ContentObject.model_validate(content.model_dump(mode="json")) == content
