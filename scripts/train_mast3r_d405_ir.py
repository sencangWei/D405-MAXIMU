#!/usr/bin/env python3
"""Launch reproducible decoder/head fine-tuning of MASt3R on D405 IR stereo."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIN_REPO = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-training")
DEFAULT_CHECKPOINT = Path(
    "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/checkpoints/"
    "MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_encoder_model(model: str) -> str:
    if "freeze=" in model:
        return model
    marker = "AsymmetricMASt3R("
    if marker not in model:
        raise ValueError("checkpoint is not an AsymmetricMASt3R model")
    return model.replace(marker, marker + "freeze='encoder', ", 1)


def configure_trainable_parameters(model, train_scope: str):
    if train_scope == "decoder-heads":
        return model
    if train_scope not in {
        "heads-only",
        "descriptors-only",
        "geometry-only",
        "descriptor-confidence-only",
    }:
        raise ValueError(f"unsupported train scope: {train_scope}")
    for parameter in model.parameters():
        parameter.requires_grad = False
    gradient_handles = []
    for head in (model.downstream_head1, model.downstream_head2):
        if train_scope == "descriptors-only":
            trainable_module = head.head_local_features
        elif train_scope == "geometry-only":
            trainable_module = head.dpt
        elif train_scope == "descriptor-confidence-only":
            if not head.two_confs:
                raise ValueError(
                    "descriptor-confidence-only requires an independent descriptor confidence channel"
                )
            output = head.head_local_features.fc2
            confidence_rows = head.patch_size**2
            if output.out_features != (head.local_feat_dim + 1) * confidence_rows:
                raise ValueError("unexpected descriptor head output layout")
            for parameter in (output.weight, output.bias):
                parameter.requires_grad = True
                def keep_confidence_rows(gradient, rows=confidence_rows):
                    masked = gradient.clone()
                    masked[:-rows].zero_()
                    return masked

                gradient_handles.append(parameter.register_hook(keep_confidence_rows))
            continue
        else:
            trainable_module = head
        for parameter in trainable_module.parameters():
            parameter.requires_grad = True
    model._d405_gradient_mask_handles = gradient_handles
    return model


def training_weight_decay(train_scope: str) -> float:
    """Avoid AdamW updates to masked descriptor rows."""
    return 0.0 if train_scope == "descriptor-confidence-only" else 0.05


def training_dataset_expression(
    dataset_class: str,
    manifest: Path,
    seed: int,
    high_motion_repeat: int,
    low_observability_repeat: int = 1,
    low_observability_loss_weight: float = 1.0,
    low_observability_max_tracked_points: int = 160,
    low_observability_min_angular_speed_deg_s: float = 8.0,
) -> str:
    if dataset_class == "D405IRRotationMatches":
        return (
            f"{dataset_class}(manifest={str(manifest)!r}, split='train', "
            "resolution=[(512,384),(512,336),(512,288),(512,256)], "
            f"n_corres=512, nneg=0.5, aug_crop=False, seed={seed})"
        )
    low_observability = ""
    if dataset_class == "D405IRTemporal":
        low_observability = (
            f", low_observability_repeat={low_observability_repeat}, "
            "low_observability_loss_weight="
            f"{low_observability_loss_weight:g}, "
            "low_observability_max_tracked_points="
            f"{low_observability_max_tracked_points}, "
            "low_observability_min_angular_speed_deg_s="
            f"{low_observability_min_angular_speed_deg_s:g}"
        )
    return (
        f"{dataset_class}(manifest={str(manifest)!r}, split='train', "
        "resolution=[(512,384),(512,336),(512,288),(512,256)], "
        f"n_corres=4096, nneg=0.5, aug_crop='auto', seed={seed}, "
        f"high_motion_repeat={high_motion_repeat}{low_observability})"
    )


def training_schedule_args(epochs: int, evaluation_only: bool) -> list[str]:
    if evaluation_only:
        return ["--epochs", "1"]
    return ["--epochs", str(epochs)]


def training_criterion_expression(
    mode: str,
    geometry_loss_weight: float,
    relative_motion_weight: float | None = None,
    include_matching: bool = True,
) -> str:
    matching = (
        "0.075*ConfMatchingLoss(MatchingLoss(InfoNCE(mode='proper', "
        "temperature=0.05), negatives_padding=0, blocksize=4096), "
        "alpha=10.0, confmode='mean')"
    )
    base = "ConfLoss(Regr3D(L21, norm_mode='?avg_dis'), alpha=0.2)"
    if include_matching:
        base = f"{base} + {matching}"
    if mode == "flow-matching":
        return matching
    if mode == "legacy":
        return base
    if mode == "relative-motion":
        if geometry_loss_weight <= 0:
            raise ValueError("relative motion weight must be positive")
        return f"{base} + {geometry_loss_weight:g}*D405RelativeMotionLoss()"
    if mode == "metric-correspondence":
        if geometry_loss_weight <= 0:
            raise ValueError("metric correspondence weight must be positive")
        return (
            f"{base} + {geometry_loss_weight:g}*"
            "D405MetricCorrespondenceLoss()"
        )
    if mode == "metric-relative":
        if geometry_loss_weight <= 0:
            raise ValueError("metric correspondence weight must be positive")
        if relative_motion_weight is None or relative_motion_weight <= 0:
            raise ValueError("relative motion weight must be positive")
        return (
            f"{base} + {geometry_loss_weight:g}*"
            "D405MetricCorrespondenceLoss() + "
            f"{relative_motion_weight:g}*D405RelativeMotionLoss()"
        )
    if mode == "window-scale":
        if geometry_loss_weight <= 0:
            raise ValueError("window scale weight must be positive")
        return f"{base} + {geometry_loss_weight:g}*D405WindowScaleLoss()"
    if mode == "window-scale-relative":
        if geometry_loss_weight <= 0:
            raise ValueError("window scale weight must be positive")
        if relative_motion_weight is None or relative_motion_weight <= 0:
            raise ValueError("relative motion weight must be positive")
        return (
            f"{base} + {geometry_loss_weight:g}*D405WindowScaleLoss() + "
            f"{relative_motion_weight:g}*D405RelativeMotionLoss()"
        )
    raise ValueError(f"unsupported training criterion: {mode}")


def validation_criterion_expression(mode: str) -> str:
    if mode == "flow-matching":
        return (
            "ConfMatchingLoss(MatchingLoss(InfoNCE(mode='proper', "
            "temperature=0.05), negatives_padding=0, blocksize=4096), "
            "alpha=10.0, confmode='mean')"
        )
    if mode == "metric":
        return (
            "Regr3D(L21, norm_mode='?avg_dis', gt_scale=True, "
            "sky_loss_value=0)"
        )
    if mode == "scale-shift-invariant":
        return (
            "Regr3D_ScaleShiftInv(L21, norm_mode='?avg_dis', gt_scale=True, "
            "sky_loss_value=0) + -1.*MatchingLoss(APLoss(nq='torch', "
            "fp=torch.float16), negatives_padding=4096)"
        )
    if mode == "relative-motion":
        return "D405RelativeMotionLoss()"
    if mode == "metric-correspondence":
        return "D405MetricCorrespondenceLoss()"
    if mode == "window-scale":
        return "D405WindowScaleLoss()"
    raise ValueError(f"unsupported validation criterion: {mode}")


def disable_evaluation_checkpoint_writes(training_module) -> None:
    training_module.misc.save_model = lambda *args, **kwargs: None
    training_module.save_final_model = lambda *args, **kwargs: None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mast3r-repo", type=Path, default=DEFAULT_TRAIN_REPO)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--accum-iter", type=int, default=8)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument(
        "--dataset-kind",
        choices=("stereo", "temporal", "rotation-matches"),
        default="stereo",
    )
    parser.add_argument(
        "--train-scope",
        choices=(
            "decoder-heads",
            "heads-only",
            "descriptors-only",
            "geometry-only",
            "descriptor-confidence-only",
        ),
        default="decoder-heads",
    )
    parser.add_argument("--lr", type=float, default=1e-6)
    parser.add_argument("--high-motion-repeat", type=int, default=3)
    parser.add_argument("--low-observability-repeat", type=int, default=1)
    parser.add_argument(
        "--low-observability-loss-weight", type=float, default=1.0
    )
    parser.add_argument(
        "--low-observability-max-tracked-points", type=int, default=160
    )
    parser.add_argument(
        "--low-observability-min-angular-speed-deg-s", type=float, default=8.0
    )
    parser.add_argument(
        "--training-criterion",
        choices=(
            "legacy",
            "relative-motion",
            "metric-correspondence",
            "metric-relative",
            "flow-matching",
            "window-scale",
            "window-scale-relative",
        ),
        default="legacy",
    )
    parser.add_argument("--relative-motion-weight", type=float, default=10.0)
    parser.add_argument(
        "--geometry-loss-weight",
        type=float,
        default=None,
        help=(
            "neutral auxiliary geometry-loss weight; defaults to the legacy "
            "--relative-motion-weight value for backward compatibility"
        ),
    )
    parser.add_argument(
        "--validation-criterion",
        choices=(
            "metric",
            "relative-motion",
            "metric-correspondence",
            "scale-shift-invariant",
            "flow-matching",
            "window-scale",
        ),
        default="metric",
        help=(
            "checkpoint selection objective; metric preserves D405 metre scale, "
            "while scale-shift-invariant reproduces the legacy MASt3R objective"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    args = parser.parse_args()
    geometry_loss_weight = (
        args.relative_motion_weight
        if args.geometry_loss_weight is None
        else args.geometry_loss_weight
    )
    include_matching = args.train_scope != "geometry-only"
    weight_decay = training_weight_decay(args.train_scope)
    if args.high_motion_repeat < 1:
        parser.error("--high-motion-repeat must be positive")
    if args.low_observability_repeat < 1:
        parser.error("--low-observability-repeat must be positive")
    if args.low_observability_loss_weight <= 0.0:
        parser.error("--low-observability-loss-weight must be positive")
    if args.low_observability_max_tracked_points <= 0:
        parser.error("--low-observability-max-tracked-points must be positive")
    if args.low_observability_min_angular_speed_deg_s < 0.0:
        parser.error(
            "--low-observability-min-angular-speed-deg-s must be non-negative"
        )
    if args.dry_run and args.eval_only:
        parser.error("--dry-run and --eval-only are mutually exclusive")

    repo = args.mast3r_repo.resolve()
    manifest = args.manifest.resolve()
    checkpoint = args.checkpoint.resolve()
    output = args.output.resolve()
    for path in (repo / "train.py", manifest, checkpoint):
        if not path.is_file():
            raise FileNotFoundError(path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("external_ground_truth_used") is not False:
        raise ValueError("training manifest uses external ground truth")

    sys.path[:0] = [str(ROOT / "scripts"), str(repo), str(repo / "dust3r")]
    import torch
    import dust3r.datasets
    import dust3r.training
    import mast3r.datasets
    from mast3r.datasets import (
        ARKitScenes,
        BlendedMVS,
        Co3d,
        MegaDepth,
        ScanNetpp,
        StaticThings3D,
        Waymo,
        WildRGBD,
    )
    from mast3r.losses import (
        APLoss,
        ConfMatchingLoss,
        InfoNCE,
        MatchingLoss,
        Regr3D,
        Regr3D_ScaleShiftInv,
    )
    from mast3r.model import AsymmetricMASt3R
    from mast3r_d405_ir_dataset import (
        D405IRRotationMatches,
        D405IRStereo,
        D405IRTemporal,
    )
    from mast3r_d405_losses import (
        D405MetricCorrespondenceLoss,
        D405RelativeMotionLoss,
        D405WindowScaleLoss,
    )

    original_mast3r_class = AsymmetricMASt3R

    def training_mast3r_factory(*model_args, **model_kwargs):
        return configure_trainable_parameters(
            original_mast3r_class(*model_args, **model_kwargs), args.train_scope
        )

    mast3r.datasets.D405IRStereo = D405IRStereo
    mast3r.datasets.D405IRTemporal = D405IRTemporal
    mast3r.datasets.D405IRRotationMatches = D405IRRotationMatches
    dust3r.datasets.D405IRStereo = D405IRStereo
    dust3r.datasets.D405IRTemporal = D405IRTemporal
    dust3r.datasets.D405IRRotationMatches = D405IRRotationMatches
    for name, value in {
        "AsymmetricMASt3R": training_mast3r_factory,
        "Regr3D": Regr3D,
        "Regr3D_ScaleShiftInv": Regr3D_ScaleShiftInv,
        "MatchingLoss": MatchingLoss,
        "ConfMatchingLoss": ConfMatchingLoss,
        "InfoNCE": InfoNCE,
        "APLoss": APLoss,
        "ARKitScenes": ARKitScenes,
        "BlendedMVS": BlendedMVS,
        "Co3d": Co3d,
        "MegaDepth": MegaDepth,
        "ScanNetpp": ScanNetpp,
        "StaticThings3D": StaticThings3D,
        "Waymo": Waymo,
        "WildRGBD": WildRGBD,
        "D405IRStereo": D405IRStereo,
        "D405IRTemporal": D405IRTemporal,
        "D405IRRotationMatches": D405IRRotationMatches,
        "D405RelativeMotionLoss": D405RelativeMotionLoss,
        "D405MetricCorrespondenceLoss": D405MetricCorrespondenceLoss,
        "D405WindowScaleLoss": D405WindowScaleLoss,
    }.items():
        setattr(dust3r.training, name, value)

    checkpoint_payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    # PyTorch >=2.6 defaults torch.load() to weights_only=True.  The trusted
    # official checkpoint stores its CLI arguments as argparse.Namespace, and
    # upstream MASt3R still calls torch.load() without an explicit override.
    torch.serialization.add_safe_globals([argparse.Namespace])
    model = frozen_encoder_model(checkpoint_payload["args"].model)
    dataset_class = {
        "stereo": "D405IRStereo",
        "temporal": "D405IRTemporal",
        "rotation-matches": "D405IRRotationMatches",
    }[args.dataset_kind]
    train_dataset = training_dataset_expression(
        dataset_class,
        manifest,
        args.seed,
        args.high_motion_repeat,
        args.low_observability_repeat,
        args.low_observability_loss_weight,
        args.low_observability_max_tracked_points,
        args.low_observability_min_angular_speed_deg_s,
    )
    test_correspondences = 512 if args.dataset_kind == "rotation-matches" else 1024
    test_dataset = (
        f"{dataset_class}(manifest={str(manifest)!r}, split='validation', "
        f"resolution=(512,384), n_corres={test_correspondences}, nneg=0.5, "
        f"seed={args.seed + 1})"
    )
    created_output = not output.exists()
    output.mkdir(parents=True, exist_ok=True)
    run_manifest = {
        "schema": "umi_mast3r_d405_ir_finetune_run_v1",
        "status": "DRY_RUN" if args.dry_run else "RUNNING",
        "external_ground_truth_used": False,
        "manifest": str(manifest),
        "manifest_sha256": sha256(manifest),
        "parent_checkpoint": str(checkpoint),
        "parent_checkpoint_sha256": sha256(checkpoint),
        "model": model,
        "seed": args.seed,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "accum_iter": args.accum_iter,
        "precision": "amp",
        "dataset_kind": args.dataset_kind,
        "train_scope": args.train_scope,
        "learning_rate": args.lr,
        "weight_decay": weight_decay,
        "high_motion_repeat": args.high_motion_repeat,
        "low_observability_repeat": args.low_observability_repeat,
        "low_observability_loss_weight": args.low_observability_loss_weight,
        "low_observability_max_tracked_points": (
            args.low_observability_max_tracked_points
        ),
        "low_observability_min_angular_speed_deg_s": (
            args.low_observability_min_angular_speed_deg_s
        ),
        "training_criterion": args.training_criterion,
        "relative_motion_weight": args.relative_motion_weight,
        "geometry_loss_weight": geometry_loss_weight,
        "descriptor_matching_loss_enabled": include_matching,
        "evaluation_only": args.eval_only,
        "validation_criterion": args.validation_criterion,
        "train_dataset": train_dataset,
        "test_dataset": test_dataset,
    }
    run_manifest_path = output / "run_manifest.json"
    run_manifest_path.write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.dry_run:
        dataset_type = {
            "stereo": D405IRStereo,
            "temporal": D405IRTemporal,
            "rotation-matches": D405IRRotationMatches,
        }[args.dataset_kind]
        dataset = dataset_type(
            manifest=str(manifest),
            split="train",
            resolution=(512, 384),
            n_corres=128,
            nneg=0.25,
            seed=args.seed,
        )
        sample = dataset[0]
        run_manifest["status"] = "READY"
        run_manifest["dry_run_sample_shapes"] = [list(view["img"].shape) for view in sample]
        run_manifest["dry_run_valid_depth_pixels"] = [
            int(view["valid_mask"].sum()) for view in sample
        ]
        run_manifest_path.write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(run_manifest, ensure_ascii=False, indent=2))
        return 0

    training_cli = [
            "--train_dataset", train_dataset,
            "--test_dataset", test_dataset,
            "--model", model,
            "--train_criterion",
            training_criterion_expression(
                args.training_criterion,
                geometry_loss_weight,
                args.relative_motion_weight,
                include_matching,
            ),
            "--test_criterion",
            validation_criterion_expression(args.validation_criterion),
            "--pretrained", str(checkpoint),
            "--lr", str(args.lr),
            "--weight_decay", str(weight_decay),
            "--min_lr", "1e-7",
            "--warmup_epochs", "1",
            "--batch_size", str(args.batch_size),
            "--accum_iter", str(args.accum_iter),
            "--num_workers", str(args.num_workers),
            "--seed", str(args.seed),
            "--amp", "1",
            "--save_freq", "1",
            "--keep_freq", "1",
            "--eval_freq", "1",
            "--disable_cudnn_benchmark",
            "--output_dir", str(output),
        ]
    training_cli.extend(training_schedule_args(args.epochs, args.eval_only))
    training_args = dust3r.training.get_args_parser().parse_args(training_cli)
    if args.eval_only:
        original_load_model = dust3r.training.misc.load_model

        def load_model_for_evaluation(*load_args, **load_kwargs):
            best_so_far = original_load_model(*load_args, **load_kwargs)
            training_namespace = load_kwargs.get("args")
            if training_namespace is None:
                training_namespace = load_args[0]
            training_namespace.start_epoch = 1
            return best_so_far

        dust3r.training.misc.load_model = load_model_for_evaluation
        disable_evaluation_checkpoint_writes(dust3r.training)
    dust3r.training.train(training_args)
    if args.eval_only:
        run_manifest["status"] = "COMPLETE_EVALUATION"
        run_manifest_path.write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    run_manifest["status"] = "COMPLETE"
    final_checkpoint = output / "checkpoint-final.pth"
    run_manifest["final_checkpoint"] = str(final_checkpoint)
    run_manifest["final_checkpoint_sha256"] = sha256(final_checkpoint)
    best_checkpoint = output / "checkpoint-best.pth"
    if best_checkpoint.is_file():
        run_manifest["best_checkpoint"] = str(best_checkpoint)
        run_manifest["best_checkpoint_sha256"] = sha256(best_checkpoint)
    pruned = []
    if created_output:
        for candidate in output.glob("checkpoint-*.pth"):
            if candidate.name in {"checkpoint-final.pth", "checkpoint-best.pth"}:
                continue
            candidate.unlink()
            pruned.append(candidate.name)
    run_manifest["pruned_checkpoints"] = sorted(pruned)
    run_manifest_path.write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
