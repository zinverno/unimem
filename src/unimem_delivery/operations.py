"""The B1 receipt/claim/recovery mechanism shared by YouTube and audio. ADR-028."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from core.contracts import CaptureStatus
from core.persistence import (
    CaptureRecordNotFoundError,
    CaptureRecordStore,
    ContentObjectNotFoundError,
    ContentObjectStore,
)

State = Literal["queued", "running", "complete", "failed", "interrupted"]


class OperationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class ResultStores(Protocol):
    @property
    def record_store(self) -> CaptureRecordStore: ...
    @property
    def content_store(self) -> ContentObjectStore: ...


class Operation[RequestT: OperationRequest](BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: RequestT
    state: State
    accepted_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    reserved_capture_id: str
    capture_id: str | None = None
    content_id: str | None = None
    error_code: str | None = None


class DurableOperationStore[RequestT: OperationRequest]:
    """One table in the existing database; each call commits and closes.

    Registration and claiming use BEGIN IMMEDIATE, so duplicate delivery and
    capacity checks are atomic even across different connections/threads.
    """

    def __init__(
        self,
        database: Path,
        *,
        request_type: type[RequestT],
        operation_type: type[Operation[RequestT]],
        table: Literal["youtube_operations", "audio_operations", "image_operations"],
        capacity: int = 32,
        history_limit: int = 10_000,
    ) -> None:
        self.table, self.request_type = table, request_type
        self.operation_type = operation_type
        self.database = database
        self.capacity = capacity
        self.history_limit = history_limit
        with self._connection() as db:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} "
                "(id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table.removesuffix('s')}_states "
                f"ON {self.table}(state)"
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

    def _decode(self, row: tuple[str, str, str]) -> Operation[RequestT]:
        try:
            result = self.operation_type.model_validate_json(row[2])
            if result.request.operation_id != row[0] or result.state != row[1]:
                raise ValueError
            return result
        except Exception:
            raise OperationError("operation_corrupt") from None

    def register(self, request: RequestT) -> tuple[Operation[RequestT], bool]:
        request = self.request_type.model_validate_json(request.model_dump_json())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT id,state,payload FROM {self.table} WHERE id=?",
                (request.operation_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode(row)
                if existing.request != request:
                    raise OperationError("operation_conflict")
                return existing, False
            count = db.execute(
                f"SELECT count(*) FROM {self.table} WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if count >= self.capacity:
                raise OperationError("queue_full")
            if db.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0] >= self.history_limit:
                raise OperationError("operation_history_full")
            now = datetime.now(UTC)
            operation = self.operation_type(
                request=request,
                state="queued",
                accepted_at=now,
                updated_at=now,
                reserved_capture_id=str(uuid4()),
            )
            db.execute(
                f"INSERT INTO {self.table} VALUES (?,?,?)",
                (request.operation_id, operation.state, operation.model_dump_json()),
            )
        return operation, True

    def get(self, operation_id: str) -> Operation[RequestT]:
        with self._connection() as db:
            row = db.execute(
                f"SELECT id,state,payload FROM {self.table} WHERE id=?", (operation_id,)
            ).fetchone()
        if row is None:
            raise OperationError("operation_not_found")
        return self._decode(row)

    def claim(self) -> Operation[RequestT] | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Also protects against accidental multiple coordinators.
            if db.execute(f"SELECT 1 FROM {self.table} WHERE state='running' LIMIT 1").fetchone():
                return None
            row = db.execute(
                f"SELECT id,state,payload FROM {self.table} "
                "WHERE state='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            op = self._decode(row)
            now = datetime.now(UTC)
            running = self.operation_type(
                **(op.model_dump() | {"state": "running", "started_at": now, "updated_at": now})
            )
            db.execute(
                f"UPDATE {self.table} SET state=?,payload=? WHERE id=?",
                (running.state, running.model_dump_json(), row[0]),
            )
        return running

    def finish(
        self,
        op: Operation[RequestT],
        state: State,
        *,
        capture_id: str | None = None,
        content_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        result = self.operation_type(
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
                f"UPDATE {self.table} SET state=?,payload=? WHERE id=? AND state='running'",
                (state, result.model_dump_json(), op.request.operation_id),
            )

    def recover(self, service: ResultStores) -> None:
        """Called only with the process lease held and no executing child."""
        with self._connection() as db:
            rows = db.execute(
                f"SELECT id,state,payload FROM {self.table} WHERE state='running'"
            ).fetchall()
        for row in rows:
            self.reconcile(self._decode(row), service)

    def reconcile(
        self,
        op: Operation[RequestT],
        service: ResultStores,
        *,
        error_code: str = "execution_interrupted",
        incomplete_state: State = "interrupted",
    ) -> None:
        capture_id = content_id = None
        state: State = incomplete_state
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
