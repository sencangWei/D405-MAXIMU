# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.417 mm |
| ATE 平均 | 5.979 mm |
| ATE 最小 | 0.936 mm |
| ATE 中位 | 6.266 mm |
| ATE P95 | 10.118 mm |
| ATE 最大 | 15.333 mm |
| 10 mm 内比例 | 94.483% |
| 姿态 RMSE | 1.605° |
| RPE 平移 RMSE | 3.626 mm |
| 终点漂移 | 12.401 mm |
| Sim(3)形状诊断 RMSE | 5.691 mm |
| Sim(3)最优尺度(gt/estimate) | 1.014486 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
