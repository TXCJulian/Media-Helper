import pytest

from app.encoder.rules import Condition, Rule, RuleError, evaluate

FACTS_4K_DOVI = {
    "height": 2160, "video_codec": "hevc", "size": 21_902_137_344,
    "bit_rate": 24_000_000, "hdr": True, "dolby_vision": True, "bit_depth": 10,
    "source_tool": "makemkv",
    "encoder_tag": "libmakemkv v1.18.4 (1.3.10/1.5.2) win(x64-release)",
}
FACTS_1080_H264 = {
    "height": 1080, "video_codec": "h264", "size": 9_000_000_000,
    "bit_rate": 8_000_000, "hdr": False, "dolby_vision": False, "bit_depth": 8,
    "source_tool": "lavf", "encoder_tag": "Lavf62.3.100",
}


def _rule(rule_id, conditions, target):
    return Rule(id=rule_id, conditions=conditions, target=target)


def test_first_match_wins():
    rules = [
        _rule("r1", [Condition("height", ">=", 2160)], "CPU 4K"),
        _rule("r2", [Condition("height", ">=", 720)], "NVENC 1080p"),
    ]
    match = evaluate(FACTS_4K_DOVI, rules, fallback="skip")
    assert match.target == "CPU 4K"
    assert match.rule_id == "r1"


def test_conditions_are_anded():
    rules = [
        _rule("r1", [Condition("height", ">=", 2160),
                     Condition("dolby_vision", "==", False)], "GPU 4K"),
        _rule("r2", [Condition("height", ">=", 2160)], "CPU 4K"),
    ]
    # DoVi is present, so r1's second condition fails and r2 wins.
    assert evaluate(FACTS_4K_DOVI, rules, "skip").target == "CPU 4K"


def test_unmatched_falls_back():
    rules = [_rule("r1", [Condition("height", ">=", 4320)], "8K")]
    match = evaluate(FACTS_1080_H264, rules, fallback="skip")
    assert match.target == "skip"
    assert match.rule_id is None


def test_evaluated_lists_only_the_rules_actually_tested():
    """Rules after the winner are never evaluated; the UI shows this so an
    unreachable rule is visible before it costs an hour of encoding."""
    rules = [
        _rule("r1", [Condition("height", ">=", 4320)], "8K"),
        _rule("r2", [Condition("height", ">=", 2160)], "CPU 4K"),
        _rule("r3", [Condition("height", ">=", 720)], "NVENC"),
    ]
    match = evaluate(FACTS_4K_DOVI, rules, "skip")
    assert match.evaluated == ["r1", "r2"]
    assert "r3" not in match.evaluated


def test_skip_is_a_valid_target():
    rules = [_rule("r1", [Condition("video_codec", "==", "hevc")], "skip")]
    assert evaluate(FACTS_4K_DOVI, rules, "NVENC").target == "skip"


@pytest.mark.parametrize(
    "op,value,expected",
    [
        (">=", 2160, True), (">=", 2161, False),
        ("<=", 2160, True), ("<", 2160, False),
        (">", 1080, True), ("==", 2160, True), ("!=", 1080, True),
    ],
)
def test_numeric_operators(op, value, expected):
    rules = [_rule("r", [Condition("height", op, value)], "hit")]
    assert (evaluate(FACTS_4K_DOVI, rules, "miss").target == "hit") is expected


def test_string_equality_is_case_insensitive():
    """ffprobe reports `hevc`, but a user typing `HEVC` means the same thing."""
    rules = [_rule("r", [Condition("video_codec", "==", "HEVC")], "hit")]
    assert evaluate(FACTS_4K_DOVI, rules, "miss").target == "hit"


def test_boolean_conditions():
    rules = [_rule("r", [Condition("dolby_vision", "==", True)], "cpu")]
    assert evaluate(FACTS_4K_DOVI, rules, "gpu").target == "cpu"
    assert evaluate(FACTS_1080_H264, rules, "gpu").target == "gpu"


