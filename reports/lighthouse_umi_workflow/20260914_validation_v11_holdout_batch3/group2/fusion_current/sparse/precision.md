# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.704 mm |
| ATE 平均 | 4.421 mm |
| ATE 最小 | 0.358 mm |
| ATE 中位 | 4.482 mm |
| ATE P95 | 7.253 mm |
| ATE 最大 | 8.326 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 2.078° |
| RPE 平移 RMSE | 4.664 mm |
| 终点漂移 | 8.271 mm |
| Sim(3)形状诊断 RMSE | 4.669 mm |
| Sim(3)最优尺度(gt/estimate) | 1.003046 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
