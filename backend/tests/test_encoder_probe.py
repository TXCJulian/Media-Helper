import json
import subprocess

import pytest

from app.encoder import probe as probe_mod
from app.encoder.probe import ProbeError, probe

DOVI_JSON = {
    "format": {"size": "21902137344", "bit_rate": "24000000", "duration": "7200.0"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "profile": "Main 10",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "r_frame_rate": "24000/1001",
            "side_data_list": [
                {"side_data_type": "DOVI configuration record", "dv_profile": 7},
                {"side_data_type": "Mastering display metadata"},
            ],
        },
        {
            "codec_type": "audio",
            "codec_name": "truehd",
            "channels": 8,
            "tags": {"language": "eng", "title": "Atmos"},
        },
        {"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
    ],
}


def _fake_run(payload, returncode=0, stderr=""):
    def _run(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["ffprobe"], returncode=returncode,
            stdout=json.dumps(payload), stderr=stderr,
        )
    return _run


@pytest.fixture
def video(tmp_path):
    """A real (empty) file for `probe()`'s isfile guard -- ffprobe itself is
    always mocked in this module, so the content never matters, only that the
    path exists."""
    def _make(name="movie.mkv"):
        path = tmp_path / name
        path.write_bytes(b"")
        return str(path)
    return _make


def test_probe_rejects_a_path_that_does_not_exist(tmp_path):
    """Closes the SSRF/oracle hazard at the source: ffprobe resolves
    protocols, not just paths, so probe() must never be handed something
    that hasn't already been confirmed to be a real file."""
    with pytest.raises(ProbeError, match="No such file"):
        probe(str(tmp_path / "does-not-exist.mkv"))


def test_extracts_the_fields_rules_evaluate(monkeypatch, video):
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(DOVI_JSON))
    facts = probe(video())
    assert facts["height"] == 2160
    assert facts["video_codec"] == "hevc"
    assert facts["size"] == 21902137344
    assert facts["bit_rate"] == 24000000


def test_a_float_formatted_bit_rate_is_still_coerced_to_int(monkeypatch, video):
    """Some ffprobe builds emit float-formatted integers. Before the fix,
    `_as_int("24000000.0")` returned None, coerced to 0 -- a bitrate rule
    would silently never fire."""
    payload = {
        "format": {"size": "800", "bit_rate": "24000000.0", "duration": "10"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                     "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
                     "r_frame_rate": "24/1"}],
    }
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    assert probe(video())["bit_rate"] == 24000000


def test_detects_dolby_vision_and_hdr_from_side_data(monkeypatch, video):
    """The rule that matters most: DoVi must not go to a GPU encoder, which
    would silently drop the profile and leave standard HDR."""
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(DOVI_JSON))
    facts = probe(video())
    assert facts["dolby_vision"] is True
    assert facts["hdr"] is True


def test_bit_depth_comes_from_pix_fmt(monkeypatch, video):
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(DOVI_JSON))
    assert probe(video())["bit_depth"] == 10


def test_eight_bit_sdr_reports_no_hdr(monkeypatch, video):
    payload = {
        "format": {"size": "800", "bit_rate": "1000", "duration": "10"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                     "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
                     "r_frame_rate": "24/1"}],
    }
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    facts = probe(video())
    assert facts["hdr"] is False
    assert facts["dolby_vision"] is False
    assert facts["bit_depth"] == 8


def test_frame_rate_is_parsed_from_the_rational(monkeypatch, video):
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(DOVI_JSON))
    assert probe(video())["frame_rate"] == pytest.approx(23.976, abs=0.001)


def test_audio_and_subtitle_tracks_are_listed(monkeypatch, video):
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(DOVI_JSON))
    facts = probe(video())
    assert facts["audio"] == [
        {"codec": "truehd", "channels": 8, "language": "eng", "title": "Atmos"}
    ]
    assert facts["subtitles"] == [{"codec": "subrip", "language": "eng"}]


