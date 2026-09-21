# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.748 mm |
| ATE 平均 | 4.462 mm |
| ATE 最小 | 0.399 mm |
| ATE 中位 | 3.774 mm |
| ATE P95 | 11.185 mm |
| ATE 最大 | 18.788 mm |
| 10 mm 内比例 | 91.681% |
| 姿态 RMSE | 1.926° |
| RPE 平移 RMSE | 7.924 mm |
| 终点漂移 | 3.877 mm |
| Sim(3)形状诊断 RMSE | 5.596 mm |
| Sim(3)最优尺度(gt/estimate) | 1.008220 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
