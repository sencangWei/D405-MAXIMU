"""Tests for the stdlib Annex-B H.265 -> MP4 remuxer.

No decoder is required: correctness is pinned by round-tripping the produced
MP4 (parse it back and compare NAL payload bytes with the source access units).
The parameter sets are the real Rockchip MPP ones captured from
192.168.113.161 (tests/fixtures/umi_hevc_parameter_sets.json).
"""

from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rk3576" / "collector" / "adapter"))

import umi_remux as rx  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "umi_hevc_parameter_sets.json").read_text())
VPS = bytes.fromhex(FIXTURE["rgb"]["vps"])
SPS = bytes.fromhex(FIXTURE["rgb"]["sps"])
PPS = bytes.fromhex(FIXTURE["rgb"]["pps"])


# ---------------------------------------------------------------------------
# synthetic Annex-B builders


def with_start_code(nal: bytes) -> bytes:
    """Prepend a 4-byte start code to a complete NAL (header included)."""
    return b"\x00\x00\x00\x01" + nal


def slice_nal(nal_type: int, first_slice: bool, size: int = 24, seed: int = 0) -> bytes:
    header = bytes([(nal_type << 1) & 0x7E, 0x01])
    body = bytes([0x80 if first_slice else 0x00]) + bytes(
        ((seed + i) * 7 + 3) & 0xFF for i in range(size)
    )
    return with_start_code(header + body)


def build_stream(aus: int, *, padding: int = 0, idr_every: int = 5) -> bytes:
    """A synthetic stream: VPS/SPS/PPS + IDR then TRAIL_R access units."""
    out = bytearray()
    for index in range(aus):
        is_idr = index % idr_every == 0
        if is_idr:
            out += with_start_code(VPS)
            out += with_start_code(SPS)
            out += with_start_code(PPS)
        out += slice_nal(19 if is_idr else 1, True, seed=index)
    out += b"\x00" * padding
    return bytes(out)


def source_access_units(aus: int, *, idr_every: int = 5, keep_ps: bool = False) -> list[list[bytes]]:
    """The NAL payloads (header included) the muxer is expected to write."""
    result = []
    for index in range(aus):
        is_idr = index % idr_every == 0
        nals = []
        if is_idr and keep_ps:
            nals += [VPS, SPS, PPS]
        nals.append(slice_nal(19 if is_idr else 1, True, seed=index)[4:])
        result.append(nals)
    return result


@pytest.fixture()
def stream(tmp_path: Path) -> Path:
    path = tmp_path / "rgb.h265.partial"
    path.write_bytes(build_stream(10, padding=128))
    return path


# ---------------------------------------------------------------------------
# SPS / hvcC


def test_parse_sps_reads_the_real_rockchip_parameter_set():
    info = rx.parse_sps(SPS)
    assert info["width"] == 1280
    assert info["height"] == 720
    assert info["profile_idc"] == 1  # Main
    assert info["level_idc"] == 120  # level 4.0
    assert info["chroma_format_idc"] == 1  # 4:2:0
    assert info["bit_depth_luma_minus8"] == 0
    assert info["bit_depth_chroma_minus8"] == 0
    assert info["max_sub_layers_minus1"] == 0
    assert info["temporal_id_nested"] is True


def test_parse_sps_rejects_a_non_sps_nal():
    with pytest.raises(rx.RemuxError):
        rx.parse_sps(VPS)


def test_build_hvcc_layout():
    hvcc = rx.build_hvcc(VPS, SPS, PPS)
    assert hvcc[0] == 1  # configurationVersion
    assert hvcc[1] == 1  # profile_space 0 | tier 0 | profile_idc 1
    assert hvcc[12] == 120  # level_idc
    assert hvcc[21] & 0x03 == 0x03  # lengthSizeMinusOne = 3
    assert hvcc[22] == 3  # numOfArrays
    offset = 23
    seen = []
    for _ in range(3):
        header = hvcc[offset]
        seen.append(header & 0x3F)
        assert header & 0x80  # array_completeness
        count = struct.unpack_from(">H", hvcc, offset + 1)[0]
        assert count == 1
        length = struct.unpack_from(">H", hvcc, offset + 3)[0]
        payload = hvcc[offset + 5 : offset + 5 + length]
        offset += 5 + length
        assert payload
    assert seen == [rx.NAL_VPS, rx.NAL_SPS, rx.NAL_PPS]
    assert offset == len(hvcc)


