"""Proof the PDF recognition path did not move when its invocation was extracted.

Phase 4 PR 2 lifts the Tesseract subprocess call out of
:mod:`unimem_ocr.tesseract` into the stdlib-only runner both adapters now share.
That is only a safe refactor if nothing observable moved with it, so this file
pins the things a Phase 3 deployment can actually see: the command line the
engine receives, and the sentence each failure produces.

**The expectations here are transcribed, not derived.** They are written out as
literals rather than built from the policy constants, because a test that
recomputes what the implementation computes proves only that the code agrees
with itself. If one of them fails, the implementation regressed: ADR-020 freezes
this path, so the fix is never to update the expectation.

**``__cause__`` is part of the frozen surface and is pinned here too.** It is not
cosmetic: it is what a traceback shows an operator and what any caller inspecting
the exception sees, and the first version of this refactor quietly changed it for
one of the four cases. A nonzero exit had no cause before and must have none now —
in particular the shared runner's own ``EngineInvocationError``, which is an
internal type this layer exists to hide, must never surface as the cause of a PDF
error.
"""

import subprocess
from pathlib import Path

import pytest

from core.processing.ocr import PdfOcrExecutionError
from tests import ocr_support
from tests.unit.ocr import fakes
from unimem_ocr.engine import EngineInvocationError

ocr_support.require_rasterizer()

from unimem_ocr.tesseract import TesseractPdfPageOcr  # noqa: E402 - guarded above

#: The exact vector the PDF adapter has always sent, minus argv[0].
FROZEN_PDF_ARGUMENTS = [
    "stdin",
    "stdout",
    "-l",
    "eng+rus",
    "--oem",
    "1",
    "--psm",
    "3",
    "--dpi",
    "300",
]


def adapter(engine: Path) -> TesseractPdfPageOcr:
    return TesseractPdfPageOcr(engine_version="5.3.4", executable=str(engine))


def message_for(engine: Path, *, page: int = 4, timeout: float = 30.0) -> str:
    """Run one invocation that must fail, and return the sentence it produced."""
    return str(failure_from(engine, page=page, timeout=timeout))


def failure_from(engine: Path, *, page: int = 4, timeout: float = 30.0) -> PdfOcrExecutionError:
    """Run one invocation that must fail, and return the error itself.

    Returned rather than asserted on here so a caller can inspect the whole
    exception — its text, its type, and its chain — instead of only its message.
    """
    try:
        adapter(engine)._recognize(b"image", page=page, timeout=timeout)
    except PdfOcrExecutionError as exc:
        return exc
    raise AssertionError("the invocation was expected to fail")


