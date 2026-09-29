"""Durable delivery requests, separate from capture lifecycle. ADR-025."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.contracts import CaptureStatus
from core.persistence import CaptureRecordNotFoundError, ContentObjectNotFoundError
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.service import YoutubeCaptureService
from unimem_youtube.source import source_url, validate_languages, video_id_from_url

State = Literal["queued", "running", "complete", "failed", "interrupted"]


class OperationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class YoutubeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    url: str = Field(max_length=4096)
    languages: tuple[str, ...] = ("en",)

    @field_validator("url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        return source_url(video_id_from_url(value))

    @field_validator("languages")
    @classmethod
    def language_priority(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validate_languages(value)
        return value


class YoutubeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: YoutubeRequest
    state: State
    accepted_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    reserved_capture_id: str
    capture_id: str | None = None
    content_id: str | None = None
    error_code: str | None = None


class OperationStore:
    """One table in the existing database; each call commits and closes.

    Registration and claiming use BEGIN IMMEDIATE, so duplicate delivery and
    capacity checks are atomic even across different connections/threads.
    """

    def __init__(self, database: Path, *, capacity: int = 32, history_limit: int = 10_000) -> None:
        self.database = database
        self.capacity = capacity
        self.history_limit = history_limit
        with self._connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS youtube_operations "
                "(id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS youtube_operation_states ON youtube_operations(state)"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.database, timeout=5)
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error:
            raise OperationError("operation_storage_unavailable") from None

    @staticmethod
    def _decode(row: tuple[str, str, str]) -> YoutubeOperation:
        try:
            result = YoutubeOperation.model_validate_json(row[2])
            if result.request.operation_id != row[0] or result.state != row[1]:
                raise ValueError
            return result
        except (ValueError, ValidationError, AcquisitionError):
            raise OperationError("operation_corrupt") from None

    def register(self, request: YoutubeRequest) -> tuple[YoutubeOperation, bool]:
        request = YoutubeRequest.model_validate_json(request.model_dump_json())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,state,payload FROM youtube_operations WHERE id=?",
                (request.operation_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode(row)
                if existing.request != request:
                    raise OperationError("operation_conflict")
                return existing, False
            count = db.execute(
                "SELECT count(*) FROM youtube_operations WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if count >= self.capacity:
                raise OperationError("queue_full")
            if (
                db.execute("SELECT count(*) FROM youtube_operations").fetchone()[0]
                >= self.history_limit
            ):
                raise OperationError("operation_history_full")
            now = datetime.now(UTC)
            operation = YoutubeOperation(
                request=request,
                state="queued",
                accepted_at=now,
                updated_at=now,
                reserved_capture_id=str(uuid4()),
            )
            db.execute(
                "INSERT INTO youtube_operations VALUES (?,?,?)",
                (request.operation_id, operation.state, operation.model_dump_json()),
            )
        return operation, True

    def get(self, operation_id: str) -> YoutubeOperation:
        with self._connection() as db:
            row = db.execute(
                "SELECT id,state,payload FROM youtube_operations WHERE id=?", (operation_id,)
            ).fetchone()
        if row is None:
            raise OperationError("operation_not_found")
        return self._decode(row)

    def claim(self) -> YoutubeOperation | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Also protects against accidental multiple coordinators.
            if db.execute(
                "SELECT 1 FROM youtube_operations WHERE state='running' LIMIT 1"
            ).fetchone():
                return None
            row = db.execute(
                "SELECT id,state,payload FROM youtube_operations "
                "WHERE state='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            op = self._decode(row)
            now = datetime.now(UTC)
            running = YoutubeOperation(
                **(op.model_dump() | {"state": "running", "started_at": now, "updated_at": now})
            )
            db.execute(
                "UPDATE youtube_operations SET state=?,payload=? WHERE id=?",
                (running.state, running.model_dump_json(), row[0]),
            )
        return running

    def finish(
        self,
        op: YoutubeOperation,
        state: State,
        *,
        capture_id: str | None = None,
        content_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        result = YoutubeOperation(
            **(
                op.model_dump()
                | {
                    "state": state,
                    "updated_at": now,
                    "finished_at": now,
                    "capture_id": capture_id,
                    "content_id": content_id,
                    "error_code": error_code,
                }
            )
        )
        with self._connection() as db:
            db.execute(
                "UPDATE youtube_operations SET state=?,payload=? WHERE id=? AND state='running'",
                (state, result.model_dump_json(), op.request.operation_id),
            )

    def recover(self, service: YoutubeCaptureService) -> None:
        """Called only with the process lease held and no executing child."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT id,state,payload FROM youtube_operations WHERE state='running'"
            ).fetchall()
        for row in rows:
            self.reconcile(self._decode(row), service)

    def reconcile(
        self,
        op: YoutubeOperation,
        service: YoutubeCaptureService,
        *,
        error_code: str = "execution_interrupted",
    ) -> None:
        capture_id = content_id = None
        state: State = "interrupted"
        try:
            capture = service.record_store.get(op.reserved_capture_id)
            capture_id = capture.id
            if capture.status is CaptureStatus.FAILED:
                self.finish(op, "failed", capture_id=capture.id, error_code=error_code)
                return
            content = service.content_store.get_for_capture(capture.id)
            content_id = content.id
            if capture.status is CaptureStatus.COMPLETE:
                state, error_code = "complete", ""
        except (CaptureRecordNotFoundError, ContentObjectNotFoundError):
            pass
        self.finish(
            op, state, capture_id=capture_id, content_id=content_id, error_code=error_code or None
        )
