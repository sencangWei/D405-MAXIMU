import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from summarize_declared_loop_morning import summarize


def test_summary_requests_negative_evidence_and_names_failed_stage(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "result": "FAIL",
                "datasets": [
                    {
                        "id": "closed",
                        "result": "FAIL",
                        "runs": [
                            {
                                "repetition": 1,
                                "result": "FAIL",
                                "loop_stage": "NO_USABLE_PNP",
                                "automatic_loop_accepts": 0,
                                "endpoint_error_m": 0.03,
                                "pose_coverage": 0.99,
                                "failures": ["no automatic loop was accepted"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "endpoint_stability.json").write_text(
        json.dumps(
            {
                "stable_sub_centimeter_run_count": 0,
                "expected_loop_run_count": 1,
                "all_expected_loops_stable_sub_centimeter": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pnp_gate_analysis.json").write_text(
        json.dumps(
            {
                "result": "INSUFFICIENT_EVIDENCE",
                "threshold_freeze_allowed": False,
                "selected_threshold": None,
            }
        ),
        encoding="utf-8",
    )

    text = summarize(tmp_path)

    assert "NO_USABLE_PNP" in text
    assert "相似画面但不闭环负样本" in text
    assert "历史闭环失败项不重录" in text
    assert "同一封存数据修算法" in text
    assert "外部真值" in text
    assert "每种动作至少3条独立采集" in text
    assert "不能把首尾闭合误差冒充绝对ATE/RPE" in text
    assert "现有RGB＋双IR母版不含Depth" in text


def test_summary_reports_optional_postprocessor_failures(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps({"result": "FAIL", "datasets": []}), encoding="utf-8"
    )
    (tmp_path / "endpoint_stability.exit_code").write_text("2\n", encoding="utf-8")
    (tmp_path / "pnp_gate_analysis.exit_code").write_text("3\n", encoding="utf-8")
    (tmp_path / "plots.exit_code").write_text("4\n", encoding="utf-8")

    text = summarize(tmp_path)

    assert "稳定闭合窗口：**未生成（后处理退出码=2）**" in text
    assert "PnP空间门证据：**未生成（后处理退出码=3）**" in text
    assert "三视图：**失败（退出码=4）**" in text
    assert "实际完成0轮" in text
    assert "暂不补录数据" in text


def test_negative_dataset_is_identified_by_stage_not_name(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "result": "FAIL",
                "datasets": [
                    {
                        "id": "new_negative_scene",
                        "result": "FAIL",
                        "runs": [
                            {
                                "repetition": 1,
                                "result": "FAIL",
                                "loop_stage": "FALSE_LOOP_ACCEPTED",
                                "automatic_loop_accepts": 1,
                                "pose_coverage": 1.0,
                                "failures": ["false automatic loops accepted: 1"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    text = summarize(tmp_path)

    assert "false automatic loops accepted" in text
    assert "历史闭环失败项不重录" not in text


def test_interrupted_negative_dataset_uses_predeclared_label(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "result": "INFRASTRUCTURE_BLOCKED",
                "datasets": [
                    {
                        "id": "negative_without_stage",
                        "result": "FAIL",
                        "runs": [
                            {
                                "repetition": 1,
                                "result": "FAIL",
                                "failures": ["infrastructure stopped run"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "endpoint_stability.json").write_text(
        json.dumps(
            {
                "required_repetitions_per_dataset": 3,
                "expected_loop_run_count": 3,
                "stable_sub_centimeter_run_count": 0,
                "all_expected_loops_stable_sub_centimeter": False,
                "datasets": [
                    {"id": "positive", "expected_loop": True, "runs": []},
                    {
                        "id": "negative_without_stage",
                        "expected_loop": False,
                        "runs": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    text = summarize(tmp_path)

    assert "计划6轮，实际完成1轮" in text
    assert "历史闭环失败项不重录" not in text
    assert "不补录数据：基础设施未完成" in text
