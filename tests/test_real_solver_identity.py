import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'.planning/metric_window_bundle_20260928/verify_real_solver_identity.py'
spec=importlib.util.spec_from_file_location('verify_real_solver_identity',PATH)
proof=importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_pair_requires_bit_equal_real_array_results():
    spec=importlib.util.spec_from_file_location('proof_scene',ROOT/'tests/test_stereo_window_bundle.py')
    scene=importlib.util.module_from_spec(spec);spec.loader.exec_module(scene)
    obs,valid,times,points,centers,rotations,gyro=scene.make_scene(noise_px=.03)
    result=proof.paired(obs,valid,times,scene.LEFT,scene.RIGHT,scene.BASELINE,
        points,centers,rotations,gyro,gyro_noise_density=.002,gyro_bias_sigma=.02)
    record=result['diagnostics']['same_input_paired_proof']
    assert record['centers_bit_equal'] is True
    assert record['landmarks_bit_equal'] is True
    assert record['gyro_bias_bit_equal'] is True
    assert record['original']==record['instrumented']
