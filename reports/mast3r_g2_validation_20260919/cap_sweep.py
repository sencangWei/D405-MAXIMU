#!/usr/bin/env python3
"""尾段"能改多少"的那两个上限, 跨全组扫一遍 —— 关门用。

§10/§11 把 VINS 权重、质量门策略、尺度权重、cap 模式、姿态节点密度都扫过之后,
尾段还剩一组没动过的量: **修正幅度上限**
(`--joint-max-correction-mm` 25 / `--full-rate-max-correction-mm` 20)。
G2 把 cap *模式* 从 global 改成 per-node 是有影响的, 那 cap *大小* 呢?

这一步的目的不是"再抠出 0.1mm", 而是把"尾段还有没有没试过的杠杆"这个问题问完 ——
若三个配置也都落在噪声里, 就可以有底气地说尾段已收敛, 瓶颈确定在上游 VINS。

规矩不变: 前端/标定输入逐字节冻结, 全组跑, 只动尾段参数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sigma_policy_sweep as S  # noqa: E402

S.SCRATCH = Path("/tmp/claude-1000/stereoab/cap")
S.CONFIGS = [
    ("K_cap_joint40", dict(joint_max_correction_mm="40")),
    ("L_cap_joint15", dict(joint_max_correction_mm="15")),
    ("M_cap_fullrate10", dict(full_rate_max_correction_mm="10")),
]

if __name__ == "__main__":
    S.main()
