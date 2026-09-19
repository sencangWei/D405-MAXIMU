#!/usr/bin/env python3
"""只重跑 Q(`--auto-docker2-scale-weight`)一组, 输出到独立 scratch。

两个目的:
  1. 修掉 sigma_policy_sweep/scale_sweep 的输出路径 bug 后, 把 Q 的 18 份产物
     完整留下来(原来同组 sparse/tight 共用目录, 跑 tight 会删掉 sparse 的);
  2. 顺便当一次**确定性校验** —— Q 的分数必须与原 scale.json 逐项一致,
     否则说明"分数不受该 bug 影响"这个判断站不住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scale_sweep as S  # noqa: E402

S.SCRATCH = Path("/tmp/claude-1000/stereoab/scale_q")
S.CONFIGS = [("Q_auto_scale0.475", dict(auto_docker2_scale_weight="1"))]

if __name__ == "__main__":
    S.main()
