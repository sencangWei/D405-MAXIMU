import stat

import pytest

from scripts import lighthouse_reference_check as reference_check


def test_capture_saves_optional_raw_record_and_refuses_overwrite(tmp_path, monkeypatch):
    stream = tmp_path / "fake_raw_stream.py"
    stream.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[sys.argv.index('--record') + 1]).write_text('raw events\\n')\n"
        "print('host_monotonic_ns,host_realtime_ns,device_time_s,name,serial,px_m,py_m,pz_m,qw,qx,qy,qz', flush=True)\n"
        "for stamp in (1000000000, 1001000000, 1002000000):\n"
        "    print(f'{stamp},{stamp},{stamp / 1e9},WM0,LHR-test,0,0,0,1,0,0,0', flush=True)\n"
    )
    stream.chmod(stream.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(reference_check, "STREAM", stream)
    raw = tmp_path / "raw" / "capture.rec"
    reference_check.capture(.002, tmp_path / "tracker.csv", 0., raw_record=raw)
    assert raw.read_text() == "raw events\n"
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        reference_check.capture(.002, tmp_path / "tracker2.csv", 0., raw_record=raw)
    assert not (tmp_path / "tracker2.csv").exists()
