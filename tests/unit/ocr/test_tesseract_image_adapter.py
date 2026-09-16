"""The direct-image Tesseract adapter: bounds, settings, and what reaches the child.

No real engine and no optional extra. The subprocess is a fake on disk — the same
one the PDF adapter's tests use — so the command line, the stdin bytes, the exit
status and the timeout are all genuinely observed rather than mocked. **This
module imports no rasterizer and no imaging library, and that is deliberate**:
these tests run on an ordinary installation, which is the first half of the claim
that direct-image OCR needs neither.

Two bounds are the substance of the file, and both are tested at their exact
edges rather than approximately. A limit that refuses at ``limit`` would reject
honest images; a limit that accepts ``limit + 1`` is not a limit. And both must
refuse *before* a child process exists, because a bound enforced after the work
is not a bound at all.
"""

import ast
import io
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, cast

import pytest

from core.processing.image_recognition import (
    ENCODED_BYTE_LIMIT,
    ENCODED_PIXEL_LIMIT,
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
)
from tests.unit.ocr import fakes
from unimem_ocr.image import ENGINE_NAME, TesseractImageOcr
from unimem_ocr.policy import DEFAULT_IMAGE_LIMITS, ImageOcrLimits

PNG_MIME_TYPE = "image/png"

#: A vector with no ``--dpi`` pair, which is the whole difference from the PDF one.
EXPECTED_IMAGE_ARGUMENTS = ["stdin", "stdout", "-l", "eng+rus", "--oem", "1", "--psm", "3"]


def adapter(engine: Path, **limit_overrides: object) -> TesseractImageOcr:
    limits = (
        DEFAULT_IMAGE_LIMITS
        if not limit_overrides
        else ImageOcrLimits(
            **{
                "max_encoded_pixels": DEFAULT_IMAGE_LIMITS.max_encoded_pixels,
                "max_encoded_bytes": DEFAULT_IMAGE_LIMITS.max_encoded_bytes,
                "recognition_timeout_seconds": DEFAULT_IMAGE_LIMITS.recognition_timeout_seconds,
                **limit_overrides,  # type: ignore[arg-type]
            }
        )
    )
    return TesseractImageOcr(engine_version="5.3.4", executable=str(engine), limits=limits)


def recognize(
    subject: TesseractImageOcr,
    data: bytes = b"\x89PNG\r\n\x1a\nbody",
    *,
    width: int = 640,
    height: int = 480,
) -> object:
    return subject.recognize_image(
        io.BytesIO(data), mime_type=PNG_MIME_TYPE, encoded_width=width, encoded_height=height
    )


