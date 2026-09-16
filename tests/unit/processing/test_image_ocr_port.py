"""The image recognition port's value types, errors, and result validation.

Three things are under test here and none of them needs a capture, a store, a
processor, or an engine.

**The error hierarchy**, because in this design the class tree *is* the lifecycle.
A type that accidentally subclassed ``ProcessingError`` would durably mark
somebody's photograph ``failed``; a limit signal that accidentally subclassed the
execution error would turn a successful capture into a 503. Those are not
hypothetical mistakes, they are one keyword each, so each relationship is
asserted rather than assumed.

**The structured limit signal**, because a processor branches on it. If its
meaning lived in its message then rewording the message would silently change
control flow, so the test reads the attributes and — deliberately — proves the
message is *not* needed to tell the two reasons apart.

**The result validation**, because an adapter is infrastructure and can be wrong
in ways ``core`` cannot see. Every rejection asserts the *type* as well as the
refusal: a malformed answer must be an
:class:`~core.processing.image_recognition.ImageOcrExecutionError` and must never
be a :class:`~core.processing.errors.ProcessingInputError`, which would blame the
picture, nor be quietly tolerated, which would record a recognizer as having
looked at an image and found nothing.
"""

from typing import Any, cast

import pytest

from core.processing.errors import ProcessingError, ProcessingInputError
from core.processing.image_recognition import (
    ENCODED_BYTE_LIMIT,
    ENCODED_PIXEL_LIMIT,
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
    ImageOcrResult,
    validate_image_ocr_result,
)


def result(**overrides: Any) -> ImageOcrResult:
    """A well-formed result, with one field spoiled at a time by the caller."""
    fields: dict[str, Any] = {
        "text": "recognized words\n",
        "engine": "fake-ocr",
        "engine_version": "9.9.9",
        "settings": {"languages": "fake+fake", "psm": 3},
    }
    return ImageOcrResult(**(fields | overrides))


class TestTheErrorHierarchy:
    """Which base class each error has decides what happens to the capture."""

    def test_an_execution_error_is_not_a_processing_error(self) -> None:
        """A ``ProcessingError`` would durably mark the capture failed."""
        assert not isinstance(ImageOcrExecutionError("engine gone"), ProcessingError)

    def test_a_limit_signal_is_not_a_processing_error(self) -> None:
        """It ends in a 201. A ``ProcessingError`` would end in a 422."""
        assert not isinstance(
            ImageOcrLimitExceeded("too big", reason=ENCODED_PIXEL_LIMIT, limit=1), ProcessingError
        )

    def test_a_limit_signal_is_not_an_execution_error(self) -> None:
        """Siblings, so neither inherits the other's lifecycle by accident."""
        assert not isinstance(
            ImageOcrLimitExceeded("too big", reason=ENCODED_PIXEL_LIMIT, limit=1),
            ImageOcrExecutionError,
        )

    def test_an_execution_error_is_not_a_limit_signal(self) -> None:
        assert not isinstance(ImageOcrExecutionError("engine gone"), ImageOcrLimitExceeded)

    def test_both_are_ordinary_exceptions(self) -> None:
        """Nothing exotic: a caller that catches ``Exception`` still catches them."""
        assert isinstance(ImageOcrExecutionError("x"), Exception)
        assert isinstance(ImageOcrLimitExceeded("x", reason=ENCODED_BYTE_LIMIT, limit=2), Exception)


class TestTheLimitSignalIsStructured:
    def test_the_reason_is_readable_as_an_attribute(self) -> None:
        refusal = ImageOcrLimitExceeded("over", reason=ENCODED_PIXEL_LIMIT, limit=20_000_000)

        assert refusal.reason == "encoded_pixel_limit"

    def test_the_limit_is_readable_as_an_attribute(self) -> None:
        refusal = ImageOcrLimitExceeded("over", reason=ENCODED_BYTE_LIMIT, limit=67_108_864)

        assert refusal.limit == 67_108_864

    def test_the_two_reasons_are_distinguishable_without_reading_the_message(self) -> None:
        """The whole point of the structure.

        Both are given the *same* message, so anything that told them apart here
        did so from the attributes. A processor that parsed ``str(exc)`` would
        fail this test, which is exactly when it should fail.
        """
        pixels = ImageOcrLimitExceeded("refused", reason=ENCODED_PIXEL_LIMIT, limit=1)
        octets = ImageOcrLimitExceeded("refused", reason=ENCODED_BYTE_LIMIT, limit=1)

        assert str(pixels) == str(octets)
        assert pixels.reason != octets.reason

    def test_the_message_still_exists_for_a_log(self) -> None:
        """Structured does not mean silent; a server log still wants a sentence."""
        refusal = ImageOcrLimitExceeded(
            "the image encodes too many pixels", reason=ENCODED_PIXEL_LIMIT, limit=1
        )

        assert "too many pixels" in str(refusal)

    def test_the_reason_constants_are_the_values_recorded_durably(self) -> None:
        """These strings reach ``metadata["image_ocr"]["skipped_reason"]``."""
        assert ENCODED_PIXEL_LIMIT == "encoded_pixel_limit"
        assert ENCODED_BYTE_LIMIT == "encoded_byte_limit"


