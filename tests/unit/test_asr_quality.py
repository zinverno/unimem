"""The research metric is stdlib-only and must not normalize away meaning errors."""

import runpy
from pathlib import Path


def test_fixed_word_metric() -> None:
    module = runpy.run_path(str(Path(__file__).parents[2] / "scripts/asr_quality.py"))
    words, score = module["words"], module["score"]
    assert words("Ёлка, \u041d\u0415: 1_2!") == ["елка", "не", "1", "2"]
    assert score("я не хочу", "я хочу") == {"N": 3, "S": 0, "D": 1, "I": 0, "WER": 1 / 3}
    assert score("a b c", "a x c")["S"] == 1
    assert score("a c", "a b c")["I"] == 1
    assert score("a b", "b a") == {"N": 2, "S": 2, "D": 0, "I": 0, "WER": 1.0}
    assert score("тысячу", "1000")["S"] == 1
    assert score("mister", "Mr.")["S"] == 1
    assert score("", "invented speech") == {"N": 0, "S": 0, "D": 0, "I": 2, "WER": None}
