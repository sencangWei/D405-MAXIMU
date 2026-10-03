#include <torch/extension.h>

std::vector<torch::Tensor> inspect_calibrated_cuda(
    torch::Tensor, torch::Tensor, torch::Tensor, torch::Tensor,
    torch::Tensor, torch::Tensor, torch::Tensor, torch::Tensor, torch::Tensor,
    int, int, int, float, float, float, float, float);
torch::Tensor inspect_retract_cuda(torch::Tensor, torch::Tensor);

static void check(torch::Tensor value, torch::ScalarType dtype) {
  TORCH_CHECK(value.is_cuda() && value.scalar_type() == dtype && value.is_contiguous(),
              "expected contiguous CUDA tensor of declared dtype");
}

std::vector<torch::Tensor> inspect_calibrated(
    torch::Tensor poses, torch::Tensor points, torch::Tensor confidence, torch::Tensor K,
    torch::Tensor ii, torch::Tensor jj, torch::Tensor idx, torch::Tensor valid, torch::Tensor Q,
    int height, int width, int border, float z_eps, float sigma_pixel, float sigma_depth,
    float C_thresh, float Q_thresh) {
  for (auto t : {poses, points, confidence, K, Q}) check(t, torch::kFloat32);
  for (auto t : {ii, jj, idx}) check(t, torch::kInt64);
  check(valid, torch::kBool);
  TORCH_CHECK(poses.dim()==2 && poses.size(1)==8 && points.dim()==3 && points.size(2)==3,
              "invalid pose/point shape");
  TORCH_CHECK(points.size(0)==poses.size(0) && points.size(1)==height*width,
              "point dimensions must match poses and image");
  TORCH_CHECK(confidence.dim()==3 && confidence.size(0)==points.size(0)
      && confidence.size(1)==points.size(1) && confidence.size(2)==1, "invalid confidence shape");
  TORCH_CHECK(K.dim()==2 && K.size(0)==3 && K.size(1)==3, "invalid K shape");
  TORCH_CHECK(ii.dim()==1 && jj.dim()==1 && ii.numel()>0 && ii.sizes()==jj.sizes(), "invalid edge arrays");
  TORCH_CHECK(idx.dim()==2 && idx.size(0)==ii.numel() && idx.size(1)==points.size(1), "invalid index shape");
  TORCH_CHECK(valid.dim()==3 && valid.size(0)==ii.numel() && valid.size(1)==points.size(1)
      && valid.size(2)==1 && valid.sizes()==Q.sizes(), "invalid validity/Q shape");
  TORCH_CHECK(ii.min().item<long>()>=0 && jj.min().item<long>()>=0
      && ii.max().item<long>()<poses.size(0) && jj.max().item<long>()<poses.size(0), "edge out of bounds");
  auto matched = idx.masked_select(valid.squeeze(-1));
  TORCH_CHECK(matched.numel()==0 || (matched.min().item<long>()>=0
      && matched.max().item<long>()<points.size(1)), "matched point out of bounds");
  TORCH_CHECK(sigma_pixel>0 && sigma_depth>0, "sigmas must be positive");
  return inspect_calibrated_cuda(poses, points, confidence, K, ii, jj, idx, valid, Q,
      height, width, border, z_eps, sigma_pixel, sigma_depth, C_thresh, Q_thresh);
}

torch::Tensor inspect_retract(torch::Tensor poses, torch::Tensor dx) {
  check(poses, torch::kFloat32); check(dx, torch::kFloat32);
  TORCH_CHECK(poses.dim()==2 && poses.size(1)==8 && dx.dim()==2
      && dx.size(0)==poses.size(0) && dx.size(1)==7, "invalid retraction shapes");
  return inspect_retract_cuda(poses, dx);
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.def("inspect_calibrated", &inspect_calibrated, "original native H/g, no solve or mutation");
  m.def("inspect_retract", &inspect_retract, "original native retraction on a clone");
}