class TestThePdfCommandLineIsFrozen:
    def test_the_vector_is_exactly_what_it_has_always_been(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        adapter(engine)._recognize(b"image", page=1, timeout=30.0)

        assert fakes.recorded_argv(tmp_path) == FROZEN_PDF_ARGUMENTS

    def test_dpi_300_still_travels_with_every_page(self, tmp_path: Path) -> None:
        """It describes a rasterization that really happened, so it stays."""
        engine = fakes.write_fake_engine(tmp_path)

        adapter(engine)._recognize(b"image", page=1, timeout=30.0)

        assert fakes.recorded_argv(tmp_path)[-2:] == ["--dpi", "300"]

    def test_the_page_image_still_travels_on_stdin(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        adapter(engine)._recognize(b"\x89PNG page raster", page=1, timeout=30.0)

        assert fakes.recorded_stdin(tmp_path) == b"\x89PNG page raster"

    def test_stdout_still_comes_back_untouched(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="  page text  \n")

        assert adapter(engine)._recognize(b"image", page=1, timeout=30.0) == "  page text  \n"


class TestThePdfErrorMessagesAreFrozen:
    def test_a_timeout_says_exactly_what_it_said_before(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        message = message_for(engine, page=4, timeout=0.2)

        assert message == "the OCR engine did not finish page 4 within 0.2s and was killed"

    def test_a_launch_failure_says_exactly_what_it_said_before(self, tmp_path: Path) -> None:
        absent = tmp_path / "absent"

        message = message_for(absent, page=7)

        assert message.startswith(
            f"the OCR engine {str(absent)!r} could not be run while recognizing page 7 ("
        )
        assert message.endswith(")")

    def test_a_nonzero_exit_says_exactly_what_it_said_before(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=2)

        assert message_for(engine, page=11) == "the OCR engine exited with status 2 on page 11"

    def test_undecodable_output_says_exactly_what_it_said_before(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout_bytes=b"\xff\xfe")

        message = message_for(engine, page=2)

        assert message == "the OCR engine's output for page 2 was not valid UTF-8"

    def test_a_nonzero_exit_still_carries_no_engine_output(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(
            tmp_path, recognize_exit=1, stderr="failed reading /home/someone/scan.png"
        )

        assert "/home/someone" not in message_for(engine, page=3)


class TestThePdfExceptionChainIsFrozen:
    """What ``__cause__`` carries, case by case, exactly as it did before.

    Four cases and they are not uniform, which is the point: three wrap a real
    Python exception and the fourth wraps a status code, so a rule that "always
    chains" would be as wrong as one that never does.
    """

    def test_a_timeout_keeps_the_subprocess_error_as_its_cause(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        failure = failure_from(engine, timeout=0.2)

        assert isinstance(failure.__cause__, subprocess.TimeoutExpired)

    def test_a_launch_failure_keeps_the_oserror_as_its_cause(self, tmp_path: Path) -> None:
        failure = failure_from(tmp_path / "absent")

        assert isinstance(failure.__cause__, OSError)

    def test_undecodable_output_keeps_the_unicode_error_as_its_cause(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout_bytes=b"\xff\xfe")

        failure = failure_from(engine)

        assert isinstance(failure.__cause__, UnicodeDecodeError)

    def test_a_nonzero_exit_has_no_cause_at_all(self, tmp_path: Path) -> None:
        """A status code is not an exception, and none is invented to stand in."""
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=2)

        failure = failure_from(engine)

        assert failure.__cause__ is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"recognize_exit": 1},
            {"stdout_bytes": b"\xff"},
            {"sleep": 5},
        ],
        ids=["nonzero", "invalid-utf8", "timeout"],
    )
    def test_the_shared_runner_never_becomes_the_cause(
        self, tmp_path: Path, kwargs: dict[str, object]
    ) -> None:
        """``EngineInvocationError`` is internal and must not leak into the chain.

        Checked over the whole chain rather than one link, so a future wrapper that
        buries it one level deeper still fails this.
        """
        engine = fakes.write_fake_engine(tmp_path, **kwargs)  # type: ignore[arg-type]

        failure = failure_from(engine, timeout=0.2)

        seen: list[BaseException] = []
        cause = failure.__cause__
        while cause is not None and cause not in seen:
            seen.append(cause)
            assert not isinstance(cause, EngineInvocationError)
            cause = cause.__cause__

    def test_the_shared_runner_is_not_the_cause_for_a_missing_executable(
        self, tmp_path: Path
    ) -> None:
        failure = failure_from(tmp_path / "absent")

        assert not isinstance(failure.__cause__, EngineInvocationError)

    def test_a_nonzero_exit_does_not_surface_the_runner_as_context_either(
        self, tmp_path: Path
    ) -> None:
        """``from None`` suppresses it, so a traceback reads as it always did."""
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=2)

        failure = failure_from(engine)

        assert failure.__suppress_context__


class TestThePdfFailureTypeIsFrozen:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"recognize_exit": 1},
            {"stdout_bytes": b"\xff"},
            {"sleep": 5},
        ],
        ids=["nonzero", "invalid-utf8", "timeout"],
    )
    def test_every_invocation_failure_is_still_a_pdf_ocr_execution_error(
        self, tmp_path: Path, kwargs: dict[str, object]
    ) -> None:
        engine = fakes.write_fake_engine(tmp_path, **kwargs)  # type: ignore[arg-type]

        with pytest.raises(PdfOcrExecutionError):
            adapter(engine)._recognize(b"image", page=1, timeout=0.2)

    def test_a_missing_executable_is_still_a_pdf_ocr_execution_error(self, tmp_path: Path) -> None:
        with pytest.raises(PdfOcrExecutionError):
            adapter(tmp_path / "absent")._recognize(b"image", page=1, timeout=30.0)

    def test_an_invocation_failure_is_never_an_input_verdict(self, tmp_path: Path) -> None:
        """Unchanged, and the reason is unchanged: a dead engine says nothing."""
        from core.processing.errors import ProcessingError

        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(PdfOcrExecutionError) as raised:
            adapter(engine)._recognize(b"image", page=1, timeout=30.0)

        assert not isinstance(raised.value, ProcessingError)
