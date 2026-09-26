# Findings

- Prior attribution to one 60ms gap is incomplete: filtered errors around11.49s already31.32mm with7.53ms optical age; local error persists after optical reacquisition.
- Same SE3, same876points: fourway mean5.0456/P9512.0899/max34.6340mm; no_gate mean6.6063/P9518.4986/max25.4640mm. No gate is not a solution.
- Motion deskew enabled baseline vs disabled: disabled board max31.8526mm, mean5.0528; rAt98l raw jump flags6->51. Reject disable-deskew.
- Frozen Tracker->camera lever arm35.3655mm, not old117mm TCP semantic.
- Source handles observations up to100ms late by clamping their timestamp to model.t; Raug is computed but not applied. Actual observed lateness is not yet measured; do not implement a speculative covariance weight.
- Source models scan timing with existing velocity. Need distinguish raw solver/filtered output and device time/record callback time.
- Timing instruments measured median lateness5.3-5.8ms onall4records and did not change raw/final trajectories. Short-delay measurement model worsened board max36.61mm; archive+rollback, not deployed.
- At peak measured orientation/lever contribution approximately1mm, origin residual35.55mm.35.54mm projects onto raw weak optical covariance direction; raw position principal std[1.57,2.31,8.50]mm. Supports optical conditioning/model bias, not proof of exactly which physical calibration defect.