class TestTheResultValueType:
    def test_a_result_is_immutable(self) -> None:
        answer = result()

        with pytest.raises(AttributeError):
            answer.text = "rewritten"  # type: ignore[misc]

    def test_an_empty_string_is_a_representable_answer(self) -> None:
        """ "Looked at and read nothing" is a result, not an omission."""
        assert result(text="").text == ""

    def test_two_equal_results_compare_equal(self) -> None:
        """A plain value type, so a test can compare them rather than their fields."""
        assert result() == result()

    def test_it_carries_no_rasterizer(self) -> None:
        """Nothing rasterizes on this path, so there is no such fact to report."""
        assert not any("raster" in name for name in ImageOcrResult.__dataclass_fields__)


class TestValidationAcceptsAnHonestAnswer:
    def test_a_well_formed_result_passes(self) -> None:
        validate_image_ocr_result(result())

    def test_empty_text_passes(self) -> None:
        """The one thing validation must never treat as malformed."""
        validate_image_ocr_result(result(text=""))

    def test_whitespace_only_text_passes(self) -> None:
        """Blankness is the processor's decision, not the validator's."""
        validate_image_ocr_result(result(text="   \n\t"))

    def test_empty_settings_pass(self) -> None:
        validate_image_ocr_result(result(settings={}))

    def test_nested_json_settings_pass(self) -> None:
        validate_image_ocr_result(
            result(settings={"languages": ["eng", "rus"], "limits": {"pixels": 1}, "dpi": None})
        )

    def test_validation_returns_nothing_and_rewrites_nothing(self) -> None:
        """It is a guard, not a normalizer: the text it approved is untouched."""
        answer = result(text="  ragged\n\n")

        validate_image_ocr_result(answer)

        assert answer.text == "  ragged\n\n"


class TestValidationRejectsAMalformedAnswer:
    """Every rejection is an execution failure, never a verdict about the image."""

    def test_non_string_text_is_refused(self) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(text=cast(Any, None)))

    def test_bytes_text_is_refused(self) -> None:
        """Bytes would reach a contract validator much later, blaming a segment."""
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(text=cast(Any, b"words")))

    @pytest.mark.parametrize("engine", ["", "   ", "\n"])
    def test_a_blank_engine_name_is_refused(self, engine: str) -> None:
        """It would become durable metadata naming an engine with no name."""
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(engine=engine))

    @pytest.mark.parametrize("version", ["", "   "])
    def test_a_blank_engine_version_is_refused(self, version: str) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(engine_version=version))

    def test_a_non_string_engine_is_refused(self) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(engine=cast(Any, 7)))

    def test_non_mapping_settings_are_refused(self) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(settings=cast(Any, ["languages", "eng"])))

    def test_a_non_string_settings_key_is_refused(self) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(settings=cast(Any, {3: "three"})))

    def test_a_settings_value_that_cannot_survive_json_is_refused(self) -> None:
        """``ContentObject.metadata`` must round-trip, so this cannot be stored."""
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(settings=cast(Any, {"clock": object()})))

    def test_a_nested_settings_value_that_cannot_survive_json_is_refused(self) -> None:
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(settings=cast(Any, {"limits": {"x": object()}})))

    @pytest.mark.parametrize(
        "spoiled",
        [
            {"text": cast(Any, None)},
            {"engine": ""},
            {"engine_version": ""},
            {"settings": cast(Any, "eng")},
        ],
        ids=["text", "engine", "version", "settings"],
    )
    def test_no_rejection_is_ever_an_input_verdict(self, spoiled: dict[str, Any]) -> None:
        """A bug in an adapter must not durably fail somebody's capture."""
        with pytest.raises(ImageOcrExecutionError) as raised:
            validate_image_ocr_result(result(**spoiled))

        assert not isinstance(raised.value, ProcessingInputError)
        assert not isinstance(raised.value, ProcessingError)

    def test_a_malformed_answer_never_becomes_a_blank_recognition(self) -> None:
        """The failure mode this validation exists to prevent.

        An adapter that answered with a broken result must not be recorded as a
        recognizer that ran and found no text, because those are different facts
        and only one of them is true.
        """
        with pytest.raises(ImageOcrExecutionError):
            validate_image_ocr_result(result(engine=""))
