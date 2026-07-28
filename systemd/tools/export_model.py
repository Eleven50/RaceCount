"""
Exports a YOLO .pt model to a faster runtime format for Pi deployment.

Raw PyTorch inference for YOLOv8n at imgsz=640 on a Raspberry Pi 5's CPU
will very likely miss the <200ms end-to-end latency target on its own.

Defaults to ONNX (via onnxruntime), NOT NCNN. NCNN is usually the faster
of the two on ARM CPUs and is what most Pi/YOLO tutorials point to — but
as of early 2026, Ultralytics has NCNN inference explicitly disabled on
ARM64 (`NotImplementedError: NCNN inference is not supported on ARM64`
in AutoBackend — confirmed by an Ultralytics maintainer, Jan 2026:
github.com/orgs/ultralytics/discussions/22214). It's a known, likely
temporary regression ("we will re-enable it later"), not a permanent
limitation — worth retrying if you're reading this well after mid-2026.
Until then, ONNX is the format that's actually confirmed working on the
Pi 5's aarch64 today, so it's the safer default here.

If you want to try NCNN anyway (e.g. you've confirmed it works on your
exact ultralytics version, or you pinned `ultralytics<8.4.0`), pass
--format ncnn — this script doesn't block it, it just doesn't default to
it.

Usage:
    python tools/export_model.py --model models/yolov8n.pt --imgsz 480
    python tools/export_model.py --model models/yolov8n.pt --format ncnn
"""
import argparse
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="models/yolov8n.pt", help="Path to source .pt model")
    parser.add_argument("--imgsz", type=int, default=480, help="Inference resolution to export for")
    parser.add_argument("--format", default="onnx", choices=["onnx", "ncnn"], help="Export format")
    args = parser.parse_args()

    if not Path(args.model).exists():
        print(f"'{args.model}' not found. Point --model at a local .pt file.")
        return 1

    if args.format == "ncnn":
        print(
            "WARNING: NCNN inference was disabled on ARM64 in recent Ultralytics "
            "releases (NotImplementedError in AutoBackend). This export will "
            "succeed, but loading it back on a Pi may fail unless that's been "
            "re-enabled by the time you're running this, or you've pinned "
            "ultralytics<8.4.0. See the module docstring above.\n"
        )

    model = YOLO(args.model)
    exported_path = model.export(format=args.format, imgsz=args.imgsz)

    print(f"\nExported {args.format.upper()} model to: {exported_path}")
    print("Update YoloEngine(model_path=...) in main.py to point at this path,")
    print("then re-benchmark end-to-end latency before/after — on the Pi itself,")
    print("not on the machine you ran this export on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
