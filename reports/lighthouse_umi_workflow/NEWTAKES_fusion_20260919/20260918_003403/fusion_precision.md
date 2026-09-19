# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 11.608 mm |
| ATE 平均 | 10.499 mm |
| ATE 最小 | 2.163 mm |
| ATE 中位 | 11.312 mm |
| ATE P95 | 18.864 mm |
| ATE 最大 | 25.989 mm |
| 10 mm 内比例 | 37.579% |
| 姿态 RMSE | 4.699° |
| RPE 平移 RMSE | 7.732 mm |
| 终点漂移 | 6.436 mm |
| Sim(3)形状诊断 RMSE | 7.659 mm |
| Sim(3)最优尺度(gt/estimate) | 0.960816 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
