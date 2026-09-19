# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 8.287 mm |
| ATE 平均 | 7.415 mm |
| ATE 最小 | 1.652 mm |
| ATE 中位 | 7.263 mm |
| ATE P95 | 13.016 mm |
| ATE 最大 | 24.163 mm |
| 10 mm 内比例 | 80.321% |
| 姿态 RMSE | 3.090° |
| RPE 平移 RMSE | 8.983 mm |
| 终点漂移 | 7.710 mm |
| Sim(3)形状诊断 RMSE | 8.225 mm |
| Sim(3)最优尺度(gt/estimate) | 1.006312 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
