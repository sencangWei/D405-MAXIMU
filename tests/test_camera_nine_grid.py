from types import SimpleNamespace

import numpy as np

from scripts.collect_calib_data import (
    NINE_GRID_LABELS,
    StageQuality,
    aprilgrid_image_center,
    nine_grid_cell,
)


GRID = {"tagCols": 6, "tagRows": 6, "tagSize": 0.0352, "tagSpacing": 0.3}


def detections_centered_at(center_x: float, center_y: float):
    pitch = 1.3
    extent = 6.0 + 5.0 * 0.3
    scale = 40.0
    origin_x = center_x - extent * scale / 2.0
    origin_y = center_y - extent * scale / 2.0
    detections = []
    for tag_id in range(36):
        row, column = divmod(tag_id, 6)
        x0 = origin_x + column * pitch * scale
        y0 = origin_y + row * pitch * scale
        corners = np.asarray([
            [x0, y0], [x0 + scale, y0],
            [x0 + scale, y0 + scale], [x0, y0 + scale],
        ])
        detections.append(SimpleNamespace(tag_id=tag_id, corners=corners))
    return detections


def test_full_aprilgrid_center_maps_to_all_nine_preview_cells():
    expected_centers = [
        (150, 150), (450, 150), (750, 150),
        (150, 450), (450, 450), (750, 450),
        (150, 750), (450, 750), (750, 750),
    ]
    for expected_cell, expected_center in enumerate(expected_centers):
        center = aprilgrid_image_center(detections_centered_at(*expected_center), GRID)
        cell, safely_inside = nine_grid_cell(center, 900, 900)
        assert cell == expected_cell, NINE_GRID_LABELS[expected_cell]
        assert safely_inside is True


def test_nine_grid_stage_requires_stable_hits_inside_target_cell():
    class Detector:
        detections = detections_centered_at(150, 750)

        def detect(self, _image):
            return self.detections

    stage = StageQuality(grid_cfg=GRID)
    image = np.zeros((900, 900), dtype=np.uint8)
    requirement = {"tags_min": 4, "grid_cell": 6, "grid_hits_min": 15}
    for _ in range(14):
        stage.feed_image(image, Detector())
    assert stage.check(requirement)[0] is False
    stage.feed_image(image, Detector())
    assert stage.check(requirement)[0] is True

    Detector.detections = detections_centered_at(300, 750)
    stage.reset()
    for _ in range(30):
        stage.feed_image(image, Detector())
    assert stage.last_grid_safely_inside is False
    assert stage.check(requirement)[0] is False
