# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.812 mm |
| ATE 平均 | 7.132 mm |
| ATE 最小 | 0.805 mm |
| ATE 中位 | 7.741 mm |
| ATE P95 | 11.402 mm |
| ATE 最大 | 14.852 mm |
| 10 mm 内比例 | 80.207% |
| 姿态 RMSE | 2.620° |
| RPE 平移 RMSE | 9.346 mm |
| 终点漂移 | 6.443 mm |
| Sim(3)形状诊断 RMSE | 7.670 mm |
| Sim(3)最优尺度(gt/estimate) | 1.009321 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
