# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.770 mm |
| ATE 平均 | 5.043 mm |
| ATE 最小 | 1.173 mm |
| ATE 中位 | 4.442 mm |
| ATE P95 | 11.198 mm |
| ATE 最大 | 20.826 mm |
| 10 mm 内比例 | 92.140% |
| 姿态 RMSE | 2.193° |
| RPE 平移 RMSE | 5.614 mm |
| 终点漂移 | 3.772 mm |
| Sim(3)形状诊断 RMSE | 5.733 mm |
| Sim(3)最优尺度(gt/estimate) | 1.003545 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
