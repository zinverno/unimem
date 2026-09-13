"""The command line, exercised without ever binding a TCP port.

``main`` takes its server as a parameter precisely so this is possible: the
argument handling and the composition are the parts worth testing, and starting
uvicorn is not.
"""

from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

import unimem_api.__main__ as cli
from unimem_api.__main__ import DEFAULT_HOST, DEFAULT_PORT, Options, main, parse_args


class RecordingServer:
    """Stands in for uvicorn: remembers the call and returns."""

    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, str, int]] = []

    def __call__(self, app: FastAPI, *, host: str, port: int) -> None:
        self.calls.append((app, host, port))


class TestArgumentParsing:
    def test_data_dir_is_required(self) -> None:
        with pytest.raises(SystemExit):
            parse_args([])

    def test_host_and_port_default(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path)])

        assert options == Options(
            data_dir=tmp_path, host=DEFAULT_HOST, port=DEFAULT_PORT, pdf_ocr=False
        )

    def test_the_default_bind_is_loopback(self) -> None:
        """Not a placeholder: this phase has no authentication of any kind."""
        assert DEFAULT_HOST == "127.0.0.1"

    def test_all_three_options_are_honoured(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path), "--host", "0.0.0.0", "--port", "9001"])

        assert options == Options(data_dir=tmp_path, host="0.0.0.0", port=9001, pdf_ocr=False)

    def test_the_data_dir_becomes_a_path(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path / "nested")])

        assert isinstance(options.data_dir, Path)

    def test_a_non_numeric_port_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--data-dir", str(tmp_path), "--port", "http"])

    def test_an_unknown_option_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--data-dir", str(tmp_path), "--reload"])

    def test_the_help_warns_against_exposing_the_server(self) -> None:
        help_text = cli.build_parser().format_help()

        assert "no authentication" in help_text


class TestMain:
    def test_it_builds_the_app_and_hands_it_to_the_server(self, tmp_path: Path) -> None:
        server = RecordingServer()

        exit_code = main(["--data-dir", str(tmp_path / "data")], server=server)

        assert exit_code == 0
        assert len(server.calls) == 1
        app, host, port = server.calls[0]
        assert isinstance(app, FastAPI)
        assert (host, port) == (DEFAULT_HOST, DEFAULT_PORT)

    def test_it_passes_the_requested_bind_through(self, tmp_path: Path) -> None:
        server = RecordingServer()

        main(
            ["--data-dir", str(tmp_path), "--host", "10.0.0.5", "--port", "9999"],
            server=server,
        )

        _, host, port = server.calls[0]
        assert (host, port) == ("10.0.0.5", 9999)

    def test_it_creates_a_data_directory_that_does_not_exist(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "deeply" / "nested" / "data"

        main(["--data-dir", str(data_dir)], server=RecordingServer())

        assert data_dir.is_dir()

    def test_the_app_it_builds_is_the_real_stack(self, tmp_path: Path) -> None:
        """Not a stub: the composition root ran, so the workspace is on disk."""
        data_dir = tmp_path / "data"
        server = RecordingServer()

        main(["--data-dir", str(data_dir)], server=server)

        assert (data_dir / "unimem.sqlite3").exists()


def test_serve_hands_the_app_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default server is a single ``uvicorn.run`` on the app object itself.

    Passing the app rather than an import string keeps the composition decision
    in :mod:`unimem_api.wiring` instead of handing it to uvicorn.
    """
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append((app, kwargs))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    app = FastAPI()

    cli.serve(app, host="127.0.0.1", port=8765)

    assert calls == [(app, {"host": "127.0.0.1", "port": 8765})]
