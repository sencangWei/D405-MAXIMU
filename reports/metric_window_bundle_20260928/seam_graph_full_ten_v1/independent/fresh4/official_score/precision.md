# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.014 mm |
| ATE 平均 | 5.663 mm |
| ATE 最小 | 0.958 mm |
| ATE 中位 | 5.982 mm |
| ATE P95 | 9.037 mm |
| ATE 最大 | 13.961 mm |
| 10 mm 内比例 | 97.285% |
| 姿态 RMSE | 1.510° |
| RPE 平移 RMSE | 3.436 mm |
| 终点漂移 | 12.505 mm |
| Sim(3)形状诊断 RMSE | 5.456 mm |
| Sim(3)最优尺度(gt/estimate) | 1.012330 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
