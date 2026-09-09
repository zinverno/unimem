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
    ],
)
def test_undocumented_operations_are_not_served(stack: Stack, method: str, path: str) -> None:
    stack.client.post("/v1/captures", json=text_envelope())

    response = getattr(stack.client, method)(path)

    assert response.status_code in {404, 405}


def imported_modules(package: ModuleType) -> dict[str, set[str]]:
    """Every module name each module of ``package`` imports, read from its AST.

    The AST rather than the source text, because a docstring that *names* a
    module is not a dependency on it — and this package's docstrings name the
    renderers precisely to say that it does not use them.
    """
    imports: dict[str, set[str]] = {}
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}."):
        path = Path(importlib.import_module(module.name).__file__ or "")
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
        imports[module.name] = names
    return imports


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

    def test_core_still_depends_only_on_pydantic_and_the_standard_library(self) -> None:
        """Adding a delivery surface added no runtime dependency to the kernel."""
        third_party = {
            name
            for names in imported_modules(core).values()
            for name in top_level(names)
            if name not in set(sys.stdlib_module_names) | {"core", "pydantic"}
        }

        assert third_party == set()


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