def test_size_in_bytes_compares_numerically_not_lexically():
    rules = [_rule("r", [Condition("size", "<", 8_000_000_000)], "skip")]
    assert evaluate(FACTS_1080_H264, rules, "encode").target == "encode"
    small = dict(FACTS_1080_H264, size=7_000_000_000)
    assert evaluate(small, rules, "encode").target == "skip"


def test_unknown_field_is_rejected_at_evaluation():
    """Catching this here rather than silently never matching: a typo'd field
    would otherwise look like a rule that simply never fires."""
    rules = [_rule("r", [Condition("heigth", ">=", 100)], "hit")]
    with pytest.raises(RuleError, match="heigth"):
        evaluate(FACTS_4K_DOVI, rules, "skip")


def test_unknown_operator_is_rejected():
    rules = [_rule("r", [Condition("height", "~=", 100)], "hit")]
    with pytest.raises(RuleError, match="~="):
        evaluate(FACTS_4K_DOVI, rules, "skip")


def test_missing_fact_does_not_match_rather_than_crashing():
    """A file whose probe lacks a field must fall through to the next rule."""
    rules = [_rule("r1", [Condition("bit_depth", ">=", 10)], "hit"),
             _rule("r2", [], "fallthrough")]
    facts = dict(FACTS_1080_H264, bit_depth=None)
    assert evaluate(facts, rules, "skip").target == "fallthrough"


def test_a_rule_with_no_conditions_always_matches():
    """This is how the UI's pinned fallback row behaves when made explicit."""
    rules = [_rule("r", [], "everything")]
    assert evaluate(FACTS_1080_H264, rules, "skip").target == "everything"


def test_an_unencoded_rip_is_selected_by_source_tool():
    """The rule this exists for: encode anything MakeMKV just produced."""
    rules = [_rule("r1", [Condition("source_tool", "==", "makemkv")], "CPU 4K")]
    assert evaluate(FACTS_4K_DOVI, rules, "skip").target == "CPU 4K"
    assert evaluate(FACTS_1080_H264, rules, "skip").target == "skip"


def test_an_already_encoded_file_can_be_skipped_by_source_tool():
    rules = [_rule("r1", [Condition("source_tool", "==", "lavf")], "skip"),
             _rule("r2", [], "CPU 4K")]
    assert evaluate(FACTS_1080_H264, rules, "skip").target == "skip"
    assert evaluate(FACTS_4K_DOVI, rules, "skip").target == "CPU 4K"


def test_codec_alone_would_have_skipped_an_unencoded_rip():
    """Regression against the original spec's example rule. The MakeMKV rip is
    HEVC, so `codec == hevc -> skip` skips a 92Mbps Dolby Vision rip -- exactly
    the file this feature exists to shrink. source_tool is the correct signal."""
    by_codec = [_rule("r1", [Condition("video_codec", "==", "hevc")], "skip")]
    assert evaluate(FACTS_4K_DOVI, by_codec, "CPU 4K").target == "skip"   # wrong
    by_tool = [_rule("r1", [Condition("source_tool", "==", "lavf")], "skip")]
    assert evaluate(FACTS_4K_DOVI, by_tool, "CPU 4K").target == "CPU 4K"  # right


def test_contains_matches_a_versioned_encoder_tag():
    """Ripper version strings change; an exact match would rot."""
    rules = [_rule("r1", [Condition("encoder_tag", "contains", "makemkv")], "CPU 4K")]
    assert evaluate(FACTS_4K_DOVI, rules, "skip").target == "CPU 4K"


def test_contains_is_case_insensitive_like_other_string_matches():
    rules = [_rule("r1", [Condition("encoder_tag", "contains", "MakeMKV")], "hit")]
    assert evaluate(FACTS_4K_DOVI, rules, "miss").target == "hit"


def test_contains_on_a_numeric_field_is_rejected():
    rules = [_rule("r1", [Condition("height", "contains", "21")], "hit")]
    with pytest.raises(RuleError, match="text fields only"):
        evaluate(FACTS_4K_DOVI, rules, "skip")


def test_empty_rule_list_returns_the_fallback():
    match = evaluate(FACTS_1080_H264, [], fallback="skip")
    assert match.target == "skip"
    assert match.evaluated == []
