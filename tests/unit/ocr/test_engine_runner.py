"""The shared Tesseract invocation runner, on its own.

Exercised against a real fake engine on disk rather than a monkeypatched
``subprocess.run``, because what is under test *includes* ``subprocess``: that
the arguments are a list rather than a shell string, that stdout and stderr stay
separate, that a timed-out child is actually killed, and that a nonzero exit is
reported rather than swallowed. A patched ``run`` would prove none of that.

**This module needs no optional extra and no native library**, which is itself
part of the claim: the runner both adapters share is stdlib-only, so a machine
with no rasterizer installed can still exercise every line of it. The PDF-side
freeze these tests make possible lives in :mod:`tests.unit.ocr.test_pdf_freeze`,
which does need the extra.
"""

import subprocess
from pathlib import Path

import pytest

from tests.unit.ocr import fakes
from unimem_ocr.engine import (
    INVALID_UTF8,
    LAUNCH_ERROR,
    NONZERO_EXIT,
    TIMEOUT,
    EngineInvocation,
    EngineInvocationError,
    build_arguments,
    run_engine,
)
from unimem_ocr.policy import (
    RENDER_DPI,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
)

#: The exact vector the PDF adapter has always sent, minus argv[0]. Written out
#: rather than derived, so that a change to the policy constants shows up here as
#: a failure to explain rather than as a silently updated expectation.
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


def invocation(executable: Path | str, **overrides: object) -> EngineInvocation:
    fields: dict[str, object] = {
        "executable": str(executable),
        "languages": TESSERACT_LANGUAGE_ARGUMENT,
        "oem": TESSERACT_OEM,
        "psm": TESSERACT_PSM,
        "timeout": 30.0,
        "dpi": None,
    }
    return EngineInvocation(**(fields | overrides))  # type: ignore[arg-type]


class TestTheArgumentVector:
    def test_the_engine_reads_stdin_and_writes_stdout(self, tmp_path: Path) -> None:
        """Tesseract's own literals, so no path is named on either side."""
        arguments = build_arguments(invocation(tmp_path / "tesseract"))

        assert arguments[1:3] == ["stdin", "stdout"]

    def test_the_first_element_is_the_executable(self, tmp_path: Path) -> None:
        arguments = build_arguments(invocation(tmp_path / "tesseract"))

        assert arguments[0] == str(tmp_path / "tesseract")

    def test_no_dpi_argument_is_emitted_when_none_is_supplied(self, tmp_path: Path) -> None:
        """The direct-image case: nothing was rasterized, so nothing is claimed."""
        arguments = build_arguments(invocation(tmp_path / "tesseract", dpi=None))

        assert "--dpi" not in arguments

    def test_a_supplied_dpi_is_emitted_last(self, tmp_path: Path) -> None:
        arguments = build_arguments(invocation(tmp_path / "tesseract", dpi=300))

        assert arguments[-2:] == ["--dpi", "300"]

    def test_the_pdf_vector_is_exactly_what_it_has_always_been(self, tmp_path: Path) -> None:
        """The freeze, as a pure function: same elements, same order."""
        arguments = build_arguments(invocation(tmp_path / "tesseract", dpi=RENDER_DPI))

        assert arguments[1:] == FROZEN_PDF_ARGUMENTS

    def test_the_image_vector_is_the_pdf_vector_without_the_dpi_pair(self, tmp_path: Path) -> None:
        """The only difference between the two call sites, stated as a test."""
        arguments = build_arguments(invocation(tmp_path / "tesseract", dpi=None))

        assert arguments[1:] == FROZEN_PDF_ARGUMENTS[:-2]

    def test_every_element_is_a_string(self, tmp_path: Path) -> None:
        """A list of strings with ``shell=False`` is the whole injection story."""
        arguments = build_arguments(invocation(tmp_path / "tesseract", dpi=300))

        assert all(isinstance(element, str) for element in arguments)


