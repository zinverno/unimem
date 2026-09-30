"""Durable snapshots, destination-scoped credentials and fenced import receipts."""

import hashlib
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from unimem_api.obsidian_contract import (
    MAX_MARKDOWN_BYTES,
    AckRequest,
    ClaimRequest,
    Delivery,
    DeliveryError,
    Destination,
    FailureRequest,
)


def suggested_filename(capture: str) -> str:
    return f"unimem-{hashlib.sha256(capture.encode()).hexdigest()}.md"


class ObsidianStore:
    def __init__(self, database: Path, *, lease_seconds: int = 120, limit: int = 1000) -> None:
        self.database, self.lease_seconds, self.limit = database, lease_seconds, limit
        try:
            fd = os.open(database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise OSError
        except OSError:
            raise DeliveryError("delivery_storage_unavailable") from None
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS destinations "
                "(id TEXT PRIMARY KEY, name TEXT NOT NULL, credential_hash TEXT UNIQUE NOT NULL, "
                "receiver_id TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS obsidian_deliveries "
                "(id TEXT PRIMARY KEY, destination TEXT NOT NULL, capture TEXT NOT NULL, "
                "payload TEXT NOT NULL, claim_id TEXT, UNIQUE(destination,capture))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS obsidian_delivery_queue ON obsidian_deliveries "
                "(destination, json_extract(payload, '$.state'), "
                "json_extract(payload, '$.lease_expires_at'))"
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.database, timeout=5)
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error:
            raise DeliveryError("delivery_storage_unavailable") from None

    def create_destination(self, name: str) -> tuple[Destination, str]:
        destination = Destination(destination_id=str(uuid4()), display_name=name.strip())
        if any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("Invalid destination name.")
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute(
                "INSERT INTO destinations VALUES (?,?,?,NULL)",
                (
                    destination.destination_id,
                    destination.display_name,
                    hashlib.sha256(token.encode()).hexdigest(),
                ),
            )
        return destination, token

    def authenticate(self, token: str) -> str | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT id FROM destinations WHERE credential_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        return str(row[0]) if row else None

    def destinations(self) -> list[Destination]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT id,name,receiver_id FROM destinations ORDER BY rowid"
            ).fetchall()
        return [Destination(destination_id=r[0], display_name=r[1], receiver_id=r[2]) for r in rows]

    def destination(self, destination_id: str) -> Destination:
        for destination in self.destinations():
            if destination.destination_id == destination_id:
                return destination
        raise DeliveryError("destination_not_found")

    def find(self, destination: str, capture: str) -> Delivery | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM obsidian_deliveries WHERE destination=? AND capture=?",
                (destination, capture),
            ).fetchone()
        return Delivery.model_validate_json(row[0]) if row else None

    def register(
        self,
        destination: str,
        capture: str,
        content: str,
        markdown: str,
        export_format: str,
        export_version: str,
    ) -> tuple[Delivery, bool]:
        self.destination(destination)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM obsidian_deliveries WHERE destination=? AND capture=?",
                (destination, capture),
            ).fetchone()
            if row:
                return Delivery.model_validate_json(row[0]), False
            if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
                raise DeliveryError("markdown_too_large")
            if db.execute("SELECT count(*) FROM obsidian_deliveries").fetchone()[0] >= self.limit:
                raise DeliveryError("delivery_history_full")
            delivery_id = str(uuid4())
            delivery = Delivery(
                delivery_id=delivery_id,
                destination_id=destination,
                source_capture_id=capture,
                source_content_id=content,
                export_format=export_format,
                export_version=export_version,
                markdown=markdown,
                markdown_sha256=hashlib.sha256(markdown.encode()).hexdigest(),
                suggested_filename=suggested_filename(capture),
                created_at=datetime.now(UTC),
            )
            db.execute(
                "INSERT INTO obsidian_deliveries VALUES (?,?,?,?,NULL)",
                (delivery_id, destination, capture, delivery.model_dump_json()),
            )
        return delivery, True

    @staticmethod
    def _read(
        db: sqlite3.Connection, destination: str, delivery_id: str
    ) -> tuple[Delivery, str | None]:
        row = db.execute(
            "SELECT payload,claim_id FROM obsidian_deliveries WHERE id=? AND destination=?",
            (delivery_id, destination),
        ).fetchone()
        if not row:
            raise DeliveryError("delivery_not_found")
        return Delivery.model_validate_json(row[0]), row[1]

    def get(self, destination: str, delivery_id: str) -> Delivery:
        with self.connection() as db:
            return self._read(db, destination, delivery_id)[0]

    def next(self, destination: str) -> Delivery | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM obsidian_deliveries WHERE destination=? "
                "AND json_extract(payload, '$.state') IN ('pending','claimed') "
                "AND (json_extract(payload, '$.lease_expires_at') IS NULL "
                "OR json_extract(payload, '$.lease_expires_at') <= ?) ORDER BY rowid LIMIT 1",
                (destination, datetime.now(UTC).isoformat().replace("+00:00", "Z")),
            ).fetchone()
        return Delivery.model_validate_json(row[0]) if row else None

    @staticmethod
    def _owner(db: sqlite3.Connection, destination: str, receiver_id: str) -> None:
        row = db.execute(
            "SELECT receiver_id FROM destinations WHERE id=?", (destination,)
        ).fetchone()
        if row is None:
            raise DeliveryError("destination_not_found")
        if row[0] is not None and row[0] != receiver_id:
            raise DeliveryError("receiver_mismatch")
        db.execute("UPDATE destinations SET receiver_id=? WHERE id=?", (receiver_id, destination))

    def claim(self, destination: str, delivery_id: str, request: ClaimRequest) -> Delivery:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            delivery, claim_id = self._read(db, destination, delivery_id)
            self._owner(db, destination, request.receiver_id)
            now = datetime.now(UTC)
            if delivery.state not in {"pending", "claimed"}:
                raise DeliveryError("delivery_terminal")
            if delivery.lease_expires_at and delivery.lease_expires_at > now:
                if claim_id == request.claim_id:
                    return delivery
                raise DeliveryError("lease_active")
            updated = delivery.model_copy(
                update={
                    "state": "claimed",
                    "lease_expires_at": now + timedelta(seconds=self.lease_seconds),
                }
            )
            db.execute(
                "UPDATE obsidian_deliveries SET payload=?,claim_id=? WHERE id=?",
                (updated.model_dump_json(), request.claim_id, delivery_id),
            )
        return updated

    def finish(
        self, destination: str, delivery_id: str, request: AckRequest | FailureRequest
    ) -> Delivery:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            delivery, claim_id = self._read(db, destination, delivery_id)
            self._owner(db, destination, request.receiver_id)
            if claim_id != request.claim_id:
                raise DeliveryError("claim_mismatch")
            if isinstance(request, AckRequest):
                if request.markdown_sha256 != delivery.markdown_sha256:
                    raise DeliveryError("digest_mismatch")
                state, error = "imported", None
            else:
                error = request.error_code
                state = {"file_exists": "conflict", "write_ambiguous": "ambiguous"}.get(
                    error, "failed"
                )
            if delivery.state == state and delivery.error_code == error:
                return delivery  # Lost ACK response, even after the original lease expires.
            if delivery.state != "claimed":
                raise DeliveryError("delivery_terminal")
            if delivery.lease_expires_at is None or delivery.lease_expires_at <= datetime.now(UTC):
                raise DeliveryError("lease_expired")
            updated = delivery.model_copy(
                update={"state": state, "error_code": error, "lease_expires_at": None}
            )
            db.execute(
                "UPDATE obsidian_deliveries SET payload=? WHERE id=?",
                (updated.model_dump_json(), delivery_id),
            )
        return updated
