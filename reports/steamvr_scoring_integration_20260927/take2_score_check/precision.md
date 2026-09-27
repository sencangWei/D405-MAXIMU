# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 1.563 mm |
| ATE 平均 | 1.418 mm |
| ATE 最小 | 0.132 mm |
| ATE 中位 | 1.257 mm |
| ATE P95 | 2.702 mm |
| ATE 最大 | 4.206 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.242° |
| RPE 平移 RMSE | 1.898 mm |
| 终点漂移 | 2.797 mm |
| Sim(3)形状诊断 RMSE | 1.520 mm |
| Sim(3)最优尺度(gt/estimate) | 0.994365 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
