"""The startup gate: what must be present before the server accepts a request.

These tests need no rasterizer and no real engine. They run a *fake* Tesseract —
an executable shell script — so that "the engine is missing", "the engine is
broken", and "the engine has English but not Russian" are all reproducible on any
machine, including one with a perfectly good Tesseract installed.

What is being protected is a single rule: a deployment that asked for local OCR
either gets exactly the configured capability or does not start. There is no
partial start, no silent narrowing of the language set, and no attempt to fetch
what is missing.
"""

import sys
from pathlib import Path

import pytest

from tests.unit.ocr.fakes import write_fake_engine
from unimem_ocr.errors import OcrPrerequisiteError
from unimem_ocr.policy import RECOGNITION_LANGUAGES
from unimem_ocr.prerequisites import (
    OPTIONAL_EXTRA,
    PROBE_TIMEOUT_SECONDS,
    available_languages,
    engine_version,
    require_engine,
    require_rasterizer,
)


class TestTheVersionProbe:
    def test_it_reports_what_the_engine_says(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, version="5.3.4")

        assert engine_version(str(engine)) == "5.3.4"

    def test_it_is_read_from_the_engine_and_not_assumed(self, tmp_path: Path) -> None:
        """A version recorded on durable content must come from the software itself."""
        engine = write_fake_engine(tmp_path, version="4.1.1-rc2")

        assert engine_version(str(engine)) == "4.1.1-rc2"

    def test_a_missing_executable_is_a_prerequisite_error(self, tmp_path: Path) -> None:
        with pytest.raises(OcrPrerequisiteError, match="could not be run"):
            engine_version(str(tmp_path / "not-installed"))

    def test_the_message_says_tesseract_is_not_installed_by_this_build(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(OcrPrerequisiteError, match="does not install it"):
            engine_version(str(tmp_path / "not-installed"))

    def test_a_nonzero_exit_is_a_prerequisite_error(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, version_exit=3)

        with pytest.raises(OcrPrerequisiteError, match="exited with status 3"):
            engine_version(str(engine))

    def test_an_unrecognizable_banner_is_a_prerequisite_error(self, tmp_path: Path) -> None:
        """A version this build cannot read is not a version it may record."""
        engine = write_fake_engine(tmp_path, version=None)

        with pytest.raises(OcrPrerequisiteError, match="did not report a version"):
            engine_version(str(engine))

    def test_the_probe_is_bounded(self) -> None:
        """A wedged executable must not hang startup forever."""
        assert PROBE_TIMEOUT_SECONDS > 0

    def test_a_hanging_executable_is_a_prerequisite_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("unimem_ocr.prerequisites.PROBE_TIMEOUT_SECONDS", 0.1)
        hanging = tmp_path / "tesseract"
        hanging.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
        hanging.chmod(0o755)

        with pytest.raises(OcrPrerequisiteError, match="did not answer"):
            engine_version(str(hanging))


class TestTheLanguageProbe:
    def test_it_lists_the_codes_and_drops_the_header(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, languages=("eng", "osd", "rus"))

        assert available_languages(str(engine)) == frozenset({"eng", "osd", "rus"})

    def test_an_engine_with_no_languages_at_all(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, languages=())

        assert available_languages(str(engine)) == frozenset()

    def test_a_nonzero_exit_is_a_prerequisite_error(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, list_exit=1)

        with pytest.raises(OcrPrerequisiteError, match="exited with status 1"):
            available_languages(str(engine))


class TestBothLanguagesAreRequired:
    def test_an_engine_with_both_is_accepted(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, languages=("eng", "rus", "deu"))

        assert require_engine(str(engine)) == "5.3.4"

    @pytest.mark.parametrize(
        ("languages", "missing"),
        [
            (("eng", "osd"), "rus"),
            (("rus", "osd"), "eng"),
            (("deu", "fra"), "eng, rus"),
            ((), "eng, rus"),
        ],
        ids=["no-rus", "no-eng", "neither", "none"],
    )
    def test_a_missing_language_fails_startup(
        self, tmp_path: Path, languages: tuple[str, ...], missing: str
    ) -> None:
        engine = write_fake_engine(tmp_path, languages=languages)

        with pytest.raises(OcrPrerequisiteError) as raised:
            require_engine(str(engine))

        assert f"missing language data for {missing}" in str(raised.value)

    def test_english_alone_is_not_a_fallback(self, tmp_path: Path) -> None:
        """The negative assertion: it fails rather than recognizing English only."""
        engine = write_fake_engine(tmp_path, languages=("eng",))

        with pytest.raises(OcrPrerequisiteError, match="nor falls back"):
            require_engine(str(engine))

    def test_the_refusal_names_the_configured_language_set(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, languages=("eng",))

        with pytest.raises(OcrPrerequisiteError) as raised:
            require_engine(str(engine))

        assert "+".join(RECOGNITION_LANGUAGES) in str(raised.value)

    def test_the_refusal_does_not_offer_to_download_anything(self, tmp_path: Path) -> None:
        engine = write_fake_engine(tmp_path, languages=("eng",))

        with pytest.raises(OcrPrerequisiteError) as raised:
            require_engine(str(engine))

        assert "neither downloads language data" in str(raised.value)

    def test_the_probes_are_the_only_thing_the_gate_runs(self, tmp_path: Path) -> None:
        """Startup validation must not recognize anything: no image is ever sent."""
        engine = write_fake_engine(tmp_path)

        require_engine(str(engine))

        assert not (tmp_path / "argv.txt").exists()
        assert not (tmp_path / "stdin.bin").exists()


class TestTheOptionalPackagesAreCheckedFirst:
    def test_a_missing_rasterizer_is_a_prerequisite_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulated by making the import genuinely fail, not by faking the check.

        ``None`` in ``sys.modules`` is how Python spells "this module is not
        importable" from inside a running interpreter, so the adapter module is
        re-imported for real and fails at its own first line — which is the
        failure an ordinary installation without the extra would produce.
        """
        monkeypatch.setitem(sys.modules, "pypdfium2", None)
        monkeypatch.delitem(sys.modules, "unimem_ocr.tesseract", raising=False)

        with pytest.raises(OcrPrerequisiteError) as raised:
            require_rasterizer()

        assert "pypdfium2" in str(raised.value)

    def test_the_message_says_which_extra_to_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "pypdfium2", None)
        monkeypatch.delitem(sys.modules, "unimem_ocr.tesseract", raising=False)

        with pytest.raises(OcrPrerequisiteError) as raised:
            require_rasterizer()

        assert f"pip install '{OPTIONAL_EXTRA}'" in str(raised.value)

    def test_it_offers_no_way_to_continue_without_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No degraded mode: the only outcome is an error."""
        monkeypatch.setitem(sys.modules, "pypdfium2", None)
        monkeypatch.delitem(sys.modules, "unimem_ocr.tesseract", raising=False)

        with pytest.raises(OcrPrerequisiteError):
            require_rasterizer()


def test_the_extra_is_named_the_way_it_is_installed() -> None:
    """The message has to be something a reader can paste into a shell."""
    assert OPTIONAL_EXTRA == "capture-core[ocr]"
