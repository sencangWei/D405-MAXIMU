# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.938 mm |
| ATE 平均 | 3.071 mm |
| ATE 最小 | 0.196 mm |
| ATE 中位 | 2.412 mm |
| ATE P95 | 8.228 mm |
| ATE 最大 | 11.312 mm |
| 10 mm 内比例 | 99.197% |
| 姿态 RMSE | 2.353° |
| RPE 平移 RMSE | 5.423 mm |
| 终点漂移 | 2.588 mm |
| Sim(3)形状诊断 RMSE | 3.297 mm |
| Sim(3)最优尺度(gt/estimate) | 1.011651 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
