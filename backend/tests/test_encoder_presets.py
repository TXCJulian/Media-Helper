import pytest

from app.encoder.presets import NamedPreset, PresetError, iter_presets, parse_document

NESTED = {
    "PresetList": [
        {
            "Folder": True,
            "PresetName": "My Presets",
            "ChildrenArray": [
                {
                    "PresetName": "NVENC 1080p",
                    "VideoEncoder": "nvenc_h265",
                    "VideoPreset": "medium",
                    "FileFormat": "av_mkv",
                },
                {
                    "PresetName": "CPU 4K",
                    "VideoEncoder": "x265_10bit",
                    "VideoPreset": "slow",
                    "FileFormat": "av_mkv",
                },
            ],
        },
        {
            "PresetName": "Top Level",
            "VideoEncoder": "x264",
            "VideoPreset": "veryfast",
            "FileFormat": "av_mp4",
        },
    ]
}


def test_walks_folders_and_returns_only_leaves():
    names = [p["PresetName"] for p in iter_presets(NESTED)]
    assert names == ["NVENC 1080p", "CPU 4K", "Top Level"]
    assert "My Presets" not in names


def test_parse_document_extracts_the_fields_rules_and_dispatch_need():
    presets = parse_document(NESTED)
    assert presets[0] == NamedPreset(
        name="NVENC 1080p",
        encoder="nvenc_h265",
        video_preset="medium",
        file_format="av_mkv",
        body=NESTED["PresetList"][0]["ChildrenArray"][0],
    )


def test_body_is_the_verbatim_leaf():
    """The encoder is sent the original leaf, not a reconstruction: HandBrake
    presets carry filter chains and audio copy masks this code never models."""
    presets = parse_document(NESTED)
    assert presets[0].body is NESTED["PresetList"][0]["ChildrenArray"][0]


def test_missing_video_encoder_is_rejected():
    with pytest.raises(PresetError, match="VideoEncoder"):
        parse_document({"PresetList": [{"PresetName": "Broken"}]})


def test_missing_name_is_rejected():
    with pytest.raises(PresetError, match="PresetName"):
        parse_document({"PresetList": [{"VideoEncoder": "x264"}]})


def test_absent_video_preset_is_empty_not_an_error():
    """Omitting VideoPreset is legal -- HandBrake uses the encoder default."""
    doc = {"PresetList": [{"PresetName": "P", "VideoEncoder": "x264",
                           "FileFormat": "av_mkv"}]}
    assert parse_document(doc)[0].video_preset == ""


def test_duplicate_names_are_rejected():
    """Presets are addressed by name when dispatching, so duplicates would make
    the choice ambiguous at the point it matters most."""
    doc = {"PresetList": [
        {"PresetName": "Same", "VideoEncoder": "x264", "FileFormat": "av_mkv"},
        {"PresetName": "Same", "VideoEncoder": "x265", "FileFormat": "av_mkv"},
    ]}
    with pytest.raises(PresetError, match="Same"):
        parse_document(doc)


def test_an_empty_document_yields_nothing():
    assert parse_document({"PresetList": []}) == []
    assert iter_presets({}) == []


def test_non_list_children_are_ignored_not_crashed_on():
    doc = {"PresetList": [{"Folder": True, "PresetName": "F", "ChildrenArray": None}]}
    assert iter_presets(doc) == []
