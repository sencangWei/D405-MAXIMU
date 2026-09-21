# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.662 mm |
| ATE 平均 | 6.577 mm |
| ATE 最小 | 0.101 mm |
| ATE 中位 | 5.301 mm |
| ATE P95 | 14.036 mm |
| ATE 最大 | 19.271 mm |
| 10 mm 内比例 | 77.567% |
| 姿态 RMSE | 2.477° |
| RPE 平移 RMSE | 5.883 mm |
| 终点漂移 | 9.436 mm |
| Sim(3)形状诊断 RMSE | 7.109 mm |
| Sim(3)最优尺度(gt/estimate) | 0.984761 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
