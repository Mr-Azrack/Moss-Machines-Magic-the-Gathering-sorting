# detectname.py
import os
import warnings
import logging
import cv2
import numpy as np
from difflib import SequenceMatcher

# Force CPU-only BEFORE torch/easyocr import + silence warnings
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")       # disable CUDA
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1") # avoid MPS attempts

warnings.filterwarnings("ignore", message=r".*pin_memory.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*MPS.*", category=UserWarning)
logging.getLogger("torch").setLevel(logging.ERROR)

import easyocr
reader = easyocr.Reader(['en'], gpu=False, verbose=False)


def is_reasonable_text(text: str) -> bool:
    text = text.lower().strip()
    if len(text) < 2:
        return False
    forbidden_combos = ['zx', 'xj', 'qj', 'jq', 'qz', 'vw', 'vv', 'jk', 'kj']
    if any(combo in text for combo in forbidden_combos):
        return False
    vowels = {'a', 'e', 'i', 'o', 'u'}
    if len(text) > 3 and not any(v in text for v in vowels):
        return False
    return True


def find_text(frame, card_contour):
    """Extract probable card-name text from a slim ROI near the top edge of the card (NO imshow)."""
    try:
        if card_contour is None:
            return None

        x_card, y_card, w_card, h_card = cv2.boundingRect(card_contour)

        # Narrow top band ROI where names typically sit
        roi_top_start = max(0, int(y_card + h_card * 0.05))
        roi_top_end   = min(frame.shape[0], int(y_card + h_card * 0.13))
        roi_left      = max(0, x_card)
        roi_right     = min(frame.shape[1], x_card + w_card)

        roi = frame[roi_top_start:roi_top_end, roi_left:roi_right]
        if roi.size == 0:
            return None

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        processed_images = []
        # Method 1: Otsu
        _, thresh1 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed_images.append(thresh1)
        # Method 2: Adaptive
        thresh2 = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )
        processed_images.append(thresh2)
        # Method 3: Denoise + Otsu
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        _, thresh3 = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed_images.append(thresh3)

        best_text = ""
        for img in processed_images:
            scaled = cv2.resize(img, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            result = reader.readtext(scaled, detail=0)
            text = ' '.join(result).strip()
            if len(text) > len(best_text):
                best_text = text

        clean_text = ''.join(c for c in best_text if c.isalpha())
        if len(clean_text) >= 2 and is_reasonable_text(clean_text):
            return clean_text.title()
        return None

    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return None


def compare_strings(string1, string2) -> float:
    if not string1 or not string2:
        return 0.0
    string1 = str(string1).lower()
    string2 = str(string2).lower()
    matcher = SequenceMatcher(None, string1, string2)
    return matcher.ratio()
