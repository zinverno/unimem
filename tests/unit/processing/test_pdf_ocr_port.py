"""The OCR port's value types, and the validation that guards the boundary.

An adapter is infrastructure: it shells out to a subprocess, drives a native
renderer, and can be wrong in ways ``core`` cannot see. The thing standing
between a wrong answer and a corrupted content object is
:func:`~core.processing.ocr.validate_ocr_result`, so it is tested on its own,
with no capture, no store, and no processor anywhere near it.

Every rejection here asserts the *type* as well as the refusal. A malformed
provider answer must be a
:class:`~core.processing.ocr.PdfOcrExecutionError` and must **not** be a
:class:`~core.processing.errors.ProcessingInputError`, because the second one
would durably mark somebody's capture ``failed`` on the strength of a bug in the
engine adapter.
"""

import pytest

from core.processing.errors import ProcessingError
from core.processing.ocr import (
    PdfOcrExecutionError,
    PdfOcrResult,
    RecognizedPage,
    validate_ocr_result,
)


def result(page_count: int, pages: dict[int, str]) -> PdfOcrResult:
    """A result over ``page_count`` physical pages reporting exactly ``pages``."""
    return PdfOcrResult(
        page_count=page_count,
        pages=tuple(RecognizedPage(page=number, text=text) for number, text in pages.items()),
        engine="fake-ocr",
        engine_version="9.9.9",
        rasterizer="fake-raster",
        rasterizer_version="1.2.3",
        settings={"languages": "fake+fake"},
    )


class TestTheValueTypes:
    def test_a_recognized_page_is_immutable(self) -> None:
        page = RecognizedPage(page=2, text="read from pixels")

        with pytest.raises(AttributeError):
            page.text = "rewritten"  # type: ignore[misc]

    def test_a_result_is_immutable(self) -> None:
        answer = result(1, {1: "x"})

        with pytest.raises(AttributeError):
            answer.page_count = 99  # type: ignore[misc]

    def test_an_empty_string_is_a_representable_answer(self) -> None:
        """ "Looked at and found nothing" is a result, not an omission."""
        assert RecognizedPage(page=1, text="").text == ""

    def test_two_equal_pages_compare_equal(self) -> None:
        """Plain value types, so a test can compare them rather than their fields."""
        assert RecognizedPage(page=3, text="a") == RecognizedPage(page=3, text="a")
        assert RecognizedPage(page=3, text="a") != RecognizedPage(page=4, text="a")


class TestAConsistentResultIsAccepted:
    def test_every_page_recognized(self) -> None:
        validate_ocr_result(result(3, {1: "a", 2: "", 3: "c"}), embedded_pages=frozenset())

    def test_every_page_covered_by_embedded_text(self) -> None:
        """Nothing to recognize is a perfectly good answer, and the common one."""
        validate_ocr_result(result(2, {}), embedded_pages=frozenset({1, 2}))

    def test_a_mixture(self) -> None:
        validate_ocr_result(result(4, {2: "scan", 4: ""}), embedded_pages=frozenset({1, 3}))

    def test_pages_reported_out_of_order(self) -> None:
        """Order is the caller's business; the port promises a set, not a sequence."""
        answer = PdfOcrResult(
            page_count=3,
            pages=(
                RecognizedPage(page=3, text="c"),
                RecognizedPage(page=1, text="a"),
                RecognizedPage(page=2, text="b"),
            ),
            engine="fake-ocr",
            engine_version="9.9.9",
            rasterizer="fake-raster",
            rasterizer_version="1.2.3",
            settings={},
        )

        validate_ocr_result(answer, embedded_pages=frozenset())

    def test_a_document_with_no_pages_at_all(self) -> None:
        """Degenerate but consistent. The emptiness is judged by the processor."""
        validate_ocr_result(result(0, {}), embedded_pages=frozenset())


class TestAnInconsistentResultIsAnExecutionFailure:
    def test_a_negative_page_count(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match="page count"):
            validate_ocr_result(result(-1, {}), embedded_pages=frozenset())

    def test_a_page_number_of_zero(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match="not a physical page number"):
            validate_ocr_result(result(2, {0: "a", 1: "b", 2: "c"}), embedded_pages=frozenset())

    def test_a_negative_page_number(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match="not a physical page number"):
            validate_ocr_result(result(1, {-3: "a", 1: "b"}), embedded_pages=frozenset())

    def test_a_page_past_the_end_of_the_document(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match="says has 2 pages"):
            validate_ocr_result(result(2, {1: "a", 2: "b", 3: "c"}), embedded_pages=frozenset())

    def test_a_page_the_caller_excluded(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match="excluded as already covered"):
            validate_ocr_result(result(2, {1: "a", 2: "b"}), embedded_pages=frozenset({1}))

    def test_a_duplicated_page(self) -> None:
        answer = PdfOcrResult(
            page_count=2,
            pages=(
                RecognizedPage(page=1, text="first answer"),
                RecognizedPage(page=2, text="b"),
                RecognizedPage(page=1, text="second answer"),
            ),
            engine="fake-ocr",
            engine_version="9.9.9",
            rasterizer="fake-raster",
            rasterizer_version="1.2.3",
            settings={},
        )

        with pytest.raises(PdfOcrExecutionError, match="more than once"):
            validate_ocr_result(answer, embedded_pages=frozenset())

    def test_a_missing_page(self) -> None:
        """The rule that keeps a dropped page from looking like a blank one."""
        with pytest.raises(PdfOcrExecutionError, match=r"no result for page\(s\) \[2\]"):
            validate_ocr_result(result(3, {1: "a", 3: "c"}), embedded_pages=frozenset())

    def test_several_missing_pages_are_all_named(self) -> None:
        with pytest.raises(PdfOcrExecutionError, match=r"\[2, 4\]"):
            validate_ocr_result(result(4, {1: "a", 3: "c"}), embedded_pages=frozenset())

    def test_a_page_count_smaller_than_the_extracted_pages(self) -> None:
        """Two readers disagreeing about the document is a failure, not a merge."""
        with pytest.raises(PdfOcrExecutionError, match="embedded text was extracted from"):
            validate_ocr_result(result(1, {}), embedded_pages=frozenset({1, 2}))

    @pytest.mark.parametrize(
        ("answer", "embedded"),
        [
            (result(-1, {}), frozenset[int]()),
            (result(2, {0: "a", 1: "b", 2: "c"}), frozenset[int]()),
            (result(2, {1: "a", 2: "b", 3: "c"}), frozenset[int]()),
            (result(2, {1: "a", 2: "b"}), frozenset({1})),
            (result(3, {1: "a", 3: "c"}), frozenset[int]()),
            (result(1, {}), frozenset({1, 2})),
        ],
        ids=["count", "zero", "beyond", "overlap", "missing", "disagreement"],
    )
    def test_no_rejection_is_ever_a_processing_error(
        self, answer: PdfOcrResult, embedded: frozenset[int]
    ) -> None:
        """The negative assertion that matters most.

        If any of these were a ``ProcessingError`` the orchestrator would mark
        the capture ``failed``, permanently, for a defect in an adapter — telling
        the submitter their document is unprocessable on the strength of a bug.
        """
        with pytest.raises(PdfOcrExecutionError) as raised:
            validate_ocr_result(answer, embedded_pages=embedded)

        assert not isinstance(raised.value, ProcessingError)


def test_the_execution_error_is_outside_the_processing_hierarchy() -> None:
    """Stated as a standalone fact, because the whole lifecycle rests on it."""
    assert not issubclass(PdfOcrExecutionError, ProcessingError)
