# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.432 mm |
| ATE 平均 | 4.022 mm |
| ATE 最小 | 0.558 mm |
| ATE 中位 | 3.786 mm |
| ATE P95 | 7.015 mm |
| ATE 最大 | 12.391 mm |
| 10 mm 内比例 | 98.336% |
| 姿态 RMSE | 1.709° |
| RPE 平移 RMSE | 5.209 mm |
| 终点漂移 | 6.032 mm |
| Sim(3)形状诊断 RMSE | 4.360 mm |
| Sim(3)最优尺度(gt/estimate) | 0.996325 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