def test_build_hvcc_carries_parameter_sets_without_start_codes():
    hvcc = rx.build_hvcc(VPS, SPS, PPS)
    assert VPS in hvcc and SPS in hvcc and PPS in hvcc
    assert b"\x00\x00\x00\x01" not in hvcc


def test_strip_emulation_prevention_removes_only_the_escape_byte():
    assert rx.strip_emulation_prevention(b"\x00\x00\x03\x00") == b"\x00\x00\x00"
    assert rx.strip_emulation_prevention(b"\x00\x00\x03\x03") == b"\x00\x00\x03"
    assert rx.strip_emulation_prevention(b"\x41\x42") == b"\x41\x42"


# ---------------------------------------------------------------------------
# remux + round trip


def test_remux_round_trips_access_unit_payload_bytes(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    result = rx.remux(stream, dst, fps=30)
    assert result.samples == 10
    info, samples = rx.read_samples(dst)
    assert info["samples"] == 10
    assert info["sample_entry"] == "hvc1"
    expected = source_access_units(10)
    for index, payload in enumerate(samples):
        assert rx.split_length_prefixed(payload) == expected[index], f"AU {index} differs"


def test_remux_output_has_no_start_codes_inside_samples(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)
    _, samples = rx.read_samples(dst)
    for payload in samples:
        nals = rx.split_length_prefixed(payload)
        assert nals
        for nal_payload in nals:
            assert not nal_payload.startswith(b"\x00\x00\x01")


def test_remux_verify_roundtrip_passes_on_its_own_output(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    result = rx.remux(stream, dst)
    check = rx.verify_roundtrip(dst, expected_samples=10, expected_mdat_sha256=result.mdat_sha256)
    assert check.ok, check.problems
    assert check.sample_entry == "hvc1"
    assert check.has_hvcc
    assert check.width == 1280 and check.height == 720
    assert check.timescale == 90000
    assert check.duration == 10 * 3000


def test_remux_marks_irap_access_units_as_sync_samples(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)  # IDR every 5 -> samples 1, 6
    info, _ = rx.read_samples(dst)
    assert info["sync_samples"] == [1, 6]


def test_remux_omits_stss_when_every_sample_is_sync(tmp_path):
    path = tmp_path / "all_idr.h265"
    path.write_bytes(build_stream(4, idr_every=1, padding=128))
    dst = tmp_path / "all_idr.mp4"
    rx.remux(path, dst)
    info, _ = rx.read_samples(dst)
    assert info["sync_samples"] == [1, 2, 3, 4]


def test_remux_keeps_parameter_sets_in_band_for_hev1(stream, tmp_path):
    dst = tmp_path / "out_hev1.mp4"
    rx.remux(stream, dst, sample_entry="hev1")
    info, samples = rx.read_samples(dst)
    assert info["sample_entry"] == "hev1"
    expected = source_access_units(10, keep_ps=True)
    for index, payload in enumerate(samples):
        assert rx.split_length_prefixed(payload) == expected[index]


def test_remux_honours_max_aus_cap(stream, tmp_path):
    dst = tmp_path / "capped.mp4"
    result = rx.remux(stream, dst, max_aus=4)
    assert result.samples == 4
    info, _ = rx.read_samples(dst)
    assert info["samples"] == 4


# ---------------------------------------------------------------------------
# damaged tails


def test_remux_drops_a_tail_access_unit_that_reaches_eof(tmp_path):
    path = tmp_path / "cut.h265"
    path.write_bytes(build_stream(6))  # last AU ends at EOF -> cut mid-frame
    dst = tmp_path / "cut.mp4"
    result = rx.remux(path, dst)
    assert result.dropped_tail_au is True
    assert result.samples == 5
    assert result.truncated_bytes > 0


def test_remux_keeps_the_last_access_unit_when_zero_padding_follows(tmp_path):
    path = tmp_path / "padded.h265"
    path.write_bytes(build_stream(6, padding=128))
    dst = tmp_path / "padded.mp4"
    result = rx.remux(path, dst)
    assert result.dropped_tail_au is False
    assert result.samples == 6


def test_remux_stops_at_a_forbidden_zero_bit(tmp_path):
    data = bytearray(build_stream(6, padding=128))
    data += b"\x00\x00\x00\x01\x80\x01" + b"\x11" * 8  # forbidden_zero_bit set
    path = tmp_path / "corrupt.h265"
    path.write_bytes(bytes(data))
    dst = tmp_path / "corrupt.mp4"
    result = rx.remux(path, dst)
    assert result.samples == 6  # everything before the corruption is kept


def test_remux_fails_closed_without_parameter_sets(tmp_path):
    path = tmp_path / "nops.h265"
    path.write_bytes(slice_nal(1, True) + slice_nal(1, True))
    with pytest.raises(rx.RemuxError, match="VPS/SPS/PPS"):
        rx.remux(path, tmp_path / "nops.mp4")


def test_remux_fails_closed_on_an_empty_stream(tmp_path):
    path = tmp_path / "empty.h265"
    path.write_bytes(b"")
    with pytest.raises(rx.RemuxError):
        rx.remux(path, tmp_path / "empty.mp4")


def test_remux_leaves_no_partial_output_behind_on_failure(tmp_path):
    path = tmp_path / "nops.h265"
    path.write_bytes(slice_nal(1, True))
    dst = tmp_path / "nops.mp4"
    with pytest.raises(rx.RemuxError):
        rx.remux(path, dst)
    assert not dst.exists()
    assert not Path(str(dst) + ".tmp").exists()


def test_remux_never_modified_the_source(stream, tmp_path):
    before = stream.read_bytes()
    rx.remux(stream, tmp_path / "out.mp4")
    assert stream.read_bytes() == before


# ---------------------------------------------------------------------------
# verification is not fooled


def test_verify_roundtrip_rejects_a_corrupted_sample_table(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)
    data = bytearray(dst.read_bytes())
    offset = data.find(b"stsz")
    assert offset > 0
    struct.pack_into(">I", data, offset + 16, 999999)  # inflate the first sample size
    dst.write_bytes(bytes(data))
    check = rx.verify_roundtrip(dst)
    assert not check.ok
    assert any("sample sizes" in problem or "mdat payload" in problem for problem in check.problems)


def test_verify_roundtrip_rejects_a_wrong_mdat_digest(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)
    check = rx.verify_roundtrip(dst, expected_mdat_sha256="00" * 32)
    assert not check.ok
    assert any("mdat digest" in problem for problem in check.problems)


def test_verify_roundtrip_rejects_a_sample_count_mismatch(stream, tmp_path):
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)
    check = rx.verify_roundtrip(dst, expected_samples=99)
    assert not check.ok
    assert any("expected 99 samples" in problem for problem in check.problems)


def test_remux_raises_when_the_written_mdat_does_not_read_back(stream, tmp_path, monkeypatch):
    """A short write on the mdat region must be detected, not shipped."""
    real_hashlib = rx.hashlib
    calls = {"n": 0}

    class _LyingDigest:
        """The writer's digest is honest; the post-write re-read lies."""

        def __init__(self):
            calls["n"] += 1
            self._liar = calls["n"] > 1
            self._inner = real_hashlib.sha256()

        def update(self, data):
            self._inner.update(data)

        def hexdigest(self):
            return "ab" * 32 if self._liar else self._inner.hexdigest()

    monkeypatch.setattr(
        rx, "hashlib", type("m", (), {"sha256": staticmethod(lambda: _LyingDigest())})
    )
    with pytest.raises(rx.RemuxError, match="mdat verification failed"):
        rx.remux(stream, tmp_path / "bad.mp4")
    assert not (tmp_path / "bad.mp4").exists()
    assert not Path(str(tmp_path / "bad.mp4") + ".tmp").exists()


# ---------------------------------------------------------------------------
# box builder branches


def test_build_moov_uses_co64_when_the_chunk_offset_exceeds_four_gigabytes():
    from array import array as _array

    moov = rx.build_moov(
        sample_sizes=_array("I", [10, 20, 30]),
        sync_samples=[1],
        samples_per_chunk=3,
        chunk_offset=5 * 1024**3,
        timescale=90000,
        sample_delta=3000,
        width=1280,
        height=720,
        hvcc=rx.build_hvcc(VPS, SPS, PPS),
    )
    assert b"co64" in moov and b"stco" not in moov


def test_build_moov_rejects_an_empty_sample_table():
    from array import array as _array

    with pytest.raises(rx.RemuxError):
        rx.build_moov(
            sample_sizes=_array("I", []),
            sync_samples=[],
            samples_per_chunk=0,
            chunk_offset=100,
            timescale=90000,
            sample_delta=3000,
            width=1280,
            height=720,
            hvcc=rx.build_hvcc(VPS, SPS, PPS),
        )


def test_build_moov_rejects_an_unknown_sample_entry():
    from array import array as _array

    with pytest.raises(rx.RemuxError):
        rx.build_moov(
            sample_sizes=_array("I", [10]),
            sync_samples=[1],
            samples_per_chunk=1,
            chunk_offset=100,
            timescale=90000,
            sample_delta=3000,
            width=1280,
            height=720,
            hvcc=rx.build_hvcc(VPS, SPS, PPS),
            sample_entry="avc1",
        )


# ---------------------------------------------------------------------------
# probe


def test_probe_head_reports_annexb_parameter_sets_and_idr(stream):
    probe = rx.probe_head(stream)
    assert probe == {"annexb": True, "vps": True, "sps": True, "pps": True, "idr": True}


def test_probe_head_on_a_missing_file_is_false(tmp_path):
    assert rx.probe_head(tmp_path / "nope.h265") == {
        "annexb": False, "vps": False, "sps": False, "pps": False, "idr": False,
    }


def test_probe_head_detects_a_stream_without_parameter_sets(tmp_path):
    path = tmp_path / "slices_only.h265"
    path.write_bytes(slice_nal(1, True))
    probe = rx.probe_head(path)
    assert probe["annexb"] is True
    assert probe["vps"] is False


# ---------------------------------------------------------------------------
# robustness


def test_remux_handles_start_codes_spanning_read_boundaries(stream, tmp_path):
    """The scanner works on the whole mapping, so a 3-byte code at a page edge is fine."""
    dst = tmp_path / "out.mp4"
    rx.remux(stream, dst)
    info, _ = rx.read_samples(dst)
    assert info["samples"] == 10


def test_remux_accepts_three_byte_start_codes(tmp_path):
    out = bytearray()
    for payload in (VPS, SPS, PPS):
        out += b"\x00\x00\x01" + payload
    out += slice_nal(19, True)[1:]  # 3-byte start code (drop one zero)
    out += b"\x00" * 128
    path = tmp_path / "three_byte.h265"
    path.write_bytes(bytes(out))
    dst = tmp_path / "three_byte.mp4"
    rx.remux(path, dst)
    info, samples = rx.read_samples(dst)
    assert info["samples"] == 1
    assert rx.split_length_prefixed(samples[0])


def test_remux_reports_progress_monotonically(stream, tmp_path):
    seen = []
    rx.remux(stream, tmp_path / "out.mp4", progress=seen.append)
    assert seen == sorted(seen)


def test_remux_rejects_a_non_multiple_timescale(stream, tmp_path):
    with pytest.raises(rx.RemuxError):
        rx.remux(stream, tmp_path / "out.mp4", timescale=90001, fps=30)
