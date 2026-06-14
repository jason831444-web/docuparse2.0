import argparse
import shutil
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


DEFAULT_MODELS = {
    "paddleocr_vl": "PaddlePaddle/PaddleOCR-VL-1.6",
}


def repo_size_gb(repo_id: str) -> float:
    info = HfApi().model_info(repo_id, files_metadata=True)
    total_bytes = sum(sibling.size or 0 for sibling in info.siblings)
    return total_bytes / 1024**3


def free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / 1024**3


def download(repo_id: str, target_dir: Path, dry_run: bool) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    size = repo_size_gb(repo_id)
    available = free_gb(target_dir)
    print(f"{repo_id}: ~{size:.2f} GiB, available at {target_dir}: {available:.2f} GiB")
    if available < size + 2:
        raise SystemExit(
            f"Not enough free space for {repo_id}. Need at least ~{size + 2:.2f} GiB including headroom."
        )
    if dry_run:
        return
    snapshot_download(repo_id=repo_id, local_dir=target_dir, local_dir_use_symlinks=False)
    print(f"Downloaded {repo_id} to {target_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download optional DocuParse open-source model weights.")
    parser.add_argument("--model", choices=["paddleocr_vl", "all"], default="all")
    parser.add_argument("--target", type=Path, default=Path("models"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = DEFAULT_MODELS if args.model == "all" else {args.model: DEFAULT_MODELS[args.model]}
    for name, repo_id in selected.items():
        download(repo_id, args.target / name, args.dry_run)


if __name__ == "__main__":
    main()
