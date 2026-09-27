# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.011 mm |
| ATE 平均 | 3.529 mm |
| ATE 最小 | 0.164 mm |
| ATE 中位 | 2.810 mm |
| ATE P95 | 7.727 mm |
| ATE 最大 | 11.323 mm |
| 10 mm 内比例 | 97.900% |
| 姿态 RMSE | 1.461° |
| RPE 平移 RMSE | 3.749 mm |
| 终点漂移 | 9.151 mm |
| Sim(3)形状诊断 RMSE | 3.930 mm |
| Sim(3)最优尺度(gt/estimate) | 1.003768 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
