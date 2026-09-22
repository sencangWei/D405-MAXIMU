import sys
from pathlib import Path

import torch
import pytest


TRAINING_REPO = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-training")
sys.path[:0] = [str(TRAINING_REPO), str(TRAINING_REPO / "dust3r")]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mast3r_d405_losses import correspondence_motion_errors


def test_correspondence_motion_error_cancels_common_scene_offset():
    gt1 = torch.tensor([[[[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]]])
    gt2 = gt1.clone()
    pred1 = gt1 + torch.tensor([0.2, -0.1, 0.3])
    pred2 = gt2 + torch.tensor([0.2, -0.1, 0.3])
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = correspondence_motion_errors(
        gt1,
        gt2,
        pred1,
        pred2,
        coordinates,
        coordinates,
        torch.tensor([[True, True]]),
    )

    assert torch.allclose(errors, torch.zeros_like(errors), atol=1e-7)


def test_correspondence_motion_error_exposes_relative_translation():
    gt1 = torch.tensor([[[[0.0, 0.0, 1.0]]]])
    gt2 = gt1.clone()
    pred1 = gt1.clone()
    pred2 = gt2 + torch.tensor([0.012, 0.0, 0.0])
    coordinates = torch.tensor([[[0, 0]]])

    errors = correspondence_motion_errors(
        gt1,
        gt2,
        pred1,
        pred2,
        coordinates,
        coordinates,
        torch.tensor([[True]]),
    )

    assert errors.item() == pytest.approx(0.012, abs=1e-7)


def test_correspondence_motion_error_ignores_invalid_matches():
    points = torch.zeros((1, 1, 2, 3))
    coordinates = torch.tensor([[[0, 0], [1, 0]]])

    errors = correspondence_motion_errors(
        points,
        points,
        points,
        points + 0.1,
        coordinates,
        coordinates,
        torch.tensor([[True, False]]),
    )

    assert errors.shape == (1,)
