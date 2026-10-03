#include <torch/extension.h>
#include "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/mast3r_slam/backend/src/gn_kernels.cu"

std::vector<torch::Tensor> inspect_calibrated_cuda(
    torch::Tensor poses, torch::Tensor points, torch::Tensor confidence, torch::Tensor K,
    torch::Tensor ii, torch::Tensor jj, torch::Tensor idx, torch::Tensor valid, torch::Tensor Q,
    int height, int width, int border, float z_eps, float sigma_pixel, float sigma_depth,
    float C_thresh, float Q_thresh) {
  auto H = torch::zeros({4, ii.size(0), 7, 7}, poses.options());
  auto g = torch::zeros({2, ii.size(0), 7}, poses.options());
  calib_proj_kernel<<<ii.size(0), THREADS>>>(
      poses.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      points.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      confidence.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      K.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      ii.packed_accessor32<long,1,torch::RestrictPtrTraits>(),
      jj.packed_accessor32<long,1,torch::RestrictPtrTraits>(),
      idx.packed_accessor32<long,2,torch::RestrictPtrTraits>(),
      valid.packed_accessor32<bool,3,torch::RestrictPtrTraits>(),
      Q.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      H.packed_accessor32<float,4,torch::RestrictPtrTraits>(),
      g.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      height, width, border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh);
  return {H, g};
}

torch::Tensor inspect_retract_cuda(torch::Tensor poses, torch::Tensor dx) {
  auto output = poses.clone();
  pose_retr_kernel<<<1, THREADS>>>(
      output.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      dx.packed_accessor32<float,2,torch::RestrictPtrTraits>(), 0);
  return output;
}
