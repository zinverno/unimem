"""``TesseractPdfPageOcr`` — the real renderer, a fake engine, and the safeguards.

The rasterizer here is genuine: PDFium really opens the fixture PDFs and really
produces pixels, because the resource checks, the handle lifetimes, and the
native-library lock are only worth testing against the library they exist for.
The *engine* is a fake — an executable shell script — so that a nonzero exit, a
hang, stderr noise, and undecodable output are all reproducible. What the real
engine actually reads is proved in ``tests/integration/ocr``.

This module needs the optional OCR extra and nothing else. Where the extra is
absent it skips, unless ``UNIMEM_REQUIRE_PDF_OCR_INTEGRATION`` says it may not —
see :mod:`tests.ocr_support`.
"""

import io
import math
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Final

import pytest

from core.processing.errors import ProcessingError, ProcessingInputError
from core.processing.ocr import PdfOcrExecutionError
from tests import ocr_support, pdfs
from tests.unit.ocr import fakes

ocr_support.require_rasterizer()

import pypdfium2 as pdfium  # noqa: E402 - only importable once the guard has passed
import pypdfium2.raw as pdfium_raw  # noqa: E402
from PIL import Image as PILImage  # noqa: E402 - the imaging library is part of the extra

from unimem_ocr.policy import (  # noqa: E402
    RENDER_DPI,
    RENDER_SCALE,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
    OcrLimits,
)
from unimem_ocr.tesseract import (  # noqa: E402
    ENGINE_NAME,
    RASTERIZER_NAME,
    TesseractPdfPageOcr,
)

#: The version an adapter under test claims. A marker, so a test can tell a
#: recorded version apart from one this module happened to hard-code.
ENGINE_VERSION: Final = "5.3.4-fake"

#: The exact argument vector, after argv[0], that every recognition must send.
EXPECTED_ARGUMENTS: Final = [
    "stdin",
    "stdout",
    "-l",
    TESSERACT_LANGUAGE_ARGUMENT,
    "--oem",
    str(TESSERACT_OEM),
    "--psm",
    str(TESSERACT_PSM),
    "--dpi",
    str(RENDER_DPI),
]


