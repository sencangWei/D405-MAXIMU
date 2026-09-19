# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 8.358 mm |
| ATE 平均 | 7.576 mm |
| ATE 最小 | 1.638 mm |
| ATE 中位 | 6.815 mm |
| ATE P95 | 13.305 mm |
| ATE 最大 | 19.081 mm |
| 10 mm 内比例 | 69.363% |
| 姿态 RMSE | 3.202° |
| RPE 平移 RMSE | 4.697 mm |
| 终点漂移 | 13.928 mm |
| Sim(3)形状诊断 RMSE | 6.428 mm |
| Sim(3)最优尺度(gt/estimate) | 0.976937 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
