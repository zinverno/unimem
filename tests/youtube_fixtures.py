"""Synthetic captions, not downloaded material or evidence of live acceptance."""

from datetime import UTC, datetime

from unimem_youtube.artifact import CaptionArtifact, CaptionTrack

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
XML = (
    '<transcript><text start="1.25" dur="2.5">Привет &amp; мир — café 😺</text>'
    '<text start="4">  repeat\n``` yaml\nx: yes\n```  </text>'
    '<text start="5" dur="0">  repeat\n``` yaml\nx: yes\n```  </text>'
    '<text start="6" dur="1"> </text></transcript>'
)


def artifact(xml: str = XML) -> CaptionArtifact:
    return CaptionArtifact(
        retrieval_version="1.2.4",
        video_id=VIDEO_ID,
        source_url=URL,
        captured_at=datetime(2026, 9, 29, 12, tzinfo=UTC),
        track=CaptionTrack(
            language="Русский", language_code="ru", is_generated=False, is_translatable=True
        ),
        captions_xml=xml,
    )