class CountingStream:
    """A forward-only stream that records every read request it was given.

    The point is the *sizes*: a reader that asks for a full chunk when one byte of
    headroom remains has overshot the bound in memory even if it refuses
    afterwards, and only the request sizes show that.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0
        self.requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requests.append(size)
        chunk = self._data[self._position : self._position + size]
        self._position += len(chunk)
        return chunk

    @property
    def bytes_served(self) -> int:
        return self._position


class TestThePixelBound:
    def test_exactly_the_limit_is_allowed(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine), width=5_000, height=4_000)  # 20,000,000

        assert fakes.was_invoked(tmp_path)

    def test_one_pixel_over_the_limit_is_refused(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded) as raised:
            recognize(adapter(engine), width=5_000, height=4_001)

        assert raised.value.reason == ENCODED_PIXEL_LIMIT

    def test_the_refusal_carries_the_limit_that_applied(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded) as raised:
            recognize(adapter(engine, max_encoded_pixels=100), width=11, height=11)

        assert raised.value.limit == 100

    def test_the_engine_is_never_started(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded):
            recognize(adapter(engine), width=6_000, height=6_000)

        assert not fakes.was_invoked(tmp_path)

    def test_not_one_byte_of_the_stream_is_read(self, tmp_path: Path) -> None:
        """The refusal is computed from the header integers alone."""
        engine = fakes.write_fake_engine(tmp_path)
        stream = CountingStream(b"\x89PNG" * 1000)

        with pytest.raises(ImageOcrLimitExceeded):
            adapter(engine).recognize_image(
                cast(BinaryIO, stream),
                mime_type=PNG_MIME_TYPE,
                encoded_width=6_000,
                encoded_height=6_000,
            )

        assert stream.requests == []
        assert stream.bytes_served == 0

    def test_a_default_deployment_uses_twenty_million(self) -> None:
        assert DEFAULT_IMAGE_LIMITS.max_encoded_pixels == 20_000_000


class TestTheEncodedByteBound:
    def test_exactly_the_limit_is_allowed(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine, max_encoded_bytes=64), b"x" * 64)

        assert fakes.recorded_stdin(tmp_path) == b"x" * 64

    def test_one_byte_over_the_limit_is_refused(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded) as raised:
            recognize(adapter(engine, max_encoded_bytes=64), b"x" * 65)

        assert raised.value.reason == ENCODED_BYTE_LIMIT

    def test_the_refusal_carries_the_limit_that_applied(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded) as raised:
            recognize(adapter(engine, max_encoded_bytes=64), b"x" * 65)

        assert raised.value.limit == 64

    def test_the_engine_is_never_started_on_a_breach(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        with pytest.raises(ImageOcrLimitExceeded):
            recognize(adapter(engine, max_encoded_bytes=8), b"x" * 4096)

        assert not fakes.was_invoked(tmp_path)

    def test_the_reader_never_overshoots_by_more_than_one_byte(self, tmp_path: Path) -> None:
        """The narrowed final request, which is what keeps the bound a bound.

        A reader that asked for a whole chunk with one byte of headroom left would
        have pulled up to that chunk past the limit into memory before noticing.
        """
        engine = fakes.write_fake_engine(tmp_path)
        stream = CountingStream(b"x" * 100_000)

        with pytest.raises(ImageOcrLimitExceeded):
            adapter(engine, max_encoded_bytes=32).recognize_image(
                cast(BinaryIO, stream),
                mime_type=PNG_MIME_TYPE,
                encoded_width=10,
                encoded_height=10,
            )

        assert stream.bytes_served <= 33
        assert max(stream.requests) <= 33

    def test_a_request_is_never_larger_than_the_remaining_headroom_plus_one(
        self, tmp_path: Path
    ) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")
        stream = CountingStream(b"x" * 10)

        adapter(engine, max_encoded_bytes=1000).recognize_image(
            cast(BinaryIO, stream), mime_type=PNG_MIME_TYPE, encoded_width=4, encoded_height=4
        )

        assert all(size <= 1001 for size in stream.requests)

    def test_a_short_read_does_not_end_the_body_early(self, tmp_path: Path) -> None:
        """``read`` may legitimately return fewer bytes than asked for."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        class Dribbling:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._at = 0

            def read(self, size: int = -1) -> bytes:
                chunk = self._data[self._at : self._at + min(size, 3)]
                self._at += len(chunk)
                return chunk

        adapter(engine).recognize_image(
            cast(BinaryIO, Dribbling(b"abcdefghij")),
            mime_type=PNG_MIME_TYPE,
            encoded_width=4,
            encoded_height=4,
        )

        assert fakes.recorded_stdin(tmp_path) == b"abcdefghij"

    def test_it_works_on_a_forward_only_stream(self, tmp_path: Path) -> None:
        """Nothing seeks, so a non-seekable backend needs no second code path."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")
        stream = CountingStream(b"forward only bytes")

        adapter(engine).recognize_image(
            cast(BinaryIO, stream), mime_type=PNG_MIME_TYPE, encoded_width=4, encoded_height=4
        )

        assert fakes.recorded_stdin(tmp_path) == b"forward only bytes"

    def test_a_default_deployment_uses_sixty_four_mebibytes(self) -> None:
        assert DEFAULT_IMAGE_LIMITS.max_encoded_bytes == 64 * 1024 * 1024 == 67_108_864


class TestWhatReachesTheChild:
    def test_the_original_bytes_are_handed_over_unchanged(self, tmp_path: Path) -> None:
        """No decode, no re-encode, no normalization anywhere in this build."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")
        original = bytes(range(256)) * 4

        recognize(adapter(engine), original)

        assert fakes.recorded_stdin(tmp_path) == original

    def test_the_command_line_is_the_fixed_image_vector(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine))

        assert fakes.recorded_argv(tmp_path) == EXPECTED_IMAGE_ARGUMENTS

    def test_no_dpi_is_ever_passed(self, tmp_path: Path) -> None:
        """This build rasterized nothing and read no density, so it claims none."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine))

        assert "--dpi" not in fakes.recorded_argv(tmp_path)

    def test_no_path_of_any_kind_appears_in_the_arguments(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine))

        assert not any("/" in argument for argument in fakes.recorded_argv(tmp_path))

    def test_the_mime_type_does_not_reach_the_arguments(self, tmp_path: Path) -> None:
        """It is recorded, not acted on: the engine reads whichever format arrived."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        recognize(adapter(engine))

        assert PNG_MIME_TYPE not in fakes.recorded_argv(tmp_path)

    def test_no_temporary_file_is_created(self, tmp_path: Path) -> None:
        """The bytes go on stdin, so there is no temporary path to leak or guess."""
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")
        before = set(tmp_path.iterdir())

        recognize(adapter(engine))

        created = {path.name for path in set(tmp_path.iterdir()) - before}
        assert created <= {fakes.ARGV_RECORD, fakes.STDIN_RECORD, fakes.COMPLETION_MARKER}


