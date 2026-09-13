"""``create_app`` — explicit dependencies, no globals, and no reach into core.

The factory is what makes the surface testable and the boundary checkable. These
tests assert both: that an app is entirely a function of what it was handed, and
that handing it things did not teach ``core`` about HTTP.
"""

import ast
import importlib
import pkgutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core
import unimem_api
import unimem_ocr
from core.intake import CaptureIntake
from core.processing import ProcessingOrchestrator, ProcessorRouter, TextProcessor
from core.rendering import JsonRenderer, MarkdownRenderer
from tests.unit.api.builders import CAPTURE_ID, text_envelope
from tests.unit.api.conftest import Stack, build_stack, served_paths
from tests.unit.api.doubles import (
    FakeCaptureRecordStore,
    FakeContentObjectStore,
    FakeRawObjectStore,
)
from unimem_api import create_app


def test_the_factory_accepts_doubles_for_every_dependency() -> None:
    """Nothing is constructed inside the factory; everything arrives as a parameter."""
    raw_store = FakeRawObjectStore()
    record_store = FakeCaptureRecordStore()
    content_store = FakeContentObjectStore()

    app = create_app(
        intake=CaptureIntake(raw_store, record_store),
        orchestrator=ProcessingOrchestrator(
            ProcessorRouter([TextProcessor(raw_store)]), record_store, content_store
        ),
        record_store=record_store,
        content_store=content_store,
        raw_store=raw_store,
    )

    assert isinstance(app, FastAPI)
    with TestClient(app) as client:
        assert client.post("/v1/captures", json=text_envelope()).status_code == 201


def test_two_apps_share_no_state() -> None:
    """No module-level app, registry, or singleton: the doubles are the only state."""
    first = build_stack()
    second = build_stack()

    assert first.client.post("/v1/captures", json=text_envelope()).status_code == 201

    assert second.client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404
    assert second.client.post("/v1/captures", json=text_envelope()).status_code == 201


def test_an_app_uses_the_stores_it_was_given() -> None:
    """A record written straight into the double is served by the app."""
    record_store = FakeCaptureRecordStore()
    stack = build_stack(record_store=record_store)
    stack.client.post("/v1/captures", json=text_envelope())

    assert record_store.get_or_none(CAPTURE_ID) is not None


def test_the_documented_routes_are_the_only_routes(stack: Stack) -> None:
    """No list, search, batch, delete, or reprocess endpoint slipped in."""
    paths = {path for path in served_paths(stack.app) if path.startswith(("/health", "/v1"))}

    assert paths == {
        "/health",
        "/v1/uploads",
        "/v1/captures",
        "/v1/captures/{capture_id}",
        "/v1/captures/{capture_id}/content",
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/captures"),
        ("delete", f"/v1/captures/{CAPTURE_ID}"),
        ("put", f"/v1/captures/{CAPTURE_ID}"),
        ("post", f"/v1/captures/{CAPTURE_ID}/content"),
        ("get", "/v1/captures/search"),
        ("get", "/v1/uploads"),
        ("delete", "/v1/uploads"),
    ],
)
def test_undocumented_operations_are_not_served(stack: Stack, method: str, path: str) -> None:
    stack.client.post("/v1/captures", json=text_envelope())

    response = getattr(stack.client, method)(path)

    assert response.status_code in {404, 405}


