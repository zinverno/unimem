"""Behaviour of the lossless JSON projection."""

import json
from typing import Any

import pytest

from core.contracts import SCHEMA_VERSION, ContentObject
from core.rendering import JsonRenderer
from tests.unit.rendering.builders import (
    ORIGINAL_SHA256,
    UNICODE_TEXT,
    make_content_object,
    make_segment,
    make_textless_segment,
)

#: Every top-level field of the contract, so a new one cannot be quietly
#: dropped from the projection without this list being updated too.
MAJOR_FIELDS = (
    "schema_version",
    "id",
    "type",
    "source",
    "original",
    "title",
    "metadata",
    "segments",
    "assets",
    "derived",
    "processing",
)


def parse(rendered: str) -> dict[str, Any]:
    """The rendered document, parsed back as a plain mapping."""
    parsed: Any = json.loads(rendered)
    assert isinstance(parsed, dict)
    return parsed


# --- identity --------------------------------------------------------------


def test_renderer_identity(json_renderer: JsonRenderer) -> None:
    assert (json_renderer.name, json_renderer.version, json_renderer.media_type) == (
        "json",
        "0.1",
        "application/json",
    )


# --- the output is JSON ----------------------------------------------------


def test_output_parses_as_json(json_renderer: JsonRenderer, content: ContentObject) -> None:
    assert parse(json_renderer.render(content))["id"] == content.id


def test_output_is_valid_utf8(json_renderer: JsonRenderer, content: ContentObject) -> None:
    """The string encodes to UTF-8 and decodes back unchanged."""
    rendered = json_renderer.render(content)

    assert rendered.encode("utf-8").decode("utf-8") == rendered


def test_output_is_compact(json_renderer: JsonRenderer, content: ContentObject) -> None:
    """One line, no indentation, no whitespace after the separators."""
    rendered = json_renderer.render(content)

    assert "\n" not in rendered
    assert rendered == json.dumps(
        json.loads(rendered), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


# --- fidelity --------------------------------------------------------------


def test_round_trips_to_an_equal_content_object(
    json_renderer: JsonRenderer, content: ContentObject
) -> None:
    restored = ContentObject.model_validate_json(json_renderer.render(content))

    assert restored == content


def test_round_trip_output_is_stable(json_renderer: JsonRenderer, content: ContentObject) -> None:
    """Rendering the restored object reproduces the same document."""
    rendered = json_renderer.render(content)
    restored = ContentObject.model_validate_json(rendered)

    assert json_renderer.render(restored) == rendered


@pytest.mark.parametrize("field", MAJOR_FIELDS)
def test_every_major_field_is_present(
    json_renderer: JsonRenderer, content: ContentObject, field: str
) -> None:
    assert field in parse(json_renderer.render(content))


def test_top_level_keys_are_exactly_the_contract_fields(
    json_renderer: JsonRenderer, content: ContentObject
) -> None:
    """No wrapper key, no rendering envelope, no invented bookkeeping."""
    assert set(parse(json_renderer.render(content))) == set(ContentObject.model_fields)
    assert set(MAJOR_FIELDS) == set(ContentObject.model_fields)


def test_nested_detail_survives(json_renderer: JsonRenderer, content: ContentObject) -> None:
    """Provenance, digests, asset refs, and processing history are all there."""
    parsed = parse(json_renderer.render(content))

    assert parsed["schema_version"] == SCHEMA_VERSION
    assert parsed["original"]["sha256"] == ORIGINAL_SHA256
    assert parsed["segments"][0]["provenance"]["capture_id"] == content.source.capture_id
    assert parsed["assets"][0]["ref"] == f"sha256:{ORIGINAL_SHA256}"
    assert parsed["derived"]["topics"] == ["architecture", "contracts"]
    assert parsed["processing"][0]["processor"] == "text"
    assert parsed["metadata"]["counters"] == {"words": 6, "links": 2}


def test_textless_segments_are_kept(json_renderer: JsonRenderer, content: ContentObject) -> None:
    """Unlike Markdown, the JSON projection drops nothing."""
    parsed = parse(json_renderer.render(content))

    assert [segment["id"] for segment in parsed["segments"]] == [
        segment.id for segment in content.segments
    ]
    assert parsed["segments"][1]["text"] is None


# --- unicode ---------------------------------------------------------------


def test_unicode_is_not_escaped(json_renderer: JsonRenderer, content: ContentObject) -> None:
    rendered = json_renderer.render(content)

    assert UNICODE_TEXT in rendered
    assert "\\u" not in rendered


def test_unicode_survives_the_round_trip(
    json_renderer: JsonRenderer, content: ContentObject
) -> None:
    restored = ContentObject.model_validate_json(json_renderer.render(content))

    assert restored.segments[2].text == UNICODE_TEXT


# --- determinism -----------------------------------------------------------


def test_repeated_renders_are_identical(
    json_renderer: JsonRenderer, content: ContentObject
) -> None:
    assert json_renderer.render(content) == json_renderer.render(content)


def test_separate_renderer_instances_agree(content: ContentObject) -> None:
    assert JsonRenderer().render(content) == JsonRenderer().render(content)


def test_equal_objects_render_identically(json_renderer: JsonRenderer) -> None:
    """Determinism is a property of the object, not of one instance of it."""
    assert json_renderer.render(make_content_object()) == json_renderer.render(
        make_content_object()
    )


def test_mapping_insertion_order_does_not_change_the_output(
    json_renderer: JsonRenderer,
) -> None:
    """Two objects whose metadata was populated in different orders agree."""
    forwards = make_content_object(
        metadata={"language": "en", "counters": {"words": 6, "links": 2}, "draft": False}
    )
    backwards = make_content_object(
        metadata={"draft": False, "counters": {"links": 2, "words": 6}, "language": "en"}
    )

    assert json_renderer.render(forwards) == json_renderer.render(backwards)


def test_mapping_keys_are_sorted(json_renderer: JsonRenderer, content: ContentObject) -> None:
    parsed = parse(json_renderer.render(content))
    keys = list(parsed)

    assert keys == sorted(keys)
    assert list(parsed["metadata"]["counters"]) == ["links", "words"]


# --- list order ------------------------------------------------------------


def test_list_order_is_the_content_object_order(json_renderer: JsonRenderer) -> None:
    """Lists are domain state, so they are never sorted or renumbered."""
    content = make_content_object(
        segments=[
            make_segment(id="seg_c", text="third", position=90),
            make_segment(id="seg_a", text="first", position=10),
            make_textless_segment(),
            make_segment(id="seg_b", text="second", position=50),
        ],
        derived={"topics": ["zeta", "alpha"]},
    )

    parsed = parse(json_renderer.render(content))

    assert [segment["id"] for segment in parsed["segments"]] == [
        "seg_c",
        "seg_a",
        "seg_visual",
        "seg_b",
    ]
    assert parsed["derived"]["topics"] == ["zeta", "alpha"]


# --- purity ----------------------------------------------------------------


def test_rendering_does_not_mutate_the_content_object(
    json_renderer: JsonRenderer, content: ContentObject
) -> None:
    before = content.model_copy(deep=True)

    json_renderer.render(content)

    assert content == before
    assert content.model_dump(mode="json") == before.model_dump(mode="json")


def test_nan_is_refused_rather_than_written(json_renderer: JsonRenderer) -> None:
    """``NaN`` is not JSON, so it never reaches the output."""
    content = make_content_object(metadata={"score": float("nan")})

    with pytest.raises(ValueError, match="Out of range float"):
        json_renderer.render(content)