def test_cover_art_is_not_mistaken_for_the_video_stream(monkeypatch, video):
    """An attached_pic stream is a thumbnail; treating it as the video track
    would report a 600x600 'resolution' and match the wrong rule."""
    payload = {
        "format": {"size": "800", "bit_rate": "1000", "duration": "10"},
        "streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "width": 600,
             "height": 600, "disposition": {"attached_pic": 1}},
            {"codec_type": "video", "codec_name": "h264", "width": 1920,
             "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "24/1"},
        ],
    }
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    assert probe(video())["height"] == 1080


def test_a_makemkv_rip_is_identified_by_its_tags(monkeypatch, video):
    """The signal that says "nothing has re-muxed this yet". Note it is HEVC:
    a codec check would call this already-encoded and skip a 92Mbps rip."""
    payload = {
        "format": {"size": "66975638308", "bit_rate": "92101000", "duration": "6247",
                   "tags": {"title": "FNAF 2",
                            "encoder": "libmakemkv v1.18.4 (1.3.10/1.5.2) win(x64-release)"}},
        "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840,
                     "height": 2160, "pix_fmt": "yuv420p10le", "r_frame_rate": "24000/1001",
                     "tags": {"_STATISTICS_WRITING_APP-eng": "MakeMKV v1.18.4 win(x64-release)",
                              "SOURCE_ID-eng": "001011"}}],
    }
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    facts = probe(video("rip.mkv"))
    assert facts["source_tool"] == "makemkv"
    assert "libmakemkv" in facts["encoder_tag"]
    assert facts["video_codec"] == "hevc"   # ...and still needs encoding


def test_an_encoded_file_reports_the_lavf_muxer(monkeypatch, video):
    """HandBrake muxes through libavformat, so its output says Lavf, not
    HandBrake. The MakeMKV statistics tags are gone."""
    payload = {
        "format": {"size": "10000", "bit_rate": "7810000", "duration": "10571",
                   "tags": {"title": "The Batman", "ENCODER": "Lavf62.3.100"}},
        "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840,
                     "height": 1608, "pix_fmt": "yuv420p10le", "r_frame_rate": "24000/1001",
                     "tags": {"DURATION": "02:56:11"}}],
    }
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    facts = probe(video("done.mkv"))
    assert facts["source_tool"] == "lavf"


def test_the_encoder_tag_key_is_matched_case_insensitively(monkeypatch, video):
    """MakeMKV writes `encoder`; libav writes `ENCODER`."""
    for key in ("encoder", "ENCODER", "Encoder"):
        payload = {"format": {"size": "1", "bit_rate": "1", "duration": "1",
                              "tags": {key: "libmakemkv v1.18.4"}},
                   "streams": [{"codec_type": "video", "codec_name": "hevc",
                                "width": 100, "height": 100}]}
        monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
        assert probe(video())["source_tool"] == "makemkv"


def test_a_file_with_no_tags_is_unknown_not_an_error(monkeypatch, video):
    payload = {"format": {"size": "1", "bit_rate": "1", "duration": "1"},
               "streams": [{"codec_type": "video", "codec_name": "h264",
                            "width": 100, "height": 100}]}
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    facts = probe(video())
    assert facts["source_tool"] == "unknown"
    assert facts["encoder_tag"] == ""


def test_a_file_with_no_video_stream_raises(monkeypatch, video):
    payload = {"format": {"size": "1", "bit_rate": "1", "duration": "1"},
               "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}
    monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run(payload))
    with pytest.raises(ProbeError, match="no video stream"):
        probe(video("x.mp3"))


def test_ffprobe_failure_raises_probe_error(monkeypatch, video):
    monkeypatch.setattr(
        probe_mod.subprocess, "run", _fake_run({}, returncode=1, stderr="boom")
    )
    with pytest.raises(ProbeError, match="boom"):
        probe(video())


def test_malformed_json_raises_probe_error(monkeypatch, video):
    def _run(*_a, **_k):
        return subprocess.CompletedProcess(["ffprobe"], 0, stdout="not json", stderr="")
    monkeypatch.setattr(probe_mod.subprocess, "run", _run)
    with pytest.raises(ProbeError):
        probe(video())


def test_timeout_raises_probe_error(monkeypatch, video):
    def _run(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)
    monkeypatch.setattr(probe_mod.subprocess, "run", _run)
    with pytest.raises(ProbeError, match="timed out"):
        probe(video())


