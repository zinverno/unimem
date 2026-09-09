"""The logical reference format and its validation."""

import hashlib

import pytest

from core.contracts import RawObjectRef
from core.storage import (
    RAW_REF_SCHEME,
    InvalidRawObjectRefError,
    build_raw_ref,
    parse_raw_ref,
    raw_object_ref,
    resolve_digest,
)

DIGEST = hashlib.sha256(b"unimem").hexdigest()


def test_reference_is_scheme_and_digest() -> None:
    assert build_raw_ref(DIGEST) == f"sha256:{DIGEST}"
    assert RAW_REF_SCHEME == "sha256"


def test_parse_returns_the_digest() -> None:
    assert parse_raw_ref(build_raw_ref(DIGEST)) == DIGEST


def test_store_reference_uses_the_digest_as_identity() -> None:
    reference = raw_object_ref(DIGEST, mime_type="image/png")
    assert reference.id == DIGEST
    assert reference.sha256 == DIGEST
    assert reference.ref == f"sha256:{DIGEST}"
    assert reference.mime_type == "image/png"


def test_mime_type_is_optional() -> None:
    assert raw_object_ref(DIGEST).mime_type is None


@pytest.mark.parametrize(
    "ref",
    [
        "sha1:" + "a" * 40,
        "md5:" + "a" * 32,
        "file:///etc/passwd",
        "https://example.com/object",
        "sha256",
        "plain-string",
    ],
)
def test_unsupported_scheme_is_rejected(ref: str) -> None:
    with pytest.raises(InvalidRawObjectRefError):
        parse_raw_ref(ref)


@pytest.mark.parametrize(
    "digest",
    [
        "not-a-digest",
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        " " + "a" * 63,
    ],
)
def test_malformed_digest_is_rejected(digest: str) -> None:
    with pytest.raises(InvalidRawObjectRefError, match="digest"):
        parse_raw_ref(f"sha256:{digest}")


@pytest.mark.parametrize(
    "ref",
    [
        "sha256:../../etc/passwd",
        "sha256:/etc/passwd",
        "sha256:" + "a" * 62 + "/..",
        "sha256:.." + "a" * 62,
    ],
)
def test_path_traversal_references_are_rejected(ref: str) -> None:
    with pytest.raises(InvalidRawObjectRefError):
        parse_raw_ref(ref)


def test_resolve_digest_accepts_a_consistent_reference() -> None:
    assert resolve_digest(raw_object_ref(DIGEST)) == DIGEST


def test_resolve_digest_accepts_a_reference_without_a_sha256_field() -> None:
    reference = RawObjectRef(id="raw_01", ref=build_raw_ref(DIGEST))
    assert resolve_digest(reference) == DIGEST


def test_disagreeing_ref_and_sha256_are_rejected() -> None:
    other = hashlib.sha256(b"something else").hexdigest()
    reference = RawObjectRef(id="raw_01", ref=build_raw_ref(DIGEST), sha256=other)
    with pytest.raises(InvalidRawObjectRefError, match="but sha256"):
        resolve_digest(reference)


def test_reference_without_a_ref_is_rejected() -> None:
    with pytest.raises(InvalidRawObjectRefError, match="has no ref"):
        resolve_digest(RawObjectRef(id="raw_01", sha256=DIGEST))
