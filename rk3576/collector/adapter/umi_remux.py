"""Stdlib Annex-B H.265 -> MP4 (hvc1) lossless remuxer.

Why this exists: on the RK3576 board GStreamer ships only plugins-base/good plus
Rockchip elements, so ``h265parse`` is absent and ``gstreamer1.0-plugins-bad``
has no install candidate. ``mp4mux``/``matroskamux`` accept H.265 only as
``hvc1``/``hev1`` (never ``byte-stream``) and ``mpph265enc`` emits
``byte-stream``, so neither a gst remux nor a gst transcode can produce a
playable container. A byte-copy muxer is therefore the only lossless option,
and it keeps the immutable release dependency-free (stdlib only).

The muxer never re-encodes: access units are copied verbatim, with start codes
replaced by 4-byte length prefixes. Re-encoding would destroy the y8 luma
infrared streams that stereo SLAM depends on.

Output layout: ``ftyp`` | ``mdat`` (64-bit largesize) | ``moov``.
Samples are written contiguously as a single chunk, so ``stco``/``co64`` holds
one offset and ``stsc`` one entry; that keeps a 4h44m stream's sample tables
small (a few MB) while staying inside the ISO-BMFF rules.

Verification does not rely on a software H.265 decoder (neither the host nor
the board has one): the mdat region is hashed while writing and re-read after
fsync, and the box structure is parsed back. On the board the produced file is
additionally decoded by ``qtdemux ! mppvideodec``.
"""

from __future__ import annotations

import hashlib
import mmap
import os
import struct
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

# NAL unit types (HEVC)
NAL_VPS = 32
NAL_SPS = 33
NAL_PPS = 34
NAL_AUD = 35
NAL_EOS = 36
NAL_EOB = 37
NAL_FILLER = 38
NAL_PREFIX_SEI = 39
NAL_SUFFIX_SEI = 40
NAL_IRAP_MIN = 16  # BLA_W_LP
NAL_IRAP_MAX = 23  # RSV_IRAP_VCL23
PARAMETER_SETS = (NAL_VPS, NAL_SPS, NAL_PPS)
PREFIX_NALS = (NAL_VPS, NAL_SPS, NAL_PPS, NAL_PREFIX_SEI)

DEFAULT_TIMESCALE = 90000
DEFAULT_FPS = 30
SAMPLE_ENTRY_DEFAULT = "hvc1"  # switch to "hev1" if the board decoder rejects hvc1
TRAILING_ZERO_RUN = 64  # >= this many trailing 0x00 bytes are padding, not data
VISUAL_SAMPLE_ENTRY_HEADER = 78  # fixed fields before the nested config box


class RemuxError(ValueError):
    """Raised when a stream cannot be remuxed; the source is never modified."""


@dataclass
class AccessUnit:
    start: int
    end: int
    nals: list[tuple[int, int, int]]  # (nal_offset, nal_end, nal_type)
    is_sync: bool
    ends_at_eof: bool


@dataclass
class RemuxResult:
    samples: int
    bytes_written: int
    mdat_sha256: str
    mdat_offset: int
    mdat_size: int
    timescale: int
    duration: int
    sample_entry: str
    truncated_bytes: int
    dropped_tail_au: bool
    width: int
    height: int
    hvcc: bytes = b""


@dataclass
class VerifyResult:
    samples: int
    mdat_sha256: str
    sample_entry: str
    sync_samples: int
    duration: int
    timescale: int
    width: int
    height: int
    has_hvcc: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------------------
# bit-level helpers


