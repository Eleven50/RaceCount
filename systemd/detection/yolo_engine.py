"""
YOLO inference engine.

Class filtering happens HERE, at the model call itself (classes=...),
not as a downstream check. With the stock COCO-pretrained weights,
"sheep" (18) and "person" (0) are distinct classes, so restricting
target_classes to [18] means a handler's hand/arm at the gate — even if
it triggers a detection — is discarded during the model's own
postprocessing and never becomes a Detections entry at all. Nothing
downstream (tracker, zone logic, counter) ever sees it.
"""
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger("racecount.detection")

# Relevant COCO class indices (standard 80-class ultralytics mapping).
COCO_SHEEP_CLASS_ID = 18
COCO_PERSON_CLASS_ID = 0


class YoloEngine:
    def __init__(
        self,
        model_path: str = "models/yolov8n.pt",
        target_classes: Optional[list] = None,
        confidence: float = 0.45,
        iou: float = 0.5,
        imgsz: int = 480,
        device: str = "cpu",
        task: str = "detect",
    ):
        """
        target_classes: COCO class IDs to keep. Defaults to sheep only.

        imgsz defaults to 480 rather than YOLO's usual 640 — raw PyTorch
        inference at 640 on a Pi 5 CPU will likely blow the <200ms latency
        budget on its own. Benchmark on your actual hardware; if it's
        still too slow, export to ONNX (see tools/export_model.py) rather
        than shrinking imgsz further, since that preserves more accuracy
        per unit of latency saved. ONNX, not NCNN: NCNN is usually faster
        on ARM CPUs, but recent Ultralytics releases have NCNN inference
        disabled specifically on ARM64 (raises NotImplementedError) — see
        tools/export_model.py's docstring for the tracking discussion.

        device is fixed to "cpu" by default per the "must not assume GPU
        acceleration" requirement — the Pi 5 has no CUDA-capable GPU.

        task is passed explicitly (rather than left for ultralytics to
        guess) mainly so NCNN-exported models — which don't embed task
        metadata as clearly as .pt files — load without a "guessing task"
        warning on every startup.
        """
        self.model_path = model_path
        self.target_classes = target_classes if target_classes is not None else [COCO_SHEEP_CLASS_ID]
        self.confidence = confidence
        self.iou = iou
        self.imgsz = imgsz
        self.device = device
        self.task = task
        self.model: Optional[YOLO] = None
        self._load_model()

    def _load_model(self):
        path = Path(self.model_path)
        if not path.exists():
            logger.warning(
                "Model file %s not found locally — ultralytics will try to "
                "download it if it's a recognised stock model name, which "
                "requires internet access on first run only. For a fully "
                "offline deployment, place the .pt/.onnx/ncnn files in "
                "models/ ahead of time.",
                self.model_path,
            )
        self.model = YOLO(self.model_path, task=self.task)
        resolved_names = getattr(self.model, "names", {})
        kept_names = [resolved_names.get(c, str(c)) for c in self.target_classes]
        logger.info(
            "Loaded YOLO model '%s' — keeping classes %s (%s), imgsz=%d, device=%s",
            self.model_path, self.target_classes, kept_names, self.imgsz, self.device,
        )

    def set_confidence(self, confidence: float):
        logger.info("Confidence threshold changed: %.2f -> %.2f", self.confidence, confidence)
        self.confidence = confidence

    def switch_model(self, model_path: str):
        logger.info("Switching model: %s -> %s", self.model_path, model_path)
        self.model_path = model_path
        self._load_model()

    def infer(self, frame: np.ndarray):
        """
        Runs one inference pass. Filtering to target_classes happens via
        the classes= argument, which ultralytics applies during its own
        NMS/postprocessing — this is a native model-level filter, not a
        manual post-hoc discard, so it costs nothing extra and can't be
        accidentally bypassed by code added later downstream.
        """
        results = self.model.predict(
            source=frame,
            classes=self.target_classes,
            conf=self.confidence,
            iou=self.iou,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        return results[0]
