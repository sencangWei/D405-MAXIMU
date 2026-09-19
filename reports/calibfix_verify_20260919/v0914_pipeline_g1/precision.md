# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.756 mm |
| ATE 平均 | 4.416 mm |
| ATE 最小 | 0.050 mm |
| ATE 中位 | 4.763 mm |
| ATE P95 | 6.773 mm |
| ATE 最大 | 13.061 mm |
| 10 mm 内比例 | 99.713% |
| 姿态 RMSE | 1.981° |
| RPE 平移 RMSE | 4.012 mm |
| 终点漂移 | 11.189 mm |
| Sim(3)形状诊断 RMSE | 4.734 mm |
| Sim(3)最优尺度(gt/estimate) | 0.997329 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