class TestTheResult:
    def test_the_text_is_exactly_what_the_engine_printed(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="  HARBOUR  \n\n")

        answer = recognize(adapter(engine))

        assert answer.text == "  HARBOUR  \n\n"  # type: ignore[attr-defined]

    def test_an_empty_answer_is_returned_rather_than_raised(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="")

        assert recognize(adapter(engine)).text == ""  # type: ignore[attr-defined]

    def test_it_names_the_engine_and_the_probed_version(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")

        answer = recognize(adapter(engine))

        assert answer.engine == ENGINE_NAME == "tesseract"  # type: ignore[attr-defined]
        assert answer.engine_version == "5.3.4"  # type: ignore[attr-defined]


class TestTheRecordedSettings:
    @pytest.fixture
    def settings(self, tmp_path: Path) -> dict[str, object]:
        engine = fakes.write_fake_engine(tmp_path, stdout="ok\n")
        return dict(adapter(engine).settings())

    def test_the_languages_are_eng_then_rus(self, settings: dict[str, object]) -> None:
        assert settings["languages"] == "eng+rus"
        assert settings["language_order"] == ["eng", "rus"]

    def test_the_engine_modes_are_recorded(self, settings: dict[str, object]) -> None:
        assert settings["oem"] == 1
        assert settings["psm"] == 3

    def test_it_records_that_no_dpi_was_supplied(self, settings: dict[str, object]) -> None:
        """A true statement about the invocation, not a claim about the picture."""
        assert settings["dpi_supplied"] is False

    def test_it_records_that_the_engine_read_the_original_bytes(
        self, settings: dict[str, object]
    ) -> None:
        assert settings["input"] == "original_encoded_bytes"

    def test_nothing_is_guessed_or_corrected(self, settings: dict[str, object]) -> None:
        assert settings["orientation_detection"] is False
        assert settings["deskew"] is False
        assert settings["retries"] == 0

    def test_the_limits_in_force_are_recorded(self, settings: dict[str, object]) -> None:
        assert settings["max_encoded_pixels"] == 20_000_000
        assert settings["max_encoded_bytes"] == 67_108_864
        assert settings["recognition_timeout_seconds"] == 30.0

    def test_there_is_no_rasterizer(self, settings: dict[str, object]) -> None:
        """Nothing rasterizes here, so there is no such fact to report."""
        assert not any("raster" in key for key in settings)

    def test_there_is_no_render_dpi(self, settings: dict[str, object]) -> None:
        assert "render_dpi" not in settings

    def test_there_is_no_confidence_or_language_detection(
        self, settings: dict[str, object]
    ) -> None:
        assert "confidence" not in settings
        assert "detected_language" not in settings


class TestInvocationFailures:
    def test_a_missing_engine_is_an_execution_failure(self, tmp_path: Path) -> None:
        with pytest.raises(ImageOcrExecutionError):
            recognize(adapter(tmp_path / "absent"))

    def test_a_nonzero_exit_is_an_execution_failure(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(ImageOcrExecutionError):
            recognize(adapter(engine))

    def test_a_timeout_is_an_execution_failure(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        with pytest.raises(ImageOcrExecutionError):
            recognize(adapter(engine, recognition_timeout_seconds=0.2))

    def test_a_timed_out_child_is_killed(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        with pytest.raises(ImageOcrExecutionError):
            recognize(adapter(engine, recognition_timeout_seconds=0.2))

        assert not fakes.completed(tmp_path)

    def test_undecodable_output_is_an_execution_failure(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout_bytes=b"\xff\xfe")

        with pytest.raises(ImageOcrExecutionError):
            recognize(adapter(engine))

    def test_a_nonzero_exit_is_never_a_limit_signal(self, tmp_path: Path) -> None:
        """Those end in a 201. An engine that failed must not produce one."""
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(ImageOcrExecutionError) as raised:
            recognize(adapter(engine))

        assert not isinstance(raised.value, ImageOcrLimitExceeded)

    def test_a_nonzero_exit_says_it_cannot_tell_the_cause(self, tmp_path: Path) -> None:
        """The message states the limitation rather than implying a diagnosis.

        Tesseract reports a broken engine and undecodable image data the same
        way, so the sentence claims neither.
        """
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(ImageOcrExecutionError) as raised:
            recognize(adapter(engine))

        assert "cannot tell" in str(raised.value)

    def test_no_failure_message_carries_the_engines_stderr(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(
            tmp_path, recognize_exit=1, stderr="cannot read /home/someone/holiday.png"
        )

        with pytest.raises(ImageOcrExecutionError) as raised:
            recognize(adapter(engine))

        assert "/home/someone" not in str(raised.value)

    def test_no_failure_mentions_a_page(self, tmp_path: Path) -> None:
        """There are none here, and borrowing the PDF wording would invent one."""
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(ImageOcrExecutionError) as raised:
            recognize(adapter(engine))

        assert "page" not in str(raised.value).lower()


class TestItLoadsNoNativeLibrary:
    """The dependency boundary, checked against the import statements themselves.

    Read from the module's AST rather than from its text, so a docstring that
    *mentions* PDFium cannot fail the test and an import hidden inside a function
    cannot pass it. Checked here as a unit; the other half of the proof is the CI
    job that runs real image OCR on a machine where the extra is not installed,
    because a passing import check on a machine that has the packages proves less
    than a passing recognition on a machine that does not.
    """

    @staticmethod
    def imported_modules(module: ModuleType) -> set[str]:
        """Every module name any import statement in this file names, at any depth."""
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names.add(node.module)
        return names

    def test_the_adapter_imports_no_rasterizer(self) -> None:
        import unimem_ocr.image

        assert not any(
            name.startswith("pypdfium2") for name in self.imported_modules(unimem_ocr.image)
        )

    def test_the_adapter_imports_no_imaging_library(self) -> None:
        import unimem_ocr.image

        assert not any(
            name == "PIL" or name.startswith("PIL.")
            for name in self.imported_modules(unimem_ocr.image)
        )

    def test_the_adapter_does_not_import_the_pdf_adapter(self) -> None:
        """That module loads PDFium at import time; this path must not reach it."""
        import unimem_ocr.image

        assert "unimem_ocr.tesseract" not in self.imported_modules(unimem_ocr.image)

    def test_the_shared_runner_imports_only_the_standard_library(self) -> None:
        """Which is what lets the image adapter use it without pulling in a renderer."""
        import unimem_ocr.engine

        imported = self.imported_modules(unimem_ocr.engine)

        assert imported <= {"subprocess", "dataclasses", "typing"}

    def test_importing_the_adapter_leaves_the_rasterizer_unloaded(self) -> None:
        """The runtime half, with the right marker chosen deliberately.

        ``pypdfium2`` is the marker and Pillow is **not**: ``pypdf``, an
        unconditional ``core`` dependency, imports Pillow whenever it happens to
        be installed, so Pillow's presence in ``sys.modules`` says nothing about
        this path either way. The renderer has no such excuse — if it is loaded
        after importing the image adapter, the image adapter loaded it.

        Run in a fresh interpreter so that another test having imported the extra
        cannot make this pass by accident. That image OCR also *works* with both
        packages absent is proven separately, by running it with them blocked.
        """
        probe = (
            "import sys; from unimem_ocr import build_tesseract_image_ocr; "
            "import unimem_ocr.image; "
            "print(sorted(n for n in sys.modules if n.split('.')[0] == 'pypdfium2'))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )

        assert completed.stdout.strip() == "[]"