def test_bit_depth_detection_for_p010le_format():
    """p010le is semi-planar 10-bit used by hardware encoders and HDR files.
    The digit 010 (zero-padded) must be recognized as 10-bit."""
    assert probe_mod._bit_depth("p010le") == 10


def test_bit_depth_detection_for_p016le_format():
    """p016le is semi-planar 16-bit. Zero-padded 016 must be recognized as 16-bit."""
    assert probe_mod._bit_depth("p016le") == 16


def test_bit_depth_detection_for_yuv420p10le_format():
    """yuv420p10le is planar 10-bit. Digit 10 following p must be recognized."""
    assert probe_mod._bit_depth("yuv420p10le") == 10


def test_bit_depth_detection_for_8bit_format():
    """yuv420p is 8-bit (no depth digits following p). Should default to 8."""
    assert probe_mod._bit_depth("yuv420p") == 8


def test_bit_depth_detection_for_empty_format():
    """Empty pix_fmt should return None."""
    assert probe_mod._bit_depth("") is None


def test_side_data_is_requested(monkeypatch, video):
    """HDR/DoVi detection depends on it, and it is not in ffprobe's defaults."""
    seen = {}
    path = video()

    def _run(cmd, *_a, **_k):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps(DOVI_JSON), "")

    monkeypatch.setattr(probe_mod.subprocess, "run", _run)
    probe(path)
    assert "-show_streams" in seen["cmd"]
    assert "-show_format" in seen["cmd"]
    assert seen["cmd"][-1] == path


@pytest.mark.parametrize("pix_fmt,depth", [
    # Semi-planar: the FIRST digit is chroma subsampling, the last two are the
    # depth. Reading them as one number made p210le "210-bit" and p216le
    # "216-bit"; only the 4:2:0 variants came out right by luck, which is why
    # the hardware-encoder case looked correct while 4:2:2 and 4:4:4 did not.
    ("p010le", 10), ("p210le", 10), ("p410le", 10),
    ("p016le", 16), ("p216le", 16), ("p416le", 16),
    # Conventional planar: depth trails the final 'p'.
    ("yuv420p10le", 10), ("yuv422p10le", 10), ("yuv444p12le", 12),
    ("yuv420p9le", 9), ("gbrp10le", 10), ("yuv420p10", 10),
    # No depth digits at all.
    ("yuv420p", 8), ("yuvj420p", 8), ("nv12", 8), ("rgb24", 8),
])
def test_bit_depth_matches_the_ffmpeg_pixel_format_definitions(pix_fmt, depth):
    assert probe_mod._bit_depth(pix_fmt) == depth


@pytest.mark.parametrize("pix_fmt,depth", [
    # Packed 4:2:2 / 4:4:4 -- first digit is subsampling, as with p010le.
    ("y210le", 10), ("y212le", 12), ("y410le", 10), ("y216le", 16),
    # Interleaved formats state the TOTAL across components; a bit_depth rule
    # means per-component, so rgb48le is 16-bit, not 48-bit.
    ("rgb24", 8), ("bgr24", 8), ("rgb48le", 16), ("rgba64le", 16),
    ("gray", 8), ("gray10le", 10), ("gray16le", 16),
    # Float.
    ("gbrpf32le", 32), ("gbrapf32le", 32),
])
def test_bit_depth_covers_the_packed_interleaved_and_float_families(pix_fmt, depth):
    assert probe_mod._bit_depth(pix_fmt) == depth


@pytest.mark.parametrize("pix_fmt,depth", [
    # Padded RGB: two padding bits then the depth, stated directly.
    ("x2rgb10le", 10), ("x2bgr10le", 10),
    # XYZ states per-component depth.
    ("xyz12le", 12),
    # The packed 'v' family states the TOTAL across three components, with the
    # 'x' being padding rather than a component: xv30 is 3x10, xv36 is 3x12.
    ("xv30le", 10), ("v30xle", 10), ("xv36le", 12),
])
def test_bit_depth_covers_the_padded_xyz_and_v_families(pix_fmt, depth):
    assert probe_mod._bit_depth(pix_fmt) == depth