def imports_of(path: Path) -> set[str]:
    """Every module name one source file imports, read from its AST.

    The AST rather than the source text, because a docstring that *names* a
    module is not a dependency on it — and several docstrings in this project name
    the tools they deliberately do not use.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def imported_modules(package: ModuleType) -> dict[str, set[str]]:
    """What every module of an importable ``package`` imports."""
    return {
        module.name: imports_of(Path(importlib.import_module(module.name).__file__ or ""))
        for module in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}.")
    }


def source_imports(package: ModuleType) -> dict[str, set[str]]:
    """The same, for a package whose modules must **not** be imported to be read.

    :mod:`unimem_ocr` has one module that imports native wheels from an optional
    extra, so importing it to find out what it imports would make this scan
    impossible in precisely the environment the scan exists to describe — an
    ordinary installation without the extra. The files are read from disk instead.
    """
    root = Path(package.__path__[0])
    return {
        f"{package.__name__}{'' if path.stem == '__init__' else '.' + path.stem}": imports_of(path)
        for path in sorted(root.glob("*.py"))
    }


def top_level(names: set[str]) -> set[str]:
    return {name.split(".")[0] for name in names}


class TestCoreKnowsNothingAboutHttp:
    """The dependency points one way, and this is what checks it."""

    def test_no_core_module_imports_the_api_package_or_a_web_framework(self) -> None:
        forbidden = {"unimem_api", "fastapi", "starlette", "uvicorn", "httpx", "httpx2"}

        offenders = {
            name: sorted(top_level(names) & forbidden)
            for name, names in imported_modules(core).items()
            if top_level(names) & forbidden
        }

        assert offenders == {}

    def test_core_depends_only_on_its_allowlisted_parsers_and_the_standard_library(
        self,
    ) -> None:
        """The kernel's third-party surface is an exact allowlist, not a trend.

        ``pydantic`` is what the contracts are written in. The rest are document
        parsers, each confined to the one processor whose format it reads — the
        tests below check that — and each admitted for the same reason: reading
        PDF or OOXML structure is not something the standard library does, and
        the alternative to a focused parser was a heavyweight rendering stack or
        a subprocess. ``pypdf`` joined in Phase 3 PR 1; ``docx`` (python-docx)
        and the ``lxml`` it parses XML with joined in Phase 3 PR 2. None of them
        is a precedent for the delivery surface, which is what the preceding
        test guards.

        The assertion stays an exact set on purpose. A new dependency has to be
        added here deliberately, in a diff someone reviews, rather than
        arriving as a transitive habit.
        """
        third_party = {
            name
            for names in imported_modules(core).values()
            for name in top_level(names)
            if name not in set(sys.stdlib_module_names) | {"core", "pydantic"}
        }

        assert third_party == {"pypdf", "docx", "lxml"}

    def test_the_pdf_parser_reaches_no_further_than_the_pdf_processor(self) -> None:
        """One module imports ``pypdf``, and it is the one whose job is PDFs.

        Contracts, storage, persistence, intake, rendering, the router, and the
        orchestrator all stay unaware that the format exists — which is what
        makes ``pypdf`` a processor's tool rather than the kernel's.
        """
        importers = {
            name for name, names in imported_modules(core).items() if "pypdf" in top_level(names)
        }

        assert importers == {"core.processing.pdf"}

    @pytest.mark.parametrize("parser", ["docx", "lxml"], ids=["python-docx", "lxml"])
    def test_the_docx_parser_reaches_no_further_than_the_docx_processor(self, parser: str) -> None:
        """Same rule, one format later, and it is the rule that keeps them apart.

        Intake in particular does not import this: it decides which *declared*
        formats it accepts, which is a fact about the build rather than about
        any parser, and it never looks inside a document to check.
        """
        importers = {
            name for name, names in imported_modules(core).items() if parser in top_level(names)
        }

        assert importers == {"core.processing.docx"}

    def test_no_converter_or_renderer_is_imported_anywhere_in_core(self) -> None:
        """The tools that would invent page numbers, spelled out so they stay out.

        Since Phase 3 PR 3 the list also names the OCR machinery. ``core`` owns
        the page *policy* — embedded text first, recognition only for what is
        left — and reaches the engine through the
        :class:`~core.processing.ocr.PdfPageOcr` port. A rasterizer, an imaging
        library, a subprocess, or the concrete adapter package appearing anywhere
        in here would mean the kernel had acquired system prerequisites and that
        the policy could no longer be tested on a machine without them.
        """
        forbidden = {
            "mammoth",
            "docx2txt",
            "pypandoc",
            "pandoc",
            "subprocess",
            "PIL",
            "pytesseract",
            "pypdfium2",
            "pypdfium2_raw",
            "tesserocr",
            "unimem_ocr",
        }

        offenders = {
            name: sorted(top_level(names) & forbidden)
            for name, names in imported_modules(core).items()
            if top_level(names) & forbidden
        }

        assert offenders == {}


class TestTheOptionalOcrAdapterStaysOptional:
    """The one optional package, and the single module allowed to know its name."""

    def test_only_the_command_line_module_imports_the_adapter_package(self) -> None:
        """Not the composition root, and not the app factory.

        :mod:`unimem_api.wiring` takes the recognizer as a *port* and never
        constructs one, which is what lets a default deployment import the whole
        application with neither ``pypdfium2`` nor ``Pillow`` installed.
        :mod:`unimem_api.__main__` is the delivery layer that already knows about
        argv and exit codes, so it is where the one conditional import lives.
        """
        importers = {
            name
            for name, names in imported_modules(unimem_api).items()
            if "unimem_ocr" in top_level(names)
        }

        assert importers == {"unimem_api.__main__"}

    @pytest.mark.parametrize("package", ["pypdfium2", "PIL"], ids=["pypdfium2", "pillow"])
    def test_no_api_module_imports_a_native_ocr_dependency(self, package: str) -> None:
        offenders = {
            name
            for name, names in imported_modules(unimem_api).items()
            if package in top_level(names)
        }

        assert offenders == set()

    def test_the_native_dependencies_appear_in_two_modules_and_no_others(self) -> None:
        """Where ``pypdfium2`` and ``Pillow`` are allowed to be named at all.

        :mod:`unimem_ocr.tesseract` is the renderer and imports both at module
        level; :mod:`unimem_ocr.prerequisites` names ``pypdfium2.version`` inside
        one function, for the line an operator reads, and the AST cannot tell a
        function-local import from a module-level one. That *importing*
        ``unimem_ocr`` loads neither package is the guarantee that actually
        matters, and it is checked against a real interpreter in
        ``tests/unit/api/test_pdf_ocr_composition.py`` rather than by reading
        source here.

        The point of this assertion is the other direction: the policy module, the
        error, and the package root must never grow a native import, because each
        of them is loaded on paths a default deployment takes.
        """
        native = {
            name
            for name, names in source_imports(unimem_ocr).items()
            if top_level(names) & {"pypdfium2", "PIL"}
        }

        assert native == {"unimem_ocr.tesseract", "unimem_ocr.prerequisites"}

    def test_the_adapter_reaches_no_network(self) -> None:
        """Local only: no cloud OCR service, no model download, no telemetry.

        Named rather than inferred, because "it does not call out" is the kind of
        claim that quietly stops being true when someone adds a fallback.
        """
        forbidden = {
            "http",
            "httpx",
            "httpx2",
            "urllib",
            "socket",
            "ssl",
            "requests",
            "aiohttp",
            "boto3",
            "google",
            "openai",
            "anthropic",
        }

        offenders = {
            name: sorted(top_level(names) & forbidden)
            for name, names in source_imports(unimem_ocr).items()
            if top_level(names) & forbidden
        }

        assert offenders == {}

    def test_the_adapter_never_imports_the_delivery_surface(self) -> None:
        """It depends on ``core`` and on nothing above it."""
        forbidden = {"unimem_api", "fastapi", "starlette", "uvicorn"}

        offenders = {
            name: sorted(top_level(names) & forbidden)
            for name, names in source_imports(unimem_ocr).items()
            if top_level(names) & forbidden
        }

        assert offenders == {}


class TestTheApiDoesNotRender:
    """``core.rendering`` is a derived-representation layer, and this API is not it."""

    def test_no_api_module_imports_a_renderer(self) -> None:
        offenders = {
            name: sorted(imported for imported in names if "rendering" in imported)
            for name, names in imported_modules(unimem_api).items()
            if any("rendering" in imported for imported in names)
        }

        assert offenders == {}

    def test_no_renderer_runs_during_the_whole_request_cycle(
        self, stack: Stack, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Booby-trap both renderers, then drive every endpoint."""

        def explode(self: object, content: object) -> str:
            raise AssertionError("the API called a renderer")

        monkeypatch.setattr(JsonRenderer, "render", explode)
        monkeypatch.setattr(MarkdownRenderer, "render", explode)

        assert stack.client.get("/health").status_code == 200
        assert stack.client.post("/v1/captures", json=text_envelope()).status_code == 201
        assert stack.client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 200
        assert stack.client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 200


def test_the_openapi_schema_describes_the_canonical_contracts(stack: Stack) -> None:
    """The POST body is ``CaptureEnvelope``, not an HTTP-specific twin of it."""
    schema = stack.client.get("/openapi.json").json()
    request_body = schema["paths"]["/v1/captures"]["post"]["requestBody"]

    reference = request_body["content"]["application/json"]["schema"]["$ref"]

    assert reference.endswith("/CaptureEnvelope")
    assert "CaptureEnvelopeRequest" not in schema["components"]["schemas"]
