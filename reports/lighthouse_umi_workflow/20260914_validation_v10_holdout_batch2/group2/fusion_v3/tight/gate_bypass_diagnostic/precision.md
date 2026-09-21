# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.454 mm |
| ATE 平均 | 4.634 mm |
| ATE 最小 | 0.216 mm |
| ATE 中位 | 3.786 mm |
| ATE P95 | 11.052 mm |
| ATE 最大 | 16.364 mm |
| 10 mm 内比例 | 93.585% |
| 姿态 RMSE | 1.464° |
| RPE 平移 RMSE | 6.979 mm |
| 终点漂移 | 1.842 mm |
| Sim(3)形状诊断 RMSE | 5.328 mm |
| Sim(3)最优尺度(gt/estimate) | 1.006370 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
