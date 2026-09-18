#!/usr/bin/env python3
"""Export MASt3R pixel matches for VINS loop-closure verification.

The exporter consumes only UMI images and MASt3R's own confirmed retrieval log.
It does not read robot or Lighthouse trajectories.  VINS later associates these
2D matches with its metric landmarks and keeps responsibility for PnP, stereo
verification, IMU-consistent pose-graph optimization, and correction gating.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


RETRIEVAL_RE = re.compile(r"Database retrieval\s+(\d+)\s*:\s*\{([^}]*)\}")
CSV_FIELDS = (
    "current_timestamp_s",
    "previous_timestamp_s",
    "current_u",
    "current_v",
    "previous_u",
    "previous_v",
    "confidence",
)


@dataclass(frozen=True)
class FrameRow:
    input_index: int
    image: Path
    timestamp_s: float


@dataclass(frozen=True)
class RetrievalPair:
    current_keyframe: int
    previous_keyframe: int
    current_input_index: int
    previous_input_index: int

    @property
    def frame_gap(self) -> int:
        return self.current_input_index - self.previous_input_index


def load_frames(path: Path) -> dict[int, FrameRow]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not {"input_index", "image", "t_sec"}.issubset(rows[0]):
        raise ValueError(f"invalid MASt3R frames table: {path}")
    result = {
        int(row["input_index"]): FrameRow(
            input_index=int(row["input_index"]),
            image=path.parent / row["image"],
            timestamp_s=float(row["t_sec"]),
        )
        for row in rows
    }
    if sorted(result) != list(range(len(result))):
        raise ValueError("frames.csv input_index must be contiguous from zero")
    return result


def load_keyframe_input_indices(path: Path, source_fps: float) -> list[int]:
    trajectory = np.atleast_2d(np.loadtxt(path, dtype=np.float64))
    if trajectory.shape[1] != 8 or len(trajectory) < 2:
        raise ValueError(f"invalid MASt3R keyframe trajectory: {path}")
    indices = np.rint(trajectory[:, 0] * source_fps).astype(int)
    residual = np.abs(trajectory[:, 0] - indices / source_fps)
    if float(np.max(residual)) > 1e-5 or np.any(np.diff(indices) <= 0):
        raise ValueError("MASt3R keyframe timestamps do not map to source frames")
    return indices.tolist()


def load_saved_keyframe_input_indices(path: Path, source_fps: float) -> list[int]:
    """Map MASt3R's sparse internal keyframe IDs to source-frame indices.

    Saved keyframe filenames are relative timestamps.  Retrieval log IDs refer
    to their insertion order, which is chronological, not to rows in
    ``dataset_full.txt`` (that file contains nearly every input frame).
    """
    timestamps = []
    for image in path.glob("*.png"):
        try:
            timestamps.append(float(image.stem))
        except ValueError:
            continue
    if len(timestamps) < 2:
        raise ValueError(f"keyframe directory has fewer than two timestamped images: {path}")
    timestamps.sort()
    indices = np.rint(np.asarray(timestamps) * source_fps).astype(int)
    residual = np.abs(np.asarray(timestamps) - indices / source_fps)
    if float(np.max(residual)) > 1e-5 or np.any(np.diff(indices) <= 0):
        raise ValueError("saved keyframe timestamps do not map to unique source frames")
    return indices.tolist()


def parse_confirmed_retrieval_pairs(
    log_text: str,
    keyframe_input_indices: list[int],
    min_frame_gap: int,
    max_loop_pairs: int,
    min_previous_input_index: int = 0,
) -> list[RetrievalPair]:
    pairs: set[tuple[int, int]] = set()
    for match in RETRIEVAL_RE.finditer(log_text):
        current_keyframe = int(match.group(1))
        if current_keyframe >= len(keyframe_input_indices):
            raise ValueError("retrieval log references an unknown current keyframe")
        previous_keyframes = [
            int(value.strip())
            for value in match.group(2).split(",")
            if value.strip()
        ]
        for previous_keyframe in previous_keyframes:
            if previous_keyframe >= len(keyframe_input_indices):
                raise ValueError("retrieval log references an unknown previous keyframe")
            current_input = keyframe_input_indices[current_keyframe]
            previous_input = keyframe_input_indices[previous_keyframe]
            if (
                previous_input >= min_previous_input_index
                and current_input - previous_input >= min_frame_gap
            ):
                pairs.add((current_keyframe, previous_keyframe))
    ranked = sorted(
        (
            RetrievalPair(
                current_keyframe=current,
                previous_keyframe=previous,
                current_input_index=keyframe_input_indices[current],
                previous_input_index=keyframe_input_indices[previous],
            )
            for current, previous in pairs
        ),
        key=lambda pair: (-pair.frame_gap, -pair.current_input_index),
    )
    selected: list[RetrievalPair] = []
    for pair in ranked:
        if any(
            abs(pair.current_input_index - existing.current_input_index) <= 30
            for existing in selected
        ):
            continue
        selected.append(pair)
        if len(selected) == max_loop_pairs:
            break
    return selected


def original_pixel_transform(image: np.ndarray, resize_img) -> tuple[float, ...]:
    _, transform = resize_img(
        image.astype(np.float32) / 255.0,
        512,
        return_transformation=True,
    )
    return tuple(float(value) for value in transform)


def to_original_pixels(
    pixels: np.ndarray, transform: tuple[float, float, float, float]
) -> np.ndarray:
    scale_w, scale_h, crop_w, crop_h = transform
    result = pixels.astype(np.float64, copy=True)
    result[:, 0] = (result[:, 0] + crop_w) * scale_w
    result[:, 1] = (result[:, 1] + crop_h) * scale_h
    return result


def select_matches(
    idx_current_to_previous: np.ndarray,
    idx_previous_to_current: np.ndarray,
    valid_current: np.ndarray,
    valid_previous: np.ndarray,
    confidence_current: np.ndarray,
    height: int,
    width: int,
    min_confidence: float,
    cycle_tolerance_px: float,
    cell_size_px: int,
    max_matches: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    current_linear = np.arange(height * width, dtype=np.int64)
    previous_linear = idx_current_to_previous.reshape(-1).astype(np.int64)
    reverse = idx_previous_to_current.reshape(-1).astype(np.int64)
    current_valid = valid_current.reshape(-1).astype(bool)
    previous_valid = valid_previous.reshape(-1).astype(bool)
    confidence = confidence_current.reshape(-1).astype(np.float64)
    inside = (previous_linear >= 0) & (previous_linear < height * width)
    safe_previous = np.clip(previous_linear, 0, height * width - 1)
    reverse_current = reverse[safe_previous]
    current_xy = np.column_stack((current_linear % width, current_linear // width))
    reverse_xy = np.column_stack((reverse_current % width, reverse_current // width))
    cycle_error = np.linalg.norm(reverse_xy - current_xy, axis=1)
    mask = (
        current_valid
        & inside
        & previous_valid[safe_previous]
        & (confidence >= min_confidence)
        & (cycle_error <= cycle_tolerance_px)
    )
    border = 8
    previous_xy = np.column_stack((safe_previous % width, safe_previous // width))
    mask &= (
        (current_xy[:, 0] >= border)
        & (current_xy[:, 0] < width - border)
        & (current_xy[:, 1] >= border)
        & (current_xy[:, 1] < height - border)
        & (previous_xy[:, 0] >= border)
        & (previous_xy[:, 0] < width - border)
        & (previous_xy[:, 1] >= border)
        & (previous_xy[:, 1] < height - border)
    )
    indices = np.flatnonzero(mask)
    indices = indices[np.argsort(confidence[indices])[::-1]]
    occupied: set[tuple[int, int]] = set()
    selected = []
    for index in indices:
        cell = (
            int(current_xy[index, 0]) // cell_size_px,
            int(current_xy[index, 1]) // cell_size_px,
        )
        if cell in occupied:
            continue
        occupied.add(cell)
        selected.append(index)
        if len(selected) == max_matches:
            break
    selected_array = np.asarray(selected, dtype=int)
    return (
        current_xy[selected_array],
        previous_xy[selected_array],
        confidence[selected_array],
    )


def export(args: argparse.Namespace) -> dict:
    import sys

    toolchain = args.toolchain.resolve()
    config_path = args.config.resolve()
    frames_path = args.frames.resolve()
    keyframe_trajectory_path = (
        args.keyframe_trajectory.resolve() if args.keyframe_trajectory else None
    )
    keyframe_dir = args.keyframe_dir.resolve() if args.keyframe_dir else None
    mast3r_log_path = args.mast3r_log.resolve()
    output_path = args.output.resolve()
    sys.path.insert(0, str(toolchain))
    import torch
    import lietorch
    from mast3r_slam.config import config, load_config
    from mast3r_slam.frame import create_frame
    from mast3r_slam.mast3r_utils import (
        load_mast3r,
        mast3r_match_symmetric,
        resize_img,
    )

    os.chdir(toolchain)
    load_config(str(config_path))
    frames = load_frames(frames_path)
    if keyframe_dir is not None:
        keyframe_indices = load_saved_keyframe_input_indices(
            keyframe_dir, args.source_fps
        )
    else:
        keyframe_indices = load_keyframe_input_indices(
            keyframe_trajectory_path, args.source_fps
        )
    pairs = parse_confirmed_retrieval_pairs(
        mast3r_log_path.read_text(encoding="utf-8", errors="replace"),
        keyframe_indices,
        args.min_frame_gap,
        args.max_loop_pairs,
        args.min_previous_input_index,
    )
    if not pairs:
        raise ValueError("MASt3R log has no confirmed retrieval pair at the requested gap")

    device = args.device
    model = load_mast3r(device=device)
    model.eval()
    def frame_for(input_index: int):
        row = frames[input_index]
        bgr = cv2.imread(str(row.image), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(row.image)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        frame = create_frame(
            input_index,
            rgb.astype(np.float32) / 255.0,
            lietorch.Sim3.Identity(1, device=device),
            device=device,
        )
        frame.feat, frame.pos, _ = model._encode_image(
            frame.img, frame.img_true_shape
        )
        return frame, original_pixel_transform(rgb, resize_img)

    exported_rows = []
    edge_summaries = []
    for pair in pairs:
        for previous_offset in range(
            -args.previous_confirmation_radius,
            args.previous_confirmation_radius + 1,
        ):
            previous_index = pair.previous_input_index + previous_offset
            if previous_index not in frames:
                continue
            previous_frame, previous_transform = frame_for(previous_index)
            for offset in range(-args.confirmation_radius, args.confirmation_radius + 1):
                current_index = pair.current_input_index + offset
                if current_index not in frames or current_index <= previous_index:
                    continue
                current_frame, current_transform = frame_for(current_index)
            (
                idx_current_to_previous,
                idx_previous_to_current,
                valid_current,
                valid_previous,
                confidence_previous_self,
                _confidence_current_self,
                confidence_current_in_previous,
                _confidence_previous_pair,
            ) = mast3r_match_symmetric(
                model,
                previous_frame.feat,
                previous_frame.pos,
                current_frame.feat,
                current_frame.pos,
                [previous_frame.img_true_shape],
                [current_frame.img_true_shape],
            )
            matched_previous = idx_current_to_previous[0].long()
            batch = torch.zeros_like(matched_previous)
            confidence_current = torch.sqrt(
                confidence_previous_self[batch, matched_previous]
                * confidence_current_in_previous[0]
            )
            height, width = map(int, current_frame.img_shape[0].tolist())
            current_px, previous_px, confidence = select_matches(
                idx_current_to_previous[0].detach().cpu().numpy(),
                idx_previous_to_current[0].detach().cpu().numpy(),
                valid_current[0].detach().cpu().numpy(),
                valid_previous[0].detach().cpu().numpy(),
                confidence_current.detach().cpu().numpy(),
                height,
                width,
                args.min_confidence,
                args.cycle_tolerance_px,
                args.cell_size_px,
                args.max_matches_per_edge,
            )
            enough_matches = len(current_px) >= args.min_matches_per_edge
            if enough_matches:
                current_original = to_original_pixels(current_px, current_transform)
                previous_original = to_original_pixels(previous_px, previous_transform)
            del (
                current_frame,
                idx_current_to_previous,
                idx_previous_to_current,
                valid_current,
                valid_previous,
                confidence_previous_self,
                _confidence_current_self,
                confidence_current_in_previous,
                _confidence_previous_pair,
                matched_previous,
                batch,
                confidence_current,
            )
            torch.cuda.empty_cache()
            if not enough_matches:
                continue
                current_timestamp = frames[current_index].timestamp_s
                previous_timestamp = frames[previous_index].timestamp_s
                for current_point, previous_point, score in zip(
                    current_original, previous_original, confidence
                ):
                    exported_rows.append(
                        {
                            "current_timestamp_s": f"{current_timestamp:.9f}",
                            "previous_timestamp_s": f"{previous_timestamp:.9f}",
                            "current_u": f"{current_point[0]:.4f}",
                            "current_v": f"{current_point[1]:.4f}",
                            "previous_u": f"{previous_point[0]:.4f}",
                            "previous_v": f"{previous_point[1]:.4f}",
                            "confidence": f"{score:.6f}",
                        }
                    )
                edge_summaries.append(
                    {
                        "current_input_index": current_index,
                        "previous_input_index": previous_index,
                        "matches": len(current_px),
                    }
                )
            del previous_frame
            torch.cuda.empty_cache()

    if not exported_rows:
        raise ValueError("no learned loop edge passed the match-quality gate")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(exported_rows)
    return {
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "selected_base_pairs": [pair.__dict__ for pair in pairs],
        "exported_edges": len(edge_summaries),
        "exported_matches": len(exported_rows),
        "edges": edge_summaries,
        "output": str(output_path),
        "policy": "MASt3R confirmed retrieval and cycle-consistent dense matches; VINS performs metric PnP/stereo/IMU gating",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    keyframe_source = parser.add_mutually_exclusive_group(required=True)
    keyframe_source.add_argument("--keyframe-trajectory", type=Path)
    keyframe_source.add_argument(
        "--keyframe-dir",
        type=Path,
        help="MASt3R保存的稀疏关键帧目录；优先用于正确解释retrieval编号",
    )
    parser.add_argument("--mast3r-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-fps", type=float, default=30.0)
    parser.add_argument("--min-frame-gap", type=int, default=150)
    parser.add_argument("--min-previous-input-index", type=int, default=0)
    parser.add_argument("--max-loop-pairs", type=int, default=1)
    parser.add_argument("--confirmation-radius", type=int, default=6)
    parser.add_argument("--previous-confirmation-radius", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=1.5)
    parser.add_argument("--cycle-tolerance-px", type=float, default=2.0)
    parser.add_argument("--cell-size-px", type=int, default=8)
    parser.add_argument("--max-matches-per-edge", type=int, default=1500)
    parser.add_argument("--min-matches-per-edge", type=int, default=80)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    if (
        args.max_loop_pairs < 1
        or args.confirmation_radius < 0
        or args.previous_confirmation_radius < 0
        or args.min_previous_input_index < 0
    ):
        parser.error("loop pair count must be positive and radius non-negative")
    report = export(args)
    import json

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
