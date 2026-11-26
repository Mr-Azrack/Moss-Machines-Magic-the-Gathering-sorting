"""CPU inference for the repo's trained MTG set-symbol CNN."""
import io
import os
from pathlib import Path

from PIL import Image, ImageOps


class SetSymbolModel:
    def __init__(self, model_dir=None):
        import numpy as np
        import torch
        import torch.nn as nn
        from torchvision import models

        self.np = np
        self.torch = torch
        self.torch.set_num_threads(max(1, int(os.environ.get('MOSS_SET_MODEL_THREADS', '1'))))
        self.image_size = 64
        root = Path(model_dir or os.environ.get(
            "MOSS_SET_MODEL_DIR", "/opt/moss-ai/worker/models"))
        parts = sorted(root.glob("set_cnn.part*.pt"))
        monolithic = root / "set_cnn.pt"
        if parts:
            buffer = io.BytesIO()
            for part in parts:
                with part.open("rb") as source:
                    buffer.write(source.read())
            buffer.seek(0)
            checkpoint = torch.load(buffer, map_location="cpu", weights_only=True)
        elif monolithic.is_file():
            checkpoint = torch.load(monolithic, map_location="cpu", weights_only=True)
        else:
            raise FileNotFoundError(f"No trained set-symbol model in {root}")

        class_to_idx = checkpoint.get("class_to_idx")
        if not isinstance(class_to_idx, dict) or len(class_to_idx) < 2:
            raise ValueError("Set-symbol checkpoint has no valid class map")
        self.idx_to_class = {int(index): str(label).lower()
                             for label, index in class_to_idx.items()}
        self.image_size = int(checkpoint.get("image_size", 64))
        if not 16 <= self.image_size <= 256:
            raise ValueError("Unexpected set-symbol input size")

        model = models.resnet18(weights=None)
        model.fc = nn.Linear(model.fc.in_features, len(class_to_idx))
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        self.model = model
        print(f"MOSS set-symbol CNN loaded ({len(class_to_idx)} classes)", flush=True)

    def predict(self, image):
        """Return the top symbol code and its model score; no image is retained."""
        image = ImageOps.exif_transpose(image)
        if image.width > image.height:
            image = image.transpose(Image.Transpose.ROTATE_90)
        width, height = image.size
        x1, y1, x2, y2 = 0.83, 0.57, 0.92, 0.61
        crop = image.crop((round(width * x1), round(height * y1),
                           round(width * x2), round(height * y2)))
        if crop.width < 8 or crop.height < 8:
            return None
        resized = crop.resize((self.image_size, self.image_size), Image.Resampling.BILINEAR)
        values = self.np.asarray(resized.convert("RGB"), dtype=self.np.float32) / 255.0
        values = (values - 0.5) / 0.5
        tensor = self.torch.from_numpy(values.transpose(2, 0, 1)).unsqueeze(0)
        with self.torch.inference_mode():
            probabilities = self.torch.softmax(self.model(tensor), dim=1)[0]
        limit = min(3, probabilities.numel())
        scores, indices = self.torch.topk(probabilities, limit)
        ranked = []
        for score, index in zip(scores.tolist(), indices.tolist()):
            code = self.idx_to_class.get(int(index))
            if code:
                ranked.append({"set_code": code, "confidence": round(float(score), 5)})
        if not ranked:
            return None
        return {"set_code": ranked[0]["set_code"],
                "confidence": ranked[0]["confidence"], "scores": ranked}