class TestRunningTheEngine:
    def test_the_image_travels_on_stdin(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="read it\n")

        run_engine(b"\x89PNG\r\n\x1a\nbody", invocation(engine))

        assert fakes.recorded_stdin(tmp_path) == b"\x89PNG\r\n\x1a\nbody"

    def test_the_text_comes_back_from_stdout(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, stdout="HARBOUR\npier 4\n")

        assert run_engine(b"image", invocation(engine)) == "HARBOUR\npier 4\n"

    def test_the_output_is_not_trimmed(self, tmp_path: Path) -> None:
        """Deciding what counts as blank belongs to canonical policy, not here."""
        engine = fakes.write_fake_engine(tmp_path, stdout="  spaced  \n\n")

        assert run_engine(b"image", invocation(engine)) == "  spaced  \n\n"

    def test_stderr_never_reaches_the_returned_text(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(
            tmp_path, stdout="clean\n", stderr="Warning: from /home/someone/tessdata"
        )

        assert run_engine(b"image", invocation(engine)) == "clean\n"

    def test_the_recorded_argv_matches_the_built_vector(self, tmp_path: Path) -> None:
        """What the child actually received, not what the caller intended to send."""
        engine = fakes.write_fake_engine(tmp_path)

        run_engine(b"image", invocation(engine, dpi=300))

        assert fakes.recorded_argv(tmp_path) == FROZEN_PDF_ARGUMENTS

    def test_no_dpi_reaches_the_child_for_a_direct_image(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path)

        run_engine(b"image", invocation(engine, dpi=None))

        assert "--dpi" not in fakes.recorded_argv(tmp_path)

    def test_no_shell_interprets_the_arguments(self, tmp_path: Path) -> None:
        """The languages string reaches the child verbatim, metacharacters and all."""
        engine = fakes.write_fake_engine(tmp_path)

        run_engine(b"image", invocation(engine, languages="eng+rus; echo pwned"))

        assert "eng+rus; echo pwned" in fakes.recorded_argv(tmp_path)


class TestFailuresAreStructured:
    def test_a_missing_executable_is_a_launch_error(self, tmp_path: Path) -> None:
        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(tmp_path / "absent"))

        assert raised.value.reason == LAUNCH_ERROR

    def test_a_launch_error_keeps_the_os_explanation_for_a_log(self, tmp_path: Path) -> None:
        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(tmp_path / "absent"))

        assert raised.value.detail

    def test_a_launch_error_keeps_the_oserror_as_the_cause(self, tmp_path: Path) -> None:
        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(tmp_path / "absent"))

        assert isinstance(raised.value.__cause__, OSError)

    def test_a_nonzero_exit_is_reported_with_its_status(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=3)

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine))

        assert raised.value.reason == NONZERO_EXIT
        assert raised.value.returncode == 3

    def test_a_nonzero_exit_never_carries_the_childs_stderr(self, tmp_path: Path) -> None:
        """Another program's prose, and it can name paths on this machine."""
        engine = fakes.write_fake_engine(
            tmp_path, recognize_exit=1, stderr="failed reading /home/someone/secret.png"
        )

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine))

        assert "/home/someone" not in str(raised.value)

    def test_a_timeout_is_reported(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine, timeout=0.2))

        assert raised.value.reason == TIMEOUT

    def test_a_timed_out_child_is_killed_rather_than_abandoned(self, tmp_path: Path) -> None:
        """The marker file only appears if the sleep ran to completion."""
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        with pytest.raises(EngineInvocationError):
            run_engine(b"image", invocation(engine, timeout=0.2))

        assert not fakes.completed(tmp_path)

    def test_a_timeout_keeps_the_subprocess_error_as_the_cause(self, tmp_path: Path) -> None:
        engine = fakes.write_fake_engine(tmp_path, sleep=5)

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine, timeout=0.2))

        assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)

    def test_undecodable_output_is_reported(self, tmp_path: Path) -> None:
        """Strictly UTF-8: nothing is replaced, dropped, or guessed at."""
        engine = fakes.write_fake_engine(tmp_path, stdout_bytes=b"\xff\xfe not utf-8")

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine))

        assert raised.value.reason == INVALID_UTF8

    def test_a_returncode_is_absent_where_there_is_none(self, tmp_path: Path) -> None:
        """So a wrapper can tell "the child said 3" from "there was no child"."""
        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(tmp_path / "absent"))

        assert raised.value.returncode is None

    def test_the_runner_never_mentions_pages_or_images(self, tmp_path: Path) -> None:
        """Domain wording belongs to the wrappers, which is what lets both reuse it."""
        engine = fakes.write_fake_engine(tmp_path, recognize_exit=1)

        with pytest.raises(EngineInvocationError) as raised:
            run_engine(b"image", invocation(engine))

        assert "page" not in str(raised.value).lower()
        assert "image" not in str(raised.value).lower()
