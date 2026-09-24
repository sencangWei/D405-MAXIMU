from types import SimpleNamespace

from scripts import lighthouse_umi_preflight as preflight


def test_tracker_check_creates_report_directory_before_copying_config(
    tmp_path, monkeypatch
):
    frozen = tmp_path / "frozen.json"
    frozen.write_text("frozen calibration\n", encoding="utf-8")
    monkeypatch.setattr(preflight, "FROZEN_LIGHTHOUSE_CONFIG", frozen)
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="offline"),
    )

    output = tmp_path / "new_session" / "preflight.json"
    result = preflight.run_tracker_check(output, duration_s=1)

    assert result["passed"] is False
    assert (output.parent / "preflight_libsurvive_runtime.json").read_text(
        encoding="utf-8"
    ) == "frozen calibration\n"
