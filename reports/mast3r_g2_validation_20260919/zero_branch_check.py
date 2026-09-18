#!/usr/bin/env python3
"""单变量实测: 把 onboard 分支**真正**压到 0, 质量门会不会放行?

§4.2 的结论依赖一个推断: `--docker2-local-weight 0` 单独用没用, 因为
`--adaptive-local-weight` 的 `np.clip(..., 0.02, 0.98)` 硬下界会把有效权重
顶回 0.02; 必须**两个开关一起关**才等价于 G1 的 "weight == 0"。

本脚本就测这一个配置, 复用 sigma_policy_sweep 的全组遍历与打分。
既是诊断也是取证 —— 结论若成立, REJECT 应从 9 降到 2(剩下的是
`batch5/group3` sparse/tight, 其双目 stereo_rmse 3.93/3.67mm ≥ 3.5mm,
即便 weight 归零也仍触发 `primary_shape_not_independently_supported`)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sigma_policy_sweep as S  # noqa: E402

S.SCRATCH = Path("/tmp/claude-1000/stereoab/zb")
S.CONFIGS = [
    ("B2_w0_no_adaptive",
     dict(docker2_local_weight="0", no_adaptive_local_weight="1")),
]

if __name__ == "__main__":
    S.main()