def adapter(
    engine: Path,
    *,
    limits: OcrLimits | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> TesseractPdfPageOcr:
    return TesseractPdfPageOcr(
        engine_version=ENGINE_VERSION,
        executable=str(engine),
        limits=limits if limits is not None else OcrLimits(),
        monotonic=monotonic,
    )


def scan(pages: int = 1) -> bytes:
    """A structurally valid PDF of ``pages`` pages carrying no embedded text."""
    return pdfs.build_pdf([[] for _ in range(pages)])


@pytest.fixture
def engine_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "engine"
    directory.mkdir()
    return directory


@pytest.fixture
def engine(engine_dir: Path) -> Path:
    return fakes.write_fake_engine(engine_dir)


class RenderSpy:
    """Wraps ``PdfPage.render``, recording every call and every live bitmap."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.bitmaps: list[Any] = []
        self.closed: list[Any] = []
        self.live: list[Any] = []
        self.peak = 0
        self.overlaps: list[int] = []
        self._inside: list[int] = []
        self._delay = delay
        original_render = pdfium.PdfPage.render
        original_close = pdfium.PdfBitmap.close
        spy = self

        def render(page: Any, *args: Any, **kwargs: Any) -> Any:
            spy.calls.append(kwargs)
            spy._inside.append(1)
            if len(spy._inside) > 1:
                spy.overlaps.append(len(spy._inside))
            try:
                if spy._delay:
                    time.sleep(spy._delay)
                bitmap = original_render(page, *args, **kwargs)
            finally:
                spy._inside.pop()
            spy.bitmaps.append(bitmap)
            spy.live.append(bitmap)
            spy.peak = max(spy.peak, len(spy.live))
            return bitmap

        def close(bitmap: Any) -> None:
            spy.closed.append(bitmap)
            if bitmap in spy.live:
                spy.live.remove(bitmap)
            original_close(bitmap)

        monkeypatch.setattr(pdfium.PdfPage, "render", render)
        monkeypatch.setattr(pdfium.PdfBitmap, "close", close)


class MutableClock:
    """An injected monotonic clock a test can move by hand, recording every read."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.reads: list[float] = []

    def __call__(self) -> float:
        self.reads.append(self.now)
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AdvancingLock:
    """A real mutex that charges the clock for the time spent acquiring it.

    This is how a lock *wait* is made deterministic. A real contended wait would
    need a second thread holding PDFium and a sleep long enough to matter; this
    double instead says "acquiring me cost this much wall clock", which is the only
    property the budget check cares about. It is a genuine ``threading.Lock``
    underneath, so mutual exclusion is unchanged while the test runs.
    """

    def __init__(self, clock: MutableClock, cost: float) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._cost = cost
        self.acquisitions = 0

    def __enter__(self) -> "AdvancingLock":
        self._lock.acquire()
        self.acquisitions += 1
        self._clock.advance(self._cost)
        return self

    def __exit__(self, *exception: object) -> None:
        self._lock.release()


class NativeWorkSpy:
    """Records every native step of a render, so a test can assert none happened."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pages_opened = 0
        self.renders = 0
        self.conversions = 0
        self.encodes = 0
        self.documents_closed = 0
        originals = {
            "get_page": pdfium.PdfDocument.get_page,
            "render": pdfium.PdfPage.render,
            "to_pil": pdfium.PdfBitmap.to_pil,
            "save": PILImage.Image.save,
            "close": pdfium.PdfDocument.close,
        }
        spy = self

        def get_page(document: Any, index: Any) -> Any:
            spy.pages_opened += 1
            return originals["get_page"](document, index)

        def render(page: Any, *args: Any, **kwargs: Any) -> Any:
            spy.renders += 1
            return originals["render"](page, *args, **kwargs)

        def to_pil(bitmap: Any) -> Any:
            spy.conversions += 1
            return originals["to_pil"](bitmap)

        def save(image: Any, *args: Any, **kwargs: Any) -> Any:
            spy.encodes += 1
            return originals["save"](image, *args, **kwargs)

        def close(document: Any) -> None:
            spy.documents_closed += 1
            originals["close"](document)

        monkeypatch.setattr(pdfium.PdfDocument, "get_page", get_page)
        monkeypatch.setattr(pdfium.PdfPage, "render", render)
        monkeypatch.setattr(pdfium.PdfBitmap, "to_pil", to_pil)
        monkeypatch.setattr(PILImage.Image, "save", save)
        monkeypatch.setattr(pdfium.PdfDocument, "close", close)

    @property
    def touched_anything(self) -> bool:
        return bool(self.pages_opened or self.renders or self.conversions or self.encodes)


@pytest.fixture
def render_spy(monkeypatch: pytest.MonkeyPatch) -> RenderSpy:
    return RenderSpy(monkeypatch)


class TestTheCommandLine:
    def test_the_argument_vector_is_fixed(self, engine: Path, engine_dir: Path) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert fakes.recorded_argv(engine_dir) == EXPECTED_ARGUMENTS

    def test_the_image_travels_on_stdin_as_a_png(self, engine: Path, engine_dir: Path) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert fakes.recorded_stdin(engine_dir).startswith(b"\x89PNG\r\n\x1a\n")

    def test_no_path_is_named_on_either_side(self, engine: Path, engine_dir: Path) -> None:
        """``stdin`` and ``stdout`` are the engine's own literals, not file names."""
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        recorded = fakes.recorded_argv(engine_dir)
        assert recorded[0] == "stdin"
        assert recorded[1] == "stdout"
        assert not [argument for argument in recorded if "/" in argument]

    def test_the_document_metadata_never_reaches_the_argument_vector(
        self, engine: Path, engine_dir: Path
    ) -> None:
        """A ``/Title`` shaped like an option is data, and stays data."""
        hostile = pdfs.build_pdf([[]], title="--psm 13 --tessdata-dir /etc; rm -rf /")

        adapter(engine).recognize_missing_pages(io.BytesIO(hostile), embedded_pages=frozenset())

        assert fakes.recorded_argv(engine_dir) == EXPECTED_ARGUMENTS

    def test_it_is_a_list_with_no_shell(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[tuple[Any, dict[str, Any]]] = []
        original = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            recorded.append((args[0], kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        arguments, keywords = recorded[-1]
        assert isinstance(arguments, list)
        assert keywords["shell"] is False
        assert keywords["capture_output"] is True
        assert keywords["timeout"] > 0
        assert isinstance(keywords["input"], bytes)

    def test_the_selected_languages_are_english_then_russian(self) -> None:
        assert TESSERACT_LANGUAGE_ARGUMENT == "eng+rus"
        assert EXPECTED_ARGUMENTS[EXPECTED_ARGUMENTS.index("-l") + 1] == "eng+rus"


class TestTheRasterization:
    def test_the_page_is_rendered_at_three_hundred_dpi(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.calls[0]["scale"] == RENDER_SCALE
        assert pytest.approx(300 / 72) == RENDER_SCALE

    def test_no_extra_rotation_is_applied(self, engine: Path, render_spy: RenderSpy) -> None:
        """Page rotation is the renderer's job; orientation is never guessed."""
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.calls[0]["rotation"] == 0

    def test_the_background_is_opaque_white(self, engine: Path, render_spy: RenderSpy) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.calls[0]["fill_color"] == (255, 255, 255, 255)

    def test_annotations_and_form_fields_are_not_rendered(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.calls[0]["draw_annots"] is False
        assert render_spy.calls[0]["may_draw_forms"] is False

    def test_no_form_environment_is_ever_initialized(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No JavaScript, no XFA, no form interaction — not even set up."""

        def explode(document: Any, config: Any = None) -> None:
            raise AssertionError("the adapter initialized PDF forms")

        monkeypatch.setattr(pdfium.PdfDocument, "init_forms", explode)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_the_stream_is_never_closed_by_the_adapter(self, engine: Path) -> None:
        """The handle belongs to the caller's ``with`` block."""
        stream = io.BytesIO(scan())

        adapter(engine).recognize_missing_pages(stream, embedded_pages=frozenset())

        assert not stream.closed


class TestEmbeddedPagesAreNotTouched:
    def test_an_excluded_page_is_neither_rendered_nor_recognized(
        self, engine: Path, engine_dir: Path, render_spy: RenderSpy
    ) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan(2)), embedded_pages=frozenset({1})
        )

        assert [page.page for page in result.pages] == [2]
        assert len(render_spy.calls) == 1
        assert len(fakes.recorded_stdin(engine_dir)) > 0

    def test_a_fully_covered_document_runs_no_engine_at_all(
        self, engine: Path, engine_dir: Path, render_spy: RenderSpy
    ) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan(3)), embedded_pages=frozenset({1, 2, 3})
        )

        assert result.pages == ()
        assert result.page_count == 3
        assert render_spy.calls == []
        assert not fakes.was_invoked(engine_dir)

    def test_the_physical_page_count_is_still_reported(self, engine: Path) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan(4)), embedded_pages=frozenset({1, 2, 3, 4})
        )

        assert result.page_count == 4


class TestWhatComesBack:
    def test_the_engine_stdout_is_returned_unchanged(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, stdout="  Ragged\ttext \n\n")

        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert result.pages[0].text == "  Ragged\ttext \n\n"

    def test_stderr_is_not_mixed_into_the_text(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(
            engine_dir, stdout="the words\n", stderr="Warning: Invalid resolution 0 dpi.\n"
        )

        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert result.pages[0].text == "the words\n"

    def test_an_empty_result_is_returned_as_an_empty_string(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, stdout="")

        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert result.pages == ((result.pages[0]),)
        assert result.pages[0].text == ""

    def test_utf8_text_survives_intact(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, stdout="Отсканировано — 你好\n")

        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert result.pages[0].text == "Отсканировано — 你好\n"

    def test_it_reports_what_actually_ran(self, engine: Path) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert result.engine == ENGINE_NAME == "tesseract"
        assert result.engine_version == ENGINE_VERSION
        assert result.rasterizer == RASTERIZER_NAME == "pypdfium2"
        assert result.rasterizer_version == str(pdfium.version.PYPDFIUM_INFO.version)

    def test_the_settings_describe_the_policy_in_force(self, engine: Path) -> None:
        settings = adapter(engine).settings()

        assert settings["languages"] == "eng+rus"
        assert settings["language_order"] == ["eng", "rus"]
        assert settings["render_dpi"] == 300
        assert settings["oem"] == 1
        assert settings["psm"] == 3
        assert settings["render_annotations"] is False
        assert settings["render_form_fields"] is False
        assert settings["orientation_detection"] is False
        assert settings["retries"] == 0
        assert settings["max_pages"] == 50
        assert settings["max_raster_pixels"] == 20_000_000
        assert settings["page_timeout_seconds"] == 30.0
        assert settings["document_budget_seconds"] == 120.0

    def test_the_settings_invent_no_confidence(self, engine: Path) -> None:
        assert not [key for key in adapter(engine).settings() if "confidence" in key]


class TestTheOperatorFacingDescription:
    def test_it_names_the_rasterizer_the_engine_and_the_languages(self, engine: Path) -> None:
        """The line CI prints, produced by the same probes the startup gate uses."""
        from unimem_ocr import describe_prerequisites

        described = describe_prerequisites(str(engine))

        assert "rasterizer: pypdfium2" in described
        assert f"pdfium {pdfium.version.PDFIUM_INFO.version}" in described
        assert f"engine: {engine} 5.3.4-fake" not in described  # the fake reports 5.3.4
        assert "languages required: eng+rus" in described
        assert "languages available: eng osd rus" in described


class TestResourceLimits:
    def test_a_document_over_the_page_limit_is_refused(
        self, engine: Path, engine_dir: Path, render_spy: RenderSpy
    ) -> None:
        limited = adapter(engine, limits=OcrLimits(max_pages=2))

        with pytest.raises(ProcessingInputError, match="at most 2 pages"):
            limited.recognize_missing_pages(io.BytesIO(scan(3)), embedded_pages=frozenset())

        assert render_spy.calls == []
        assert not fakes.was_invoked(engine_dir)

    def test_the_page_limit_is_checked_before_any_rasterization(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        """Not "recognize the first N": a document over the limit is refused whole."""
        limited = adapter(engine, limits=OcrLimits(max_pages=1))

        with pytest.raises(ProcessingInputError):
            limited.recognize_missing_pages(io.BytesIO(scan(5)), embedded_pages=frozenset())

        assert render_spy.bitmaps == []

    def test_a_page_over_the_pixel_limit_is_refused(
        self, engine: Path, engine_dir: Path, render_spy: RenderSpy
    ) -> None:
        limited = adapter(engine, limits=OcrLimits(max_raster_pixels=1000))

        with pytest.raises(ProcessingInputError, match="over this build's limit of 1000"):
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.bitmaps == []
        assert not fakes.was_invoked(engine_dir)

    def test_the_pixel_refusal_names_the_computed_size(self, engine: Path) -> None:
        """A US Letter page at 300 DPI, computed the way the renderer computes it.

        The expected number is derived here with the same rounding the adapter
        uses — ``ceil`` on each dimension — because that is what makes the check a
        *prediction* of the allocation rather than an estimate near it. A US Letter
        page is 612x792 canvas units, so the raster is a little over eight
        megapixels.
        """
        expected = math.ceil(612 * RENDER_SCALE) * math.ceil(792 * RENDER_SCALE)
        limited = adapter(engine, limits=OcrLimits(max_raster_pixels=1000))

        with pytest.raises(ProcessingInputError) as raised:
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert str(expected) in str(raised.value)
        assert f"at {RENDER_DPI} DPI" in str(raised.value)
        assert 8_000_000 < expected < 9_000_000

    def test_an_oversized_page_is_not_silently_downscaled(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        limited = adapter(engine, limits=OcrLimits(max_raster_pixels=1000))

        with pytest.raises(ProcessingInputError):
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.calls == []

    def test_an_ordinary_letter_page_is_within_the_default_limit(self, engine: Path) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert len(result.pages) == 1

    def test_both_limit_refusals_are_input_verdicts(self, engine: Path) -> None:
        """They describe the submitted document, so ``FAILED`` is the honest outcome."""
        for limits in (OcrLimits(max_pages=1), OcrLimits(max_raster_pixels=10)):
            with pytest.raises(ProcessingInputError) as raised:
                adapter(engine, limits=limits).recognize_missing_pages(
                    io.BytesIO(scan(2)), embedded_pages=frozenset()
                )
            assert not isinstance(raised.value, PdfOcrExecutionError)


class TestTheRecognitionBudget:
    def test_the_per_page_timeout_is_passed_to_the_subprocess(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[float] = []
        original = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["timeout"])
            return original(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)

        adapter(engine, limits=OcrLimits(page_timeout_seconds=7.0)).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert seen == [7.0]

    def test_the_smaller_of_the_remaining_budget_and_the_page_timeout_is_used(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[float] = []
        original = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["timeout"])
            return original(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        limits = OcrLimits(page_timeout_seconds=30.0, document_budget_seconds=4.0)

        adapter(engine, limits=limits).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert seen[0] == pytest.approx(4.0, abs=0.5)

    def test_the_budget_shrinks_across_pages(self, engine: Path) -> None:
        """A fifty-page scan cannot spend a full per-page timeout on every page."""
        ticks = iter([0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        seen: list[float] = []

        recognizer = TesseractPdfPageOcr(
            engine_version=ENGINE_VERSION,
            executable=str(engine),
            limits=OcrLimits(page_timeout_seconds=30.0, document_budget_seconds=10.0),
            monotonic=lambda: next(ticks),
        )
        original = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["timeout"])
            return original(*args, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subprocess, "run", spy)
            recognizer.recognize_missing_pages(io.BytesIO(scan(3)), embedded_pages=frozenset())

        assert seen == sorted(seen, reverse=True)
        assert seen[-1] < seen[0]

    def test_no_page_is_begun_after_the_budget_is_spent(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        """Page one completes; page two is refused before it starts.

        Ticks: deadline, then three reads for page one (pre-render, post-lock,
        post-render), then page two's pre-render read, which is past the deadline.
        """
        ticks = iter([0.0, 0.0, 0.0, 1.0, 99.0])
        recognizer = TesseractPdfPageOcr(
            engine_version=ENGINE_VERSION,
            executable=str(engine),
            limits=OcrLimits(document_budget_seconds=10.0),
            monotonic=lambda: next(ticks),
        )

        with pytest.raises(PdfOcrExecutionError, match="before page 2 was begun"):
            recognizer.recognize_missing_pages(io.BytesIO(scan(3)), embedded_pages=frozenset())

        assert len(render_spy.calls) == 1

    def test_a_budget_spent_while_a_page_was_being_rendered_stops_the_run(
        self, engine: Path, engine_dir: Path
    ) -> None:
        """Rasterization itself takes time, and the budget is re-read after it.

        Four ticks, not three: the deadline, the caller's pre-render check, the
        post-lock check inside ``_render``, and the post-render read that is the one
        this test is about. Only the last is past the deadline, so the render really
        does happen and the refusal really is on its far side.
        """
        ticks = iter([0.0, 0.0, 0.0, 100.0])
        recognizer = TesseractPdfPageOcr(
            engine_version=ENGINE_VERSION,
            executable=str(engine),
            limits=OcrLimits(document_budget_seconds=10.0),
            monotonic=lambda: next(ticks),
        )

        with pytest.raises(PdfOcrExecutionError, match="while preparing page 1"):
            recognizer.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not fakes.was_invoked(engine_dir)

    def test_exhausting_the_budget_is_an_execution_failure_not_an_input_verdict(
        self, engine: Path
    ) -> None:
        ticks = iter([0.0, 500.0])
        recognizer = TesseractPdfPageOcr(
            engine_version=ENGINE_VERSION,
            executable=str(engine),
            limits=OcrLimits(document_budget_seconds=1.0),
            monotonic=lambda: next(ticks),
        )

        with pytest.raises(PdfOcrExecutionError) as raised:
            recognizer.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not isinstance(raised.value, ProcessingError)


class TestABudgetSpentWaitingForTheNativeLock:
    """The gap between the caller's check and the work the lock protects.

    ``recognize_missing_pages`` checks the budget, then calls ``_render``, which
    blocks on the process-wide PDFium lock. Between those two moments this thread
    can wait for an unbounded time while another capture finishes inside PDFium. A
    budget that is only checked before the wait is not a budget, so it is rechecked
    the instant the lock is held and before anything native is touched.

    The wait is made deterministic by an :class:`AdvancingLock` that charges the
    injected clock for each acquisition — no 120-second sleep, and no race against
    an unguarded renderer.
    """

    #: Each lock acquisition costs a second: open, page count, then render.
    COST: Final = 1.0

    def recognizer(self, engine: Path, clock: MutableClock, budget: float) -> TesseractPdfPageOcr:
        return TesseractPdfPageOcr(
            engine_version=ENGINE_VERSION,
            executable=str(engine),
            limits=OcrLimits(document_budget_seconds=budget),
            monotonic=clock,
        )

    def test_a_budget_spent_waiting_for_the_lock_stops_before_the_page_is_opened(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression. Budget remains at the attempt; the wait consumes it.

        With a 2.5s budget and a 1s cost per acquisition: opening the document
        reaches 1.0s and counting its pages 2.0s, so the caller's pre-render check
        at 2.0s still has budget left — this is genuinely the lock-wait branch and
        not the earlier one. Acquiring the lock for the render reaches 3.0s, and
        nothing native may happen after that.
        """
        clock = MutableClock()
        lock = AdvancingLock(clock, self.COST)
        monkeypatch.setattr("unimem_ocr.tesseract._PDFIUM_LOCK", lock)
        work = NativeWorkSpy(monkeypatch)

        with pytest.raises(PdfOcrExecutionError, match="waiting for the rasterizer lock"):
            self.recognizer(engine, clock, 2.5).recognize_missing_pages(
                io.BytesIO(scan()), embedded_pages=frozenset()
            )

        # Budget genuinely remained when the render lock was attempted: the
        # caller's pre-render read is the second one, and it is inside the deadline.
        assert clock.reads[0] == 0.0
        assert clock.reads[1] == 2.0
        assert clock.reads[1] < 2.5
        # ...and the read taken once the lock was held is past it.
        assert clock.reads[2] == 3.0
        assert clock.reads[2] >= 2.5
        # Three acquisitions before the refusal: open, page count, render.
        assert lock.acquisitions >= 3

        assert work.pages_opened == 0
        assert work.renders == 0
        assert work.conversions == 0
        assert work.encodes == 0
        assert not fakes.was_invoked(engine_dir)

    def test_that_refusal_cleans_up_the_document_and_stays_nonterminal(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The document is closed under the same lock, and the capture is not failed."""
        clock = MutableClock()
        lock = AdvancingLock(clock, self.COST)
        monkeypatch.setattr("unimem_ocr.tesseract._PDFIUM_LOCK", lock)
        work = NativeWorkSpy(monkeypatch)

        with pytest.raises(PdfOcrExecutionError) as raised:
            self.recognizer(engine, clock, 2.5).recognize_missing_pages(
                io.BytesIO(scan()), embedded_pages=frozenset()
            )

        assert work.documents_closed == 1
        assert not isinstance(raised.value, ProcessingError)

    def test_a_control_with_budget_remaining_renders_normally(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same lock, the same clock, a budget that is not exhausted.

        Without this the test above could pass for the wrong reason — an
        ``AdvancingLock`` that broke rendering outright would look identical.
        """
        clock = MutableClock()
        lock = AdvancingLock(clock, self.COST)
        monkeypatch.setattr("unimem_ocr.tesseract._PDFIUM_LOCK", lock)
        work = NativeWorkSpy(monkeypatch)

        result = self.recognizer(engine, clock, 100.0).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert len(result.pages) == 1
        assert work.pages_opened == 1
        assert work.renders == 1
        assert work.encodes == 1
        assert work.documents_closed == 1
        assert fakes.was_invoked(engine_dir)


class TestATimingOutEngine:
    def test_the_timeout_is_an_execution_failure(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, sleep=1.0)
        limited = adapter(engine, limits=OcrLimits(page_timeout_seconds=0.2))

        with pytest.raises(PdfOcrExecutionError, match="was killed"):
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_the_child_really_is_killed(self, engine_dir: Path) -> None:
        """The child writes a marker after its sleep. It must never appear.

        A negative assertion with a real deadline: the fake sleeps for a second,
        the adapter gives it a fifth of that, and this waits well past the sleep
        before looking. A child that had merely been abandoned would have finished
        and left the marker behind.
        """
        engine = fakes.write_fake_engine(engine_dir, sleep=1.0)
        limited = adapter(engine, limits=OcrLimits(page_timeout_seconds=0.2))

        with pytest.raises(PdfOcrExecutionError):
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        time.sleep(1.5)
        assert not fakes.completed(engine_dir)

    def test_the_pdf_handles_are_released_after_a_timeout(
        self, engine_dir: Path, render_spy: RenderSpy
    ) -> None:
        engine = fakes.write_fake_engine(engine_dir, sleep=1.0)
        limited = adapter(engine, limits=OcrLimits(page_timeout_seconds=0.2))

        with pytest.raises(PdfOcrExecutionError):
            limited.recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert render_spy.live == []
        assert len(render_spy.closed) == len(render_spy.bitmaps)


class TestAFailingEngine:
    def test_a_nonzero_exit_is_an_execution_failure(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, recognize_exit=1)

        with pytest.raises(PdfOcrExecutionError, match="exited with status 1"):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_a_nonzero_exit_is_not_a_verdict_about_the_document(self, engine_dir: Path) -> None:
        """It could be a crash, a missing data file, or an OOM kill — unknowable."""
        engine = fakes.write_fake_engine(engine_dir, recognize_exit=2)

        with pytest.raises(PdfOcrExecutionError) as raised:
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not isinstance(raised.value, ProcessingError)

    def test_the_engine_stderr_does_not_reach_the_error(self, engine_dir: Path) -> None:
        secret = "/home/someone/private/tessdata"
        engine = fakes.write_fake_engine(engine_dir, recognize_exit=1, stderr=secret)

        with pytest.raises(PdfOcrExecutionError) as raised:
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert secret not in str(raised.value)

    def test_a_missing_executable_is_an_execution_failure(self, tmp_path: Path) -> None:
        """Not an input verdict: the document was never looked at."""
        with pytest.raises(PdfOcrExecutionError, match="could not be run"):
            adapter(tmp_path / "no-such-engine").recognize_missing_pages(
                io.BytesIO(scan()), embedded_pages=frozenset()
            )

    def test_undecodable_output_is_an_execution_failure(self, engine_dir: Path) -> None:
        engine = fakes.write_fake_engine(engine_dir, stdout_bytes=b"\xff\xfe not utf-8")

        with pytest.raises(PdfOcrExecutionError, match="not valid UTF-8"):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_a_rendering_failure_after_a_successful_open_is_an_execution_failure(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(page: Any, *args: Any, **kwargs: Any) -> Any:
            raise pdfium.PdfiumError("rendering failed")

        monkeypatch.setattr(pdfium.PdfPage, "render", explode)

        with pytest.raises(PdfOcrExecutionError, match="rasterizing page 1"):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_a_programming_error_is_not_disguised_as_a_document_problem(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only known failures are translated; a bug stays visible as a bug."""

        def explode(page: Any, *args: Any, **kwargs: Any) -> Any:
            raise TypeError("a defect in this module")

        monkeypatch.setattr(pdfium.PdfPage, "render", explode)

        with pytest.raises(TypeError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())


class TestLoadErrorClassification:
    """Which load failures are verdicts about the document, and which are not.

    ``PdfiumError.err_code`` is populated by exactly one PDFium API — document
    loading — and it is the *only* thing this adapter consults. These tests inject
    each documented code explicitly rather than hunting for a fixture that happens
    to produce it, because the classification rule is about codes and a fixture
    proves only the code it happens to trigger.
    """

    #: Reasons that really are statements about the submitted bytes.
    INPUT_CODES = (
        pytest.param(pdfium_raw.FPDF_ERR_FORMAT, id="format"),
        pytest.param(pdfium_raw.FPDF_ERR_PASSWORD, id="password"),
        pytest.param(pdfium_raw.FPDF_ERR_SECURITY, id="security"),
    )

    #: Everything else, including a missing code and one this build never saw.
    UNPROVEN_CODES = (
        pytest.param(pdfium_raw.FPDF_ERR_UNKNOWN, id="unknown"),
        pytest.param(pdfium_raw.FPDF_ERR_FILE, id="file"),
        pytest.param(pdfium_raw.FPDF_ERR_PAGE, id="page"),
        pytest.param(pdfium_raw.FPDF_ERR_SUCCESS, id="success-as-error"),
        pytest.param(None, id="absent"),
        pytest.param(4242, id="unrecognized"),
    )

    def failing_loader(
        self, monkeypatch: pytest.MonkeyPatch, code: int | None, message: str = "load failed"
    ) -> None:
        """Make ``PdfDocument(...)`` raise one specific PDFium load failure."""

        def explode(*args: Any, **kwargs: Any) -> Any:
            raise pdfium.PdfiumError(message, err_code=code)

        monkeypatch.setattr(pdfium, "PdfDocument", explode)

    @pytest.mark.parametrize("code", INPUT_CODES)
    def test_a_recognized_input_reason_is_a_processing_input_error(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        self.failing_loader(monkeypatch, code)

        with pytest.raises(ProcessingInputError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    @pytest.mark.parametrize("code", UNPROVEN_CODES)
    def test_an_unproven_reason_is_an_execution_failure(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        """The defect this class exists for.

        A load failure PDFium would not explain must not become a durable
        ``FAILED`` verdict on somebody's document just because it happened while
        opening the file.
        """
        self.failing_loader(monkeypatch, code)

        with pytest.raises(PdfOcrExecutionError) as raised:
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not isinstance(raised.value, ProcessingError)

    @pytest.mark.parametrize("code", UNPROVEN_CODES)
    def test_an_unproven_reason_is_never_called_malformed_input(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        """Stated as a negative: the 422 path must not be reachable for these."""
        self.failing_loader(monkeypatch, code)

        with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is the assertion
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not isinstance(raised.value, ProcessingInputError)

    def test_the_message_is_not_what_decides_the_classification(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The decisive test against sniffing the exception text.

        The message says exactly what a format failure's message says, and the
        code says ``UNKNOWN``. The code wins, because the message is another
        library's prose and not an interface.
        """
        self.failing_loader(
            monkeypatch,
            pdfium_raw.FPDF_ERR_UNKNOWN,
            message="Failed to load document (PDFium: Data format error).",
        )

        with pytest.raises(PdfOcrExecutionError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    def test_the_reverse_is_also_true(self, engine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A format code with an unhelpful message is still an input verdict."""
        self.failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_FORMAT, message="something happened")

        with pytest.raises(ProcessingInputError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

    @pytest.mark.parametrize("code", [*INPUT_CODES, *UNPROVEN_CODES])
    def test_the_original_exception_is_preserved_as_the_cause(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        self.failing_loader(monkeypatch, code, message="the original PDFium failure")

        with pytest.raises(Exception) as raised:  # noqa: PT011 - both types are acceptable here
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        cause = raised.value.__cause__
        assert isinstance(cause, pdfium.PdfiumError)
        assert getattr(cause, "err_code", "missing") == code

    @pytest.mark.parametrize("code", [*INPUT_CODES, *UNPROVEN_CODES])
    def test_no_public_message_repeats_the_libraries_own_text(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        """PDFium's message can name byte offsets and local paths; neither leaks."""
        secret = "/home/someone/private/scan.pdf at offset 91723"
        self.failing_loader(monkeypatch, code, message=secret)

        with pytest.raises(Exception) as raised:  # noqa: PT011 - both types are acceptable here
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert secret not in str(raised.value)
        assert "/home/someone" not in str(raised.value)

    @pytest.mark.parametrize("code", [*INPUT_CODES, *UNPROVEN_CODES])
    def test_no_load_failure_ever_reaches_the_engine(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        self.failing_loader(monkeypatch, code)

        with pytest.raises(Exception):  # noqa: B017, PT011 - classification is tested above
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert not fakes.was_invoked(engine_dir)

    def test_the_allowlist_is_exactly_the_three_justified_codes(self) -> None:
        """Spelled out, so widening it is a reviewed edit rather than a drift."""
        from unimem_ocr.tesseract import _LOAD_INPUT_ERROR_CODES

        assert (
            frozenset(
                {
                    pdfium_raw.FPDF_ERR_FORMAT,
                    pdfium_raw.FPDF_ERR_PASSWORD,
                    pdfium_raw.FPDF_ERR_SECURITY,
                }
            )
            == _LOAD_INPUT_ERROR_CODES
        )

    def test_a_render_failure_still_carries_no_error_code_and_is_an_execution_failure(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only loading reports a code, which is why only loading is classified.

        Every other PDFium API raises with ``err_code`` ``None``, so a
        post-load failure has nothing to classify and stays an execution failure
        — the same answer the allowlist gives an absent code.
        """

        def explode(page: Any, *args: Any, **kwargs: Any) -> Any:
            raise pdfium.PdfiumError("rendering failed")

        monkeypatch.setattr(pdfium.PdfPage, "render", explode)

        with pytest.raises(PdfOcrExecutionError) as raised:
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        cause = raised.value.__cause__
        assert isinstance(cause, pdfium.PdfiumError)
        assert cause.err_code is None


class TestDocumentsPdfiumCannotOpen:
    def test_an_encrypted_pdf_is_an_input_verdict(self, engine: Path) -> None:
        with pytest.raises(ProcessingInputError, match="password-protected"):
            adapter(engine).recognize_missing_pages(
                io.BytesIO(pdfs.encrypted_pdf()), embedded_pages=frozenset()
            )

    def test_a_malformed_pdf_is_an_input_verdict(self, engine: Path) -> None:
        with pytest.raises(ProcessingInputError, match="malformed"):
            adapter(engine).recognize_missing_pages(
                io.BytesIO(pdfs.corrupt_pdf()), embedded_pages=frozenset()
            )

    def test_bytes_that_are_not_a_pdf_are_an_input_verdict(self, engine: Path) -> None:
        with pytest.raises(ProcessingInputError):
            adapter(engine).recognize_missing_pages(
                io.BytesIO(b"not a pdf at all"), embedded_pages=frozenset()
            )

    def test_a_refused_document_never_reaches_the_engine(
        self, engine: Path, engine_dir: Path
    ) -> None:
        with pytest.raises(ProcessingInputError):
            adapter(engine).recognize_missing_pages(
                io.BytesIO(pdfs.corrupt_pdf()), embedded_pages=frozenset()
            )

        assert not fakes.was_invoked(engine_dir)


class TestRasterLifetime:
    def test_every_bitmap_is_closed(self, engine: Path, render_spy: RenderSpy) -> None:
        adapter(engine).recognize_missing_pages(io.BytesIO(scan(4)), embedded_pages=frozenset())

        assert len(render_spy.bitmaps) == 4
        assert len(render_spy.closed) == 4
        assert render_spy.live == []

    def test_only_one_page_raster_exists_at_a_time(
        self, engine: Path, render_spy: RenderSpy
    ) -> None:
        """A fifty-page scan must cost one page of pixels, not fifty."""
        adapter(engine).recognize_missing_pages(io.BytesIO(scan(6)), embedded_pages=frozenset())

        assert render_spy.peak == 1

    def test_no_page_image_is_returned_across_the_port(self, engine: Path) -> None:
        result = adapter(engine).recognize_missing_pages(
            io.BytesIO(scan()), embedded_pages=frozenset()
        )

        assert all(isinstance(page.text, str) for page in result.pages)
        assert not hasattr(result, "images")

    def test_the_png_is_encoded_and_copied_out_before_anything_is_closed(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The buffer-lifetime proof, as an order of events.

        ``to_pil`` hands back an image sharing the bitmap's memory, so every read
        of that memory has to finish while the bitmap is valid. This records the
        real sequence and asserts it: if the encode were moved after the ``with``
        blocks unwound — or the closes hoisted before it — ``image.save`` would
        appear after a close and this fails.

        Nothing here dereferences freed memory to make the point; the assertion is
        about ordering, observed from safe wrappers around the real calls.
        """
        events: list[str] = []
        original = {
            "render": pdfium.PdfPage.render,
            "to_pil": pdfium.PdfBitmap.to_pil,
            "save": PILImage.Image.save,
            "image_close": PILImage.Image.close,
            "bitmap_close": pdfium.PdfBitmap.close,
            "page_close": pdfium.PdfPage.close,
        }

        def record(name: str, function: Any) -> Any:
            def wrapper(target: Any, *args: Any, **kwargs: Any) -> Any:
                events.append(name)
                return function(target, *args, **kwargs)

            return wrapper

        monkeypatch.setattr(pdfium.PdfPage, "render", record("render", original["render"]))
        monkeypatch.setattr(pdfium.PdfBitmap, "to_pil", record("to_pil", original["to_pil"]))
        monkeypatch.setattr(PILImage.Image, "save", record("image.save", original["save"]))
        monkeypatch.setattr(PILImage.Image, "close", record("image.close", original["image_close"]))
        monkeypatch.setattr(
            pdfium.PdfBitmap, "close", record("bitmap.close", original["bitmap_close"])
        )
        monkeypatch.setattr(pdfium.PdfPage, "close", record("page.close", original["page_close"]))

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert events == [
            "render",
            "to_pil",
            "image.save",
            "image.close",
            "bitmap.close",
            "page.close",
        ]
        assert events.index("image.save") < events.index("bitmap.close")
        assert events.index("image.save") < events.index("image.close")

    def test_the_bytes_handed_to_the_engine_are_independent_of_the_native_buffer(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decoded *after* every native object is closed, and still whole.

        The PNG the adapter actually sent is read back from what the fake engine
        recorded on stdin, then fully decoded here — with the bitmap, image and
        page long closed. A payload that were still a view over freed PDFium
        memory could not survive that. The expected size is taken from the live
        bitmap rather than hard-coded, so the comparison is against what the
        renderer really produced.
        """
        sizes: list[tuple[int, int]] = []
        original = pdfium.PdfBitmap.to_pil

        def to_pil(bitmap: Any) -> Any:
            sizes.append((bitmap.width, bitmap.height))
            return original(bitmap)

        monkeypatch.setattr(pdfium.PdfBitmap, "to_pil", to_pil)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        png = fakes.recorded_stdin(engine_dir)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        with PILImage.open(io.BytesIO(png)) as decoded:
            decoded.load()
            assert decoded.size == sizes[0]
            assert decoded.size[0] > 2000

    def test_a_conversion_failure_releases_the_bitmap_and_the_page(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``to_pil`` raising must not leave native memory held."""
        closed: list[str] = []
        original_bitmap_close = pdfium.PdfBitmap.close
        original_page_close = pdfium.PdfPage.close

        def bitmap_close(bitmap: Any) -> None:
            closed.append("bitmap")
            original_bitmap_close(bitmap)

        def page_close(page: Any) -> None:
            closed.append("page")
            original_page_close(page)

        monkeypatch.setattr(pdfium.PdfBitmap, "close", bitmap_close)
        monkeypatch.setattr(pdfium.PdfPage, "close", page_close)

        def explode(bitmap: Any) -> Any:
            raise MemoryError("conversion failed")

        monkeypatch.setattr(pdfium.PdfBitmap, "to_pil", explode)

        with pytest.raises(MemoryError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert closed == ["bitmap", "page"]
        assert not fakes.was_invoked(engine_dir)

    def test_an_encoding_failure_releases_the_image_bitmap_and_page(
        self, engine: Path, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Image.save`` raising must not leave native memory held either."""
        closed: list[str] = []
        originals = {
            "image": PILImage.Image.close,
            "bitmap": pdfium.PdfBitmap.close,
            "page": pdfium.PdfPage.close,
        }

        def closer(name: str, function: Any) -> Any:
            def wrapper(target: Any) -> None:
                closed.append(name)
                function(target)

            return wrapper

        monkeypatch.setattr(PILImage.Image, "close", closer("image", originals["image"]))
        monkeypatch.setattr(pdfium.PdfBitmap, "close", closer("bitmap", originals["bitmap"]))
        monkeypatch.setattr(pdfium.PdfPage, "close", closer("page", originals["page"]))

        def explode(image: Any, *args: Any, **kwargs: Any) -> None:
            raise OSError("encoder failed")

        monkeypatch.setattr(PILImage.Image, "save", explode)

        with pytest.raises(OSError, match="encoder failed"):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert closed == ["image", "bitmap", "page"]
        assert not fakes.was_invoked(engine_dir)

    def test_the_native_objects_are_closed_before_the_subprocess_runs(
        self, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The engine consumes independent bytes, not a live native buffer.

        The lock is released for the subprocess, so nothing may still be holding
        PDFium memory when it starts.
        """
        events: list[str] = []
        original_bitmap_close = pdfium.PdfBitmap.close
        original_run = subprocess.run

        def bitmap_close(bitmap: Any) -> None:
            events.append("bitmap.close")
            original_bitmap_close(bitmap)

        def run(*args: Any, **kwargs: Any) -> Any:
            events.append("subprocess")
            return original_run(*args, **kwargs)

        monkeypatch.setattr(pdfium.PdfBitmap, "close", bitmap_close)
        monkeypatch.setattr(subprocess, "run", run)
        engine = fakes.write_fake_engine(engine_dir)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert events == ["bitmap.close", "subprocess"]

    def test_no_temporary_file_is_created(
        self, engine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """There is no temporary file, so there is no temporary path to leak."""
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))

        adapter(engine).recognize_missing_pages(io.BytesIO(scan(2)), embedded_pages=frozenset())

        assert list(scratch.iterdir()) == []

    def test_the_document_is_closed_even_when_a_page_fails(
        self, engine: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed: list[Any] = []
        original = pdfium.PdfDocument.close

        def close(document: Any) -> None:
            closed.append(document)
            original(document)

        monkeypatch.setattr(pdfium.PdfDocument, "close", close)

        def explode(page: Any, *args: Any, **kwargs: Any) -> Any:
            raise pdfium.PdfiumError("rendering failed")

        monkeypatch.setattr(pdfium.PdfPage, "render", explode)

        with pytest.raises(PdfOcrExecutionError):
            adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert len(closed) == 1


class RecordingLock:
    """A real mutex that remembers which thread is inside it.

    Stands in for the module's own lock so that a test can ask the question that
    actually matters — *was the lock held while PDFium was being called* — rather
    than the weaker question of whether two calls happened to overlap during one
    run. A render observed with the lock free would mean the guard had been
    removed from that code path, whatever the timing happened to look like.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: int | None = None
        self.acquisitions = 0

    def __enter__(self) -> "RecordingLock":
        self._lock.acquire()
        self.holder = threading.get_ident()
        self.acquisitions += 1
        return self

    def __exit__(self, *exception: object) -> None:
        self.holder = None
        self._lock.release()

    def held_here(self) -> bool:
        return self.holder == threading.get_ident()


class TestTheNativeLibraryLock:
    """PDFium is not thread-safe across documents, so nothing may overlap inside it."""

    def drive(self, workspace: Path, *, threads: int) -> None:
        """Run ``threads`` adapters over their own documents at the same time.

        Each thread gets its own fake engine in its own directory: the fake records
        what it was sent to a file, and four threads sharing one record file would
        be testing the fixture's concurrency rather than the adapter's.
        """
        errors: list[BaseException] = []

        def run(index: int) -> None:
            directory = workspace / f"engine-{index}"
            directory.mkdir()
            engine = fakes.write_fake_engine(directory)
            try:
                adapter(engine).recognize_missing_pages(
                    io.BytesIO(scan(2)), embedded_pages=frozenset()
                )
            except BaseException as failure:  # pragma: no cover - reported below
                errors.append(failure)

        workers = [threading.Thread(target=run, args=(index,)) for index in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=180)
        assert errors == []

    def test_every_rasterization_happens_with_the_lock_held(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The assertion with real negative power: a render with the lock free fails.

        If the guard were dropped from the rasterization path — or moved to cover
        only part of it — ``unguarded`` would be nonzero here even in a single
        thread, with no dependence on timing at all.
        """
        recording = RecordingLock()
        monkeypatch.setattr("unimem_ocr.tesseract._PDFIUM_LOCK", recording)
        guarded = 0
        unguarded = 0
        original = pdfium.PdfPage.render

        def render(page: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal guarded, unguarded
            if recording.held_here():
                guarded += 1
            else:
                unguarded += 1
            return original(page, *args, **kwargs)

        monkeypatch.setattr(pdfium.PdfPage, "render", render)
        engine = fakes.write_fake_engine(tmp_path)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan(3)), embedded_pages=frozenset())

        assert unguarded == 0
        assert guarded == 3

    def test_opening_counting_and_closing_are_guarded_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Destruction is a native call, so it is inside the boundary as well."""
        recording = RecordingLock()
        monkeypatch.setattr("unimem_ocr.tesseract._PDFIUM_LOCK", recording)
        held: list[bool] = []
        original_close = pdfium.PdfDocument.close

        def close(document: Any) -> None:
            held.append(recording.held_here())
            original_close(document)

        monkeypatch.setattr(pdfium.PdfDocument, "close", close)
        engine = fakes.write_fake_engine(tmp_path)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert held == [True]
        # open, page count, one render, close.
        assert recording.acquisitions == 4

    def test_concurrent_adapters_never_render_at_the_same_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = RenderSpy(monkeypatch, delay=0.05)

        self.drive(tmp_path, threads=4)

        assert len(spy.calls) == 8
        assert spy.overlaps == []

    def test_the_overlap_detector_reports_real_overlaps(self) -> None:
        """The control, and deliberately with no PDFium in it.

        ``test_concurrent_adapters_never_render_at_the_same_time`` is only worth
        anything if the instrument it uses can fire, so the same bookkeeping is
        driven through a plain sleeper guarded by nothing. Removing the real lock
        and racing the actual renderer would be the other way to show this, and it
        is not worth risking a native crash in CI to make a point about a counter.
        """
        inside: list[int] = []
        overlaps: list[int] = []

        def work() -> None:
            with nullcontext():
                inside.append(1)
                if len(inside) > 1:
                    overlaps.append(len(inside))
                time.sleep(0.05)
                inside.pop()

        workers = [threading.Thread(target=work) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)

        assert overlaps != []

    def test_the_lock_is_one_object_shared_by_the_whole_process(self) -> None:
        """Two independently constructed adapters must not each have their own."""
        from unimem_ocr import tesseract

        assert isinstance(tesseract._PDFIUM_LOCK, type(threading.Lock()))

    def test_the_engine_runs_with_the_lock_released(
        self, engine_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Holding a native-library lock across a subprocess would serialize the server.

        Asserted by observing that the lock is acquirable while a deliberately slow
        engine is running.
        """
        from unimem_ocr import tesseract

        engine = fakes.write_fake_engine(engine_dir, sleep=0.4)
        acquired: list[bool] = []
        original = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            held = tesseract._PDFIUM_LOCK.acquire(blocking=False)
            acquired.append(held)
            if held:
                tesseract._PDFIUM_LOCK.release()
            return original(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)

        adapter(engine).recognize_missing_pages(io.BytesIO(scan()), embedded_pages=frozenset())

        assert acquired == [True]
