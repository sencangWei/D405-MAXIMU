# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 9.105 mm |
| ATE 平均 | 7.564 mm |
| ATE 最小 | 1.176 mm |
| ATE 中位 | 5.721 mm |
| ATE P95 | 18.129 mm |
| ATE 最大 | 20.370 mm |
| 10 mm 内比例 | 74.928% |
| 姿态 RMSE | 2.829° |
| RPE 平移 RMSE | 6.678 mm |
| 终点漂移 | 12.331 mm |
| Sim(3)形状诊断 RMSE | 8.038 mm |
| Sim(3)最优尺度(gt/estimate) | 0.977363 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
