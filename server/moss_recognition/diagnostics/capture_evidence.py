"""Add a reference image to the local MOSS Foundations Capture diagnostic."""

import json
from pathlib import Path
import sys
import time
from typing import NamedTuple

import numpy as np

HERE = Path(__file__).resolve().parent
FOUNDATIONS = HERE.parent / 'Foundations_Pilot'
sys.path.insert(0, str(FOUNDATIONS))
from foundations_capture import PilotGUI, diagnostics, outer_border_crop, save_image
from camera_evidence import EVIDENCE_MARGIN_MM, EvidenceMarginError, evidence_crop


class CardEvidence(NamedTuple):
    image: np.ndarray
    outside_margin_mm: int
    footer_at_frame_edge: bool


def ordered_corners(contour):
    """Use the sorter's top/bottom corner convention for its upright cards."""
    points = np.asarray(contour, dtype=np.float32).reshape(4, 2)
    top, bottom = np.split(points[points[:, 1].argsort()], 2)
    tl, tr = top[top[:, 0].argsort()]
    bl, br = bottom[bottom[:, 0].argsort()]
    return np.array([tl, tr, br, bl])


def padded_card(frame, contour):
    return evidence_crop(frame, ordered_corners(contour))


def padded_from_raw(frame):
    return outer_border_crop(frame, padded_card)


def available_card_evidence(frame, contour):
    corners = ordered_corners(contour)
    try:
        image = evidence_crop(frame, corners)
    except EvidenceMarginError:
        # Use only pixels the sensor actually captured. Preserve the warning if
        # the printed footer could extend outside that image.
        image = evidence_crop(frame, corners, margin_mm=0)
        height, width = frame.shape[:2]
        footer = corners[2:]
        at_edge = bool(footer[:, 1].max() >= height - 3
                       or footer[:, 0].min() <= 2 or footer[:, 0].max() >= width - 3)
        return CardEvidence(image, 0, at_edge)
    return CardEvidence(image, EVIDENCE_MARGIN_MM, False)


def save_card_evidence(path, evidence):
    save_image(path, evidence.image)
    path.with_suffix('.json').write_text(json.dumps({
        'outside_margin_mm': evidence.outside_margin_mm,
        'footer_at_frame_edge': evidence.footer_at_frame_edge,
    }), encoding='utf-8')


class EvidenceGUI(PilotGUI):
    def __init__(self, root):
        super().__init__(root)
        self.root.title(self.root.title() + ' - Footer Evidence')
        self.evidence_attempts = 0
        scanner = self.scanner
        process = scanner._process_card_from_contour

        def capture(frame, contour):
            if self.evidence_attempts < 20:
                self.evidence_attempts += 1
                try:
                    evidence = outer_border_crop(frame, available_card_evidence)
                    if evidence is None:
                        diagnostics.emit('footer_evidence_skipped', reason='outer_border_not_verified')
                    else:
                        self._capture_dir.mkdir(parents=True, exist_ok=True)
                        filename = self._capture_dir / f'{time.time_ns()}_evidence.png'
                        save_card_evidence(filename, evidence)
                        diagnostics.emit('footer_evidence_captured', image=str(filename),
                                         margin_mm=evidence.outside_margin_mm,
                                         footer_at_frame_edge=evidence.footer_at_frame_edge)
                except (ValueError, OSError) as exc:
                    diagnostics.emit('footer_evidence_skipped', reason=str(exc))
            return process(frame, contour)

        scanner._process_card_from_contour = capture


if __name__ == '__main__':
    diagnostics.DiagnosticGUI = EvidenceGUI
    diagnostics.main()
