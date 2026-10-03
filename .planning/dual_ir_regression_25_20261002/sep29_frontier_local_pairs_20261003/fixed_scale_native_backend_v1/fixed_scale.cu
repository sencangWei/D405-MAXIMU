#include <torch/extension.h>

#include <algorithm>
#include <vector>

#include "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/mast3r_slam/backend/src/gn_kernels.cu"

std::vector<torch::Tensor> gauss_newton_calib_fixed_scales_cuda(
  torch::Tensor Twc, torch::Tensor Xs, torch::Tensor Cs,
  torch::Tensor K,
  torch::Tensor ii, torch::Tensor jj,
  torch::Tensor idx_ii2jj, torch::Tensor valid_match,
  torch::Tensor Q,
  const int height, const int width,
  const int pixel_border,
  const float z_eps,
  const float sigma_pixel, const float sigma_depth,
  const float C_thresh,
  const float Q_thresh,
  const int max_iter,
  const float delta_thresh,
  torch::Tensor target_scales)
{
  using torch::indexing::Slice;

  auto opts = Twc.options();
  const int num_edges = ii.size(0);
  const int num_poses = Xs.size(0);
  const int n = Xs.size(1);
  const int num_fix = 1;
  const int source_pose_dim = 7;
  const int solve_pose_dim = 6;

  Twc.index_put_({Slice(), 7}, target_scales);

  torch::Tensor unique_kf_idx = get_unique_kf_idx(ii, jj);
  std::vector<torch::Tensor> inds = create_inds(unique_kf_idx, 0, ii, jj);
  torch::Tensor ii_edge = inds[0];
  torch::Tensor jj_edge = inds[1];
  std::vector<torch::Tensor> inds_opt = create_inds(unique_kf_idx, num_fix, ii, jj);
  torch::Tensor ii_opt = inds_opt[0];
  torch::Tensor jj_opt = inds_opt[1];

  torch::Tensor Hs = torch::zeros({4, num_edges, source_pose_dim, source_pose_dim}, opts);
  torch::Tensor gs = torch::zeros({2, num_edges, source_pose_dim}, opts);
  torch::Tensor dx7 = torch::zeros({std::max(num_poses - num_fix, 0), source_pose_dim}, opts);
  torch::Tensor delta_norm = torch::zeros({}, opts);

  for (int itr=0; itr<max_iter; itr++) {
    Twc.index_put_({Slice(), 7}, target_scales);
    Hs.zero_();
    gs.zero_();

    calib_proj_kernel<<<num_edges, THREADS>>>(
      Twc.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      Xs.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      Cs.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      K.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      ii_edge.packed_accessor32<long,1,torch::RestrictPtrTraits>(),
      jj_edge.packed_accessor32<long,1,torch::RestrictPtrTraits>(),
      idx_ii2jj.packed_accessor32<long,2,torch::RestrictPtrTraits>(),
      valid_match.packed_accessor32<bool,3,torch::RestrictPtrTraits>(),
      Q.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      Hs.packed_accessor32<float,4,torch::RestrictPtrTraits>(),
      gs.packed_accessor32<float,3,torch::RestrictPtrTraits>(),
      height, width, pixel_border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh
    );

    SparseBlock A(num_poses - num_fix, solve_pose_dim);
    const float visual_normalizer = 1.0f;
    torch::Tensor Hs6 = Hs.index({Slice(), Slice(), Slice(0, 6), Slice(0, 6)}).contiguous();
    torch::Tensor gs6 = gs.index({Slice(), Slice(), Slice(0, 6)}).contiguous();
    A.update_lhs((Hs6 / visual_normalizer).reshape({-1, solve_pose_dim, solve_pose_dim}),
        torch::cat({ii_opt, ii_opt, jj_opt, jj_opt}),
        torch::cat({ii_opt, jj_opt, ii_opt, jj_opt}));
    A.update_rhs((gs6 / visual_normalizer).reshape({-1, solve_pose_dim}),
        torch::cat({ii_opt, jj_opt}));

    torch::Tensor dx6 = -A.solve();
    dx7.zero_();
    dx7.index_put_({Slice(), Slice(0, 6)}, dx6);

    pose_retr_kernel<<<1, THREADS>>>(
      Twc.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      dx7.packed_accessor32<float,2,torch::RestrictPtrTraits>(),
      num_fix);
    Twc.index_put_({Slice(), 7}, target_scales);

    delta_norm = at::norm(dx6);
    if (delta_norm.item<float>() < delta_thresh) {
      break;
    }
  }

  return {dx7, delta_norm};
}
