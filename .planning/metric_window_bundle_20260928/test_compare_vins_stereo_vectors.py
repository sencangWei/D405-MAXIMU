import unittest

from compare_vins_stereo_vectors import compare


class CompareVinsStereoVectorsTest(unittest.TestCase):
    def test_same_camera_displacement_has_zero_residual(self):
        rows = [
            dict(input_index="0", valid="1", x="0", y="0", z="0",
                 qx="0", qy="0", qz="0", qw="1"),
            dict(input_index="1", valid="1", x="0.02", y="0", z="0",
                 qx="0", qy="0", qz="0", qw="1"),
        ]
        edges = [dict(accepted=True, first_index=0, second_index=1,
                      metric_displacement_camera_i_m=[0.02, 0, 0])]
        result = compare(rows, edges)
        self.assertEqual(result["edges"], 1)
        self.assertAlmostEqual(result["vector_residual_median_mm"], 0)
        self.assertAlmostEqual(result["direction_error_median_deg"], 0)

    def test_rejects_edges_below_motion_floor(self):
        rows = [
            dict(input_index="0", valid="1", x="0", y="0", z="0",
                 qx="0", qy="0", qz="0", qw="1"),
            dict(input_index="1", valid="1", x="0.005", y="0", z="0",
                 qx="0", qy="0", qz="0", qw="1"),
        ]
        edges = [dict(accepted=True, first_index=0, second_index=1,
                      metric_displacement_camera_i_m=[0.005, 0, 0])]
        with self.assertRaisesRegex(ValueError, "no accepted metric edge"):
            compare(rows, edges)


if __name__ == "__main__":
    unittest.main()
