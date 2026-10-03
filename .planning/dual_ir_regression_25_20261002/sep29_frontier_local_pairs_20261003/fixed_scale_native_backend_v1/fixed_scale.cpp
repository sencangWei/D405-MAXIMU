#include <torch/extension.h>

#include <vector>

#define CHECK_CONTIGUOUS(x) TORCH_CHECK(x.is_contiguous(), #x " must be contiguous")
#define CHECK_CUDA_FLOAT32(x) TORCH_CHECK(x.is_cuda() && x.scalar_type() == torch::kFloat32, #x " must be CUDA float32")
#define CHECK_CUDA_LONG(x) TORCH_CHECK(x.is_cuda() && x.scalar_type() == torch::kInt64, #x " must be CUDA int64")
#define CHECK_CUDA_BOOL(x) TORCH_CHECK(x.is_cuda() && x.scalar_type() == torch::kBool, #x " must be CUDA bool")

std::vector<torch::Tensor> gauss_newton_calib_cuda(
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
  torch::Tensor metric_targets,
  torch::Tensor metric_valid,
  const float metric_position_sigma,
  const float metric_log_scale_sigma);

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
  torch::Tensor target_scales);

static void check_common(
  torch::Tensor Twc, torch::Tensor Xs, torch::Tensor Cs, torch::Tensor K,
  torch::Tensor ii, torch::Tensor jj, torch::Tensor idx_ii2jj,
  torch::Tensor valid_match, torch::Tensor Q) {
  CHECK_CUDA_FLOAT32(Twc);
  CHECK_CUDA_FLOAT32(Xs);
  CHECK_CUDA_FLOAT32(Cs);
  CHECK_CUDA_FLOAT32(K);
  CHECK_CUDA_LONG(ii);
  CHECK_CUDA_LONG(jj);
  CHECK_CUDA_LONG(idx_ii2jj);
  CHECK_CUDA_BOOL(valid_match);
  CHECK_CUDA_FLOAT32(Q);
  CHECK_CONTIGUOUS(Twc);
  CHECK_CONTIGUOUS(Xs);
  CHECK_CONTIGUOUS(Cs);
  CHECK_CONTIGUOUS(K);
  CHECK_CONTIGUOUS(ii);
  CHECK_CONTIGUOUS(jj);
  CHECK_CONTIGUOUS(idx_ii2jj);
  CHECK_CONTIGUOUS(valid_match);
  CHECK_CONTIGUOUS(Q);
  TORCH_CHECK(Twc.dim() == 2 && Twc.size(1) == 8, "Twc must be [N,8]");
  TORCH_CHECK(Xs.dim() == 3 && Xs.size(0) == Twc.size(0) && Xs.size(2) == 3, "Xs must be [N,P,3]");
  TORCH_CHECK(Cs.dim() == 3 && Cs.size(0) == Xs.size(0) && Cs.size(1) == Xs.size(1) && Cs.size(2) == 1, "Cs must be [N,P,1]");
  TORCH_CHECK(K.dim() == 2 && K.size(0) == 3 && K.size(1) == 3, "K must be [3,3]");
  TORCH_CHECK(valid_match.sizes() == Q.sizes(), "valid_match/Q shape mismatch");
  TORCH_CHECK(idx_ii2jj.dim() == 2 && idx_ii2jj.size(0) == Q.size(0) && idx_ii2jj.size(1) == Q.size(1), "idx_ii2jj shape mismatch");
  TORCH_CHECK(ii.dim() == 1 && jj.dim() == 1 && ii.size(0) == jj.size(0) && ii.size(0) == Q.size(0), "edge arrays shape mismatch");
  TORCH_CHECK(bool(torch::isfinite(Twc).all().item<bool>()), "Twc must be finite");
  TORCH_CHECK(bool(torch::isfinite(Xs).all().item<bool>()), "Xs must be finite");
  TORCH_CHECK(bool(torch::isfinite(Cs).all().item<bool>()), "Cs must be finite");
  TORCH_CHECK(bool(torch::isfinite(K).all().item<bool>()), "K must be finite");
  TORCH_CHECK(bool(torch::isfinite(Q).all().item<bool>()), "Q must be finite");
  TORCH_CHECK(bool((Twc.index({torch::indexing::Slice(), 7}) > 0).all().item<bool>()), "Twc scales must be positive");
  const auto q = Twc.index({torch::indexing::Slice(), torch::indexing::Slice(3, 7)});
  const auto qnorm = torch::sqrt(torch::sum(q * q, 1));
  TORCH_CHECK(bool((torch::abs(qnorm - 1.0) < 1e-3).all().item<bool>()), "Twc quaternions must be unit length");
}

std::vector<torch::Tensor> gauss_newton_calib(
  torch::Tensor Twc, torch::Tensor Xs, torch::Tensor Cs, torch::Tensor K,
  torch::Tensor ii, torch::Tensor jj, torch::Tensor idx_ii2jj,
  torch::Tensor valid_match, torch::Tensor Q,
  const int height, const int width, const int pixel_border, const float z_eps,
  const float sigma_pixel, const float sigma_depth, const float C_thresh,
  const float Q_thresh, const int max_iter, const float delta_thresh) {
  check_common(Twc, Xs, Cs, K, ii, jj, idx_ii2jj, valid_match, Q);
  return gauss_newton_calib_cuda(Twc, Xs, Cs, K, ii, jj, idx_ii2jj, valid_match, Q,
      height, width, pixel_border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh,
      max_iter, delta_thresh, torch::empty({0}, Twc.options()),
      torch::empty({0}, Twc.options().dtype(torch::kBool)), 0.0f, 0.0f);
}

std::vector<torch::Tensor> gauss_newton_calib_fixed_scales(
  torch::Tensor Twc, torch::Tensor Xs, torch::Tensor Cs, torch::Tensor K,
  torch::Tensor ii, torch::Tensor jj, torch::Tensor idx_ii2jj,
  torch::Tensor valid_match, torch::Tensor Q,
  const int height, const int width, const int pixel_border, const float z_eps,
  const float sigma_pixel, const float sigma_depth, const float C_thresh,
  const float Q_thresh, const int max_iter, const float delta_thresh,
  torch::Tensor target_scales) {
  check_common(Twc, Xs, Cs, K, ii, jj, idx_ii2jj, valid_match, Q);
  CHECK_CUDA_FLOAT32(target_scales);
  CHECK_CONTIGUOUS(target_scales);
  TORCH_CHECK(target_scales.dim() == 1 && target_scales.size(0) == Twc.size(0), "target_scales must be [N]");
  TORCH_CHECK(bool(torch::isfinite(target_scales).all().item<bool>()), "target_scales must be finite");
  TORCH_CHECK(bool((target_scales > 0).all().item<bool>()), "target_scales must be positive");
  return gauss_newton_calib_fixed_scales_cuda(Twc, Xs, Cs, K, ii, jj, idx_ii2jj, valid_match, Q,
      height, width, pixel_border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh,
      max_iter, delta_thresh, target_scales);
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.def("gauss_newton_calib", &gauss_newton_calib, "isolated original calibrated GN control");
  m.def("gauss_newton_calib_fixed_scales", &gauss_newton_calib_fixed_scales, "isolated calibrated GN with fixed Sim3 scales");
}