def strip_emulation_prevention(data: bytes) -> bytes:
    """Remove 0x000003 emulation-prevention bytes from an RBSP payload."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 2 < n and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
            out += b"\x00\x00"
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


class _BitReader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def u(self, count: int) -> int:
        value = 0
        for _ in range(count):
            byte_index = self._pos >> 3
            if byte_index >= len(self._data):
                raise RemuxError("bit reader ran past the end of the SPS")
            bit = (self._data[byte_index] >> (7 - (self._pos & 7))) & 1
            value = (value << 1) | bit
            self._pos += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 32:
                raise RemuxError("invalid exp-golomb code in the SPS")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)


def parse_sps(sps_nal: bytes) -> dict:
    """Parse the fields hvcC needs out of an SPS NAL (header included)."""
    if len(sps_nal) < 4:
        raise RemuxError("SPS is too short")
    nal_type = (sps_nal[0] >> 1) & 0x3F
    if nal_type != NAL_SPS:
        raise RemuxError("not an SPS NAL")
    reader = _BitReader(strip_emulation_prevention(sps_nal[2:]))
    sps_video_parameter_set_id = reader.u(4)
    max_sub_layers_minus1 = reader.u(3)
    temporal_id_nesting = reader.u(1)
    profile_space = reader.u(2)
    tier_flag = reader.u(1)
    profile_idc = reader.u(5)
    compatibility = reader.u(32)
    constraints = reader.u(48)
    level_idc = reader.u(8)
    # sub-layer flags are only present when max_sub_layers_minus1 > 0
    for _ in range(max_sub_layers_minus1):
        reader.u(1)  # sub_layer_profile_present_flag
        reader.u(1)  # sub_layer_level_present_flag
    if max_sub_layers_minus1 > 0:
        for _ in range(max_sub_layers_minus1, 8):
            reader.u(2)  # reserved_zero_2bits
    for _ in range(max_sub_layers_minus1):
        reader.u(88)  # sub-layer profile/tier/level, not needed for hvcC
    reader.ue()  # sps_seq_parameter_set_id
    chroma_format_idc = reader.ue()
    if chroma_format_idc == 3:
        reader.u(1)  # separate_colour_plane_flag
    width = reader.ue()
    height = reader.ue()
    if reader.u(1):  # conformance_window_flag
        reader.ue()  # conf_win_left_offset
        reader.ue()  # conf_win_right_offset
        reader.ue()  # conf_win_top_offset
        reader.ue()  # conf_win_bottom_offset
    bit_depth_luma_minus8 = reader.ue()
    bit_depth_chroma_minus8 = reader.ue()
    if width <= 0 or height <= 0:
        raise RemuxError("SPS carries a non-positive picture size")
    return {
        "sps_video_parameter_set_id": sps_video_parameter_set_id,
        "max_sub_layers_minus1": max_sub_layers_minus1,
        "temporal_id_nested": bool(temporal_id_nesting),
        "profile_space": profile_space,
        "tier_flag": tier_flag,
        "profile_idc": profile_idc,
        "compatibility_flags": compatibility,
        "constraint_flags": constraints,
        "level_idc": level_idc,
        "chroma_format_idc": chroma_format_idc,
        "width": width,
        "height": height,
        "bit_depth_luma_minus8": bit_depth_luma_minus8,
        "bit_depth_chroma_minus8": bit_depth_chroma_minus8,
    }


def build_hvcc(vps: bytes, sps: bytes, pps: bytes) -> bytes:
    """Build a HEVCDecoderConfigurationRecord (the ``hvcC`` box payload)."""
    info = parse_sps(sps)
    if (vps[0] >> 1) & 0x3F != NAL_VPS:
        raise RemuxError("first parameter set is not a VPS")
    if (pps[0] >> 1) & 0x3F != NAL_PPS:
        raise RemuxError("third parameter set is not a PPS")
    record = bytearray()
    record.append(1)  # configurationVersion
    record.append(info["profile_space"] << 6 | info["tier_flag"] << 5 | info["profile_idc"])
    record += info["compatibility_flags"].to_bytes(4, "big")
    record += info["constraint_flags"].to_bytes(6, "big")
    record.append(info["level_idc"])
    record += struct.pack(">H", 0xF000)  # min_spatial_segmentation_idc = 0, reserved 1111b
    record.append(0xFC)  # parallelismType = 0, reserved 111111b
    record.append(0xFC | (info["chroma_format_idc"] & 0x03))
    record.append(0xF8 | (info["bit_depth_luma_minus8"] & 0x07))
    record.append(0xF8 | (info["bit_depth_chroma_minus8"] & 0x07))
    record += struct.pack(">H", 0)  # avgFrameRate
    record.append(
        (0 << 6)  # constantFrameRate
        | (((info["max_sub_layers_minus1"] + 1) & 0x07) << 3)
        | ((1 if info["temporal_id_nested"] else 0) << 2)
        | 0x03  # lengthSizeMinusOne = 3 (4-byte NAL length prefixes)
    )
    arrays = ((NAL_VPS, vps), (NAL_SPS, sps), (NAL_PPS, pps))
    record.append(len(arrays))
    for nal_type, nal in arrays:
        record.append(0x80 | (nal_type & 0x3F))  # array_completeness = 1
        record += struct.pack(">H", 1)
        record += struct.pack(">H", len(nal))
        record += nal
    return bytes(record)


# ---------------------------------------------------------------------------
# box helpers


def box(type_: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + type_ + payload


def full_box(type_: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(type_, bytes([version]) + flags.to_bytes(3, "big") + payload)


def iter_boxes(data, start: int = 0, end: int | None = None) -> Iterator[tuple[bytes, int, int, int]]:
    """Yield (type, payload_offset, payload_end, box_start) for sibling boxes."""
    if end is None:
        end = len(data)
    offset = start
    while offset + 8 <= end:
        size = struct.unpack_from(">I", data, offset)[0]
        type_ = bytes(data[offset + 4 : offset + 8])
        header = 8
        if size == 1:
            if offset + 16 > end:
                raise RemuxError("truncated 64-bit box header")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise RemuxError(f"box {type_!r} has an invalid size")
        yield type_, offset + header, offset + size, offset
        offset += size


def find_sample_entry_config(data, payload_start: int, payload_end: int):
    """Locate the codec config box nested in a visual sample entry (hvcC)."""
    return find_box(
        data, [b"hvcC"], payload_start + VISUAL_SAMPLE_ENTRY_HEADER, payload_end
    )


def find_box(data, path: list[bytes], start: int = 0, end: int | None = None):
    """Locate a nested box; returns (payload_offset, payload_end) or None."""
    if end is None:
        end = len(data)
    if not path:
        return (start, end)
    head, *rest = path
    for type_, payload_start, payload_end, _ in iter_boxes(data, start, end):
        if type_ == head:
            return find_box(data, rest, payload_start, payload_end)
    return None


# ---------------------------------------------------------------------------
# Annex-B scanning


def _nal_payload_end(mm: mmap.mmap, offset: int, size: int) -> int:
    """End of a NAL that starts at ``offset``: next start code or padding start."""
    nxt = mm.find(b"\x00\x00\x01", offset + 3)
    end = size if nxt == -1 else (nxt - 1 if nxt > 0 and mm[nxt - 1] == 0 else nxt)
    # byte_stream_nal_unit allows trailing_zero_8bits after a NAL: a long zero run at
    # the end of the file is pre-allocated padding, not picture data
    i = end
    limit = max(offset + 2, end - 4096)
    while i > limit and mm[i - 1] == 0:
        i -= 1
    if end - i >= TRAILING_ZERO_RUN:
        return i
    return end


def scan_access_units(
    mm: mmap.mmap,
    size: int,
    *,
    max_aus: int | None = None,
    progress: Callable[[int], None] | None = None,
    progress_step: int = 64 * 1024 * 1024,
) -> Iterator[AccessUnit]:
    """Yield access units in decode order. Stops at the first malformed NAL."""
    current: list[tuple[int, int, int]] = []
    has_vcl = False
    au_start = None
    next_report = progress_step

    def flush(end: int, ends_at_eof: bool) -> AccessUnit | None:
        nonlocal current, has_vcl, au_start
        if not current or not has_vcl:
            current = []
            has_vcl = False
            au_start = None
            return None
        nals = current
        is_sync = any(NAL_IRAP_MIN <= t <= NAL_IRAP_MAX for _, _, t in nals)
        unit = AccessUnit(au_start, end, nals, is_sync, ends_at_eof)
        current = []
        has_vcl = False
        au_start = None
        return unit

    position = mm.find(b"\x00\x00\x01", 0)
    emitted = 0
    last_nal_end = 0
    while position != -1:
        if position + 4 > size:
            break
        if position > 0 and mm[position - 1] == 0:
            nal_offset = position + 3  # 4-byte start code
        else:
            nal_offset = position + 3  # 3-byte start code
        if nal_offset >= size:
            break
        header = mm[nal_offset]
        if header & 0x80:
            break  # forbidden_zero_bit set: stream is corrupt from here on
        nal_type = (header >> 1) & 0x3F
        if nal_type > NAL_SUFFIX_SEI:
            break  # not a defined NAL type: stop rather than guess
        nal_end = _nal_payload_end(mm, nal_offset, size)
        if nal_end <= nal_offset:
            break
        ends_at_eof = nal_end >= size
        last_nal_end = nal_end

        if nal_type in (NAL_EOS, NAL_EOB):
            current.append((nal_offset, nal_end, nal_type))
            unit = flush(nal_end, ends_at_eof)
            if unit is not None:
                yield unit
                emitted += 1
                if max_aus is not None and emitted >= max_aus:
                    return
        elif nal_type == NAL_AUD:
            unit = flush(au_start if au_start is not None else nal_offset, False)
            if unit is not None:
                yield unit
                emitted += 1
                if max_aus is not None and emitted >= max_aus:
                    return
            au_start = nal_offset
            current = [(nal_offset, nal_end, nal_type)]
        elif nal_type <= 31:  # VCL
            if len(mm) < nal_offset + 3:
                break
            first_slice = bool(mm[nal_offset + 2] & 0x80)
            if has_vcl and first_slice:
                unit = flush(nal_offset, False)
                if unit is not None:
                    yield unit
                    emitted += 1
                    if max_aus is not None and emitted >= max_aus:
                        return
                au_start = nal_offset
            elif au_start is None:
                au_start = nal_offset
            current.append((nal_offset, nal_end, nal_type))
            has_vcl = True
        else:  # non-VCL, non-AUD
            if has_vcl and nal_type in PREFIX_NALS:
                unit = flush(nal_offset, False)
                if unit is not None:
                    yield unit
                    emitted += 1
                    if max_aus is not None and emitted >= max_aus:
                        return
                au_start = nal_offset
            elif au_start is None:
                au_start = nal_offset
            current.append((nal_offset, nal_end, nal_type))

        if progress is not None and nal_end >= next_report:
            progress(nal_end)
            next_report = nal_end + progress_step
        position = mm.find(b"\x00\x00\x01", nal_end)

    # the final AU ends where its last NAL ends: trailing zero padding is not part
    # of the picture, so a padded tail is a complete AU while a cut one is not
    final_end = last_nal_end or size
    unit = flush(final_end, final_end >= size)
    if unit is not None:
        yield unit


def probe_head(path: Path, limit: int = 1 << 20) -> dict:
    """Cheap 'is this worth rescuing' signal: Annex-B + parameter sets + an IDR."""
    found = {"annexb": False, "vps": False, "sps": False, "pps": False, "idr": False}
    try:
        with path.open("rb") as handle:
            data = handle.read(limit)
    except OSError:
        return found
    position = data.find(b"\x00\x00\x01")
    while position != -1 and position + 4 < len(data):
        offset = position + 3
        header = data[offset]
        if header & 0x80:
            break
        nal_type = (header >> 1) & 0x3F
        found["annexb"] = True
        for key, value in (
            ("vps", NAL_VPS), ("sps", NAL_SPS), ("pps", NAL_PPS),
        ):
            if nal_type == value:
                found[key] = True
        if NAL_IRAP_MIN <= nal_type <= NAL_IRAP_MAX:
            found["idr"] = True
        if found["vps"] and found["sps"] and found["pps"] and found["idr"]:
            break
        position = data.find(b"\x00\x00\x01", offset)
    return found


# ---------------------------------------------------------------------------
# moov construction


def build_moov(
    *,
    sample_sizes: array,
    sync_samples: list[int],
    samples_per_chunk: int,
    chunk_offset: int,
    timescale: int,
    sample_delta: int,
    width: int,
    height: int,
    hvcc: bytes,
    sample_entry: str = SAMPLE_ENTRY_DEFAULT,
) -> bytes:
    """Pure builder for the movie box (unit-testable without touching disk)."""
    sample_count = len(sample_sizes)
    if sample_count == 0:
        raise RemuxError("cannot build a movie with no samples")
    if sample_entry not in ("hvc1", "hev1"):
        raise RemuxError("sample entry must be hvc1 or hev1")
    media_duration = sample_count * sample_delta
    movie_duration = int(round(media_duration * 1000 / timescale))

    def lang(code: str = "und") -> int:
        return (
            ((ord(code[0]) - 0x60) << 10) | ((ord(code[1]) - 0x60) << 5) | (ord(code[2]) - 0x60)
        )

    mvhd = full_box(
        b"mvhd", 0, 0,
        struct.pack(
            ">IIII", 0, 0, 1000, movie_duration
        )
        + struct.pack(">IHH", 0x00010000, 0x0100, 0)
        + b"\x00" * 8
        + struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
        + b"\x00" * 24
        + struct.pack(">I", 2),
    )
    tkhd = full_box(
        b"tkhd", 0, 0x000007,
        struct.pack(">IIIII", 0, 0, 1, 0, movie_duration)
        + b"\x00" * 8
        + struct.pack(">HHHH", 0, 0, 0, 0)
        + struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
        + struct.pack(">II", width << 16, height << 16),
    )
    mdhd = full_box(
        b"mdhd", 0, 0,
        struct.pack(">IIIIHH", 0, 0, timescale, media_duration, lang(), 0),
    )
    hdlr = full_box(
        b"hdlr", 0, 0,
        struct.pack(">I", 0) + b"vide" + b"\x00" * 12 + b"VideoHandler\x00",
    )
    vmhd = full_box(b"vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    dref = full_box(b"dref", 0, 0, struct.pack(">I", 1) + full_box(b"url ", 0, 1, b""))
    dinf = box(b"dinf", dref)

    hvcC = box(b"hvcC", hvcc)
    sample_entry_payload = (
        b"\x00" * 6
        + struct.pack(">H", 1)  # data_reference_index
        + struct.pack(">HH", 0, 0)  # pre_defined, reserved
        + b"\x00" * 12  # pre_defined[3]
        + struct.pack(">HH", width, height)
        + struct.pack(">II", 0x00480000, 0x00480000)  # 72 dpi
        + struct.pack(">I", 0)  # reserved
        + struct.pack(">H", 1)  # frame_count
        + b"\x00" * 32  # compressorname
        + struct.pack(">H", 0x0018)  # depth
        + struct.pack(">h", -1)  # pre_defined
        + hvcC
    )
    stsd = full_box(b"stsd", 0, 0, struct.pack(">I", 1) + box(sample_entry.encode(), sample_entry_payload))

    stts = full_box(b"stts", 0, 0, struct.pack(">I", 1) + struct.pack(">II", sample_count, sample_delta))
    stsc = full_box(
        b"stsc", 0, 0,
        struct.pack(">I", 1) + struct.pack(">III", 1, samples_per_chunk, 1),
    )
    # MP4 tables are big-endian; array('I') is native-endian, so swap a copy
    sizes_be = array("I", sample_sizes)
    if sys.byteorder == "little":
        sizes_be.byteswap()
    stsz = full_box(
        b"stsz", 0, 0, struct.pack(">II", 0, sample_count) + sizes_be.tobytes()
    )
    if chunk_offset + sum(sample_sizes) <= 0xFFFFFFFF and chunk_offset <= 0xFFFFFFFF:
        stco = full_box(b"stco", 0, 0, struct.pack(">I", 1) + struct.pack(">I", chunk_offset))
        offsets = stco
    else:
        offsets = full_box(b"co64", 0, 0, struct.pack(">I", 1) + struct.pack(">Q", chunk_offset))
    if len(sync_samples) == sample_count:
        stss = b""  # every sample is a sync sample
    else:
        stss = full_box(
            b"stss", 0, 0,
            struct.pack(">I", len(sync_samples)) + b"".join(struct.pack(">I", s) for s in sync_samples),
        )
    stbl = box(b"stbl", stsd + stts + stss + stsc + stsz + offsets)
    minf = box(b"minf", vmhd + dinf + stbl)
    mdia = box(b"mdia", mdhd + hdlr + minf)
    trak = box(b"trak", tkhd + mdia)
    return box(b"moov", mvhd + trak)


# ---------------------------------------------------------------------------
# remux


def _ftyp(sample_entry: str) -> bytes:
    compatible = b"isomiso2mp41" + sample_entry.encode()
    return box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + compatible)


def remux(
    src: Path,
    dst: Path,
    *,
    max_aus: int | None = None,
    sample_entry: str = SAMPLE_ENTRY_DEFAULT,
    timescale: int = DEFAULT_TIMESCALE,
    fps: int = DEFAULT_FPS,
    width: int | None = None,
    height: int | None = None,
    progress: Callable[[int], None] | None = None,
) -> RemuxResult:
    """Remux an Annex-B H.265 file into MP4. The source is never modified."""
    if sample_entry not in ("hvc1", "hev1"):
        raise RemuxError("sample entry must be hvc1 or hev1")
    if fps <= 0 or timescale % fps:
        raise RemuxError("timescale must be a multiple of the frame rate")
    sample_delta = timescale // fps
    src = Path(src)
    size = src.stat().st_size
    if size == 0:
        raise RemuxError("source stream is empty")
    tmp = Path(str(dst) + ".tmp")
    parameter_sets: dict[int, bytes] = {}
    sample_sizes = array("I")
    sync_samples: list[int] = []
    dropped_tail = False
    truncated_bytes = 0
    sps_info: dict | None = None

    with src.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        aus = scan_access_units(mm, size, max_aus=max_aus, progress=progress)
        digest = hashlib.sha256()
        try:
            with tmp.open("wb") as out:
                out.write(_ftyp(sample_entry))
                out.write(struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 0))
                mdat_offset = out.tell()
                payload_start = mdat_offset
                for unit in aus:
                    if unit.ends_at_eof:
                        # the last AU may have been cut mid-frame by the power loss
                        dropped_tail = True
                        truncated_bytes = max(0, size - unit.start)
                        break
                    if sps_info is None:
                        for nal_offset, nal_end, nal_type in unit.nals:
                            if nal_type in PARAMETER_SETS and nal_type not in parameter_sets:
                                parameter_sets[nal_type] = bytes(mm[nal_offset:nal_end])
                        if NAL_SPS in parameter_sets:
                            sps_info = parse_sps(parameter_sets[NAL_SPS])
                    chunk = bytearray()
                    for nal_offset, nal_end, nal_type in unit.nals:
                        if sample_entry == "hvc1" and nal_type in PARAMETER_SETS:
                            continue  # carried once in hvcC instead
                        length = nal_end - nal_offset
                        chunk += struct.pack(">I", length)
                        chunk += mm[nal_offset:nal_end]
                    if not chunk:
                        continue
                    out.write(chunk)
                    digest.update(chunk)
                    sample_sizes.append(len(chunk))
                    if unit.is_sync:
                        sync_samples.append(len(sample_sizes))
                payload_end = out.tell()
                payload_size = payload_end - payload_start
                out.seek(mdat_offset - 8)
                out.write(struct.pack(">Q", 16 + payload_size))
                out.seek(payload_end)  # back to the end before appending moov
                if not sample_sizes:
                    raise RemuxError("no complete access units were found")
                for required in PARAMETER_SETS:
                    if required not in parameter_sets:
                        raise RemuxError("stream is missing VPS/SPS/PPS")
                if sps_info is None:
                    sps_info = parse_sps(parameter_sets[NAL_SPS])
                hvcc = build_hvcc(
                    parameter_sets[NAL_VPS], parameter_sets[NAL_SPS], parameter_sets[NAL_PPS]
                )
                moov = build_moov(
                    sample_sizes=sample_sizes,
                    sync_samples=sync_samples,
                    samples_per_chunk=len(sample_sizes),
                    chunk_offset=payload_start,
                    timescale=timescale,
                    sample_delta=sample_delta,
                    width=width or sps_info["width"],
                    height=height or sps_info["height"],
                    hvcc=hvcc,
                    sample_entry=sample_entry,
                )
                out.write(moov)
                out.flush()
                os.fsync(out.fileno())
                bytes_written = out.tell()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        mdat_digest = digest.hexdigest()
        # re-read only the produced mdat region: catches short writes and torn pages
        with tmp.open("rb") as check:
            check.seek(payload_start)
            remaining = payload_size
            verify = hashlib.sha256()
            while remaining:
                block = check.read(min(1 << 22, remaining))
                if not block:
                    break
                verify.update(block)
                remaining -= len(block)
        if verify.hexdigest() != mdat_digest or remaining:
            tmp.unlink(missing_ok=True)
            raise RemuxError("mdat verification failed after writing")

    os.rename(tmp, dst)
    return RemuxResult(
        samples=len(sample_sizes),
        bytes_written=bytes_written,
        mdat_sha256=mdat_digest,
        mdat_offset=payload_start,
        mdat_size=payload_size,
        timescale=timescale,
        duration=len(sample_sizes) * sample_delta,
        sample_entry=sample_entry,
        truncated_bytes=truncated_bytes,
        dropped_tail_au=dropped_tail,
        width=width or sps_info["width"],
        height=height or sps_info["height"],
        hvcc=hvcc,
    )


# ---------------------------------------------------------------------------
# verification / reading back


def verify_roundtrip(
    path: Path,
    *,
    expected_samples: int | None = None,
    expected_mdat_sha256: str | None = None,
) -> VerifyResult:
    """Parse the produced MP4 back and report structural problems."""
    path = Path(path)
    size = path.stat().st_size
    problems: list[str] = []
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            moov = find_box(mm, [b"moov"])
            if moov is None:
                raise RemuxError("produced file has no moov box")
            stbl = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl"])
            if stbl is None:
                raise RemuxError("produced file has no sample table")
            stsz_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsz"])
            stco_box = find_box(
                mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stco"]
            ) or find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"co64"])
            stsc_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsc"])
            stts_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stts"])
            mdat = find_box(mm, [b"mdat"])
            if stsz_box is None or stco_box is None or stsc_box is None or stts_box is None or mdat is None:
                raise RemuxError("produced file is missing a mandatory box")
            sample_size, sample_count = struct.unpack_from(">II", mm, stsz_box[0] + 4)
            sizes = (
                [sample_size] * sample_count
                if sample_size
                else list(struct.unpack_from(f">{sample_count}I", mm, stsz_box[0] + 12))
            )
            if sample_size:
                _ = sizes  # constant-size form; still validated below
            if abs(stco_box[1] - stco_box[0]) == 12:
                chunk_offset = struct.unpack_from(">I", mm, stco_box[0] + 8)[0]
            else:
                chunk_offset = struct.unpack_from(">Q", mm, stco_box[0] + 8)[0]
            entry_count, first_chunk, samples_per_chunk, _desc = struct.unpack_from(
                ">IIII", mm, stsc_box[0] + 4
            )
            entry_count_stts, stts_count, stts_delta = struct.unpack_from(">III", mm, stts_box[0] + 4)
            mdhd = find_box(mm, [b"moov", b"trak", b"mdia", b"mdhd"])
            timescale, duration = struct.unpack_from(">II", mm, mdhd[0] + 12)
            tkhd = find_box(mm, [b"moov", b"trak", b"tkhd"])
            width, height = struct.unpack_from(">II", mm, tkhd[0] + 76)
            stsd = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"])
            entry = None
            has_hvcc = False
            if stsd is not None:
                for type_, payload_start, payload_end, _ in iter_boxes(mm, stsd[0] + 8, stsd[1]):
                    entry = type_.decode("ascii", "replace")
                    has_hvcc = (
                        find_sample_entry_config(mm, payload_start, payload_end) is not None
                        if entry in ("hvc1", "hev1")
                        else False
                    )
                    break
            sync_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stss"])
            sync_samples = (
                struct.unpack_from(">I", mm, sync_box[0] + 4)[0]
                if sync_box is not None
                else sample_count
            )
            mdat_payload = mdat[1] - mdat[0]
            mdat_digest = hashlib.sha256(mm[mdat[0] : mdat[1]]).hexdigest()

    if sum(sizes) != mdat_payload:
        problems.append(
            f"sample sizes ({sum(sizes)}) do not cover the mdat payload ({mdat_payload})"
        )
    if sample_size and sample_size * sample_count != mdat_payload:
        problems.append("constant sample size does not match the mdat payload")
    if entry_count != 1 or first_chunk != 1 or _desc != 1:
        problems.append("stsc must describe a single chunk")
    if samples_per_chunk != sample_count:
        problems.append("stsc sample count does not match stsz")
    if entry_count_stts != 1 or stts_count != sample_count:
        problems.append("stts does not describe every sample")
    if not has_hvcc:
        problems.append("sample entry has no hvcC configuration")
    if not sync_samples:
        problems.append("no sync sample was recorded")
    if expected_samples is not None and sample_count != expected_samples:
        problems.append(f"expected {expected_samples} samples, found {sample_count}")
    if expected_mdat_sha256 is not None and mdat_digest != expected_mdat_sha256:
        problems.append("mdat digest does not match the remux result")
    if chunk_offset < 8 or chunk_offset + mdat_payload > size:
        problems.append("chunk offset points outside the file")
    return VerifyResult(
        samples=sample_count,
        mdat_sha256=mdat_digest,
        sample_entry=entry or "",
        sync_samples=sync_samples,
        duration=duration,
        timescale=timescale,
        width=width >> 16,
        height=height >> 16,
        has_hvcc=has_hvcc,
        problems=problems,
    )


def read_samples(path: Path) -> tuple[dict, list[bytes]]:
    """Return (movie info, per-sample payload bytes) — used by tests."""
    path = Path(path)
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            stbl = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl"])
            if stbl is None:
                raise RemuxError("no sample table")
            stsz_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsz"])
            stco_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stco"])
            if stco_box is None:
                stco_box = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"co64"])
            sample_size, sample_count = struct.unpack_from(">II", mm, stsz_box[0] + 4)
            sizes = (
                [sample_size] * sample_count
                if sample_size
                else list(struct.unpack_from(f">{sample_count}I", mm, stsz_box[0] + 12))
            )
            if abs(stco_box[1] - stco_box[0]) == 12:
                offset = struct.unpack_from(">I", mm, stco_box[0] + 8)[0]
            else:
                offset = struct.unpack_from(">Q", mm, stco_box[0] + 8)[0]
            samples = []
            for sample in sizes:
                samples.append(bytes(mm[offset : offset + sample]))
                offset += sample
            mdhd = find_box(mm, [b"moov", b"trak", b"mdia", b"mdhd"])
            timescale, duration = struct.unpack_from(">II", mm, mdhd[0] + 12)
            stss = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stss"])
            sync = (
                list(
                    struct.unpack_from(
                        f">{struct.unpack_from('>I', mm, stss[0] + 4)[0]}I", mm, stss[0] + 8
                    )
                )
                if stss is not None
                else list(range(1, sample_count + 1))
            )
            stsd = find_box(mm, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"])
            entry = ""
            hvcc = b""
            for type_, payload_start, payload_end, _ in iter_boxes(mm, stsd[0] + 8, stsd[1]):
                entry = type_.decode("ascii", "replace")
                hvcc_box = find_sample_entry_config(mm, payload_start, payload_end)
                if hvcc_box is not None:
                    hvcc = bytes(mm[hvcc_box[0] : hvcc_box[1]])
                break
    return (
        {
            "samples": sample_count,
            "timescale": timescale,
            "duration": duration,
            "sync_samples": sync,
            "sample_entry": entry,
            "hvcc": hvcc,
        },
        samples,
    )


def split_length_prefixed(payload: bytes) -> list[bytes]:
    """Split a sample payload into its 4-byte-length-prefixed NAL units."""
    nals = []
    offset = 0
    while offset + 4 <= len(payload):
        length = struct.unpack_from(">I", payload, offset)[0]
        offset += 4
        nals.append(payload[offset : offset + length])
        offset += length
    return nals
