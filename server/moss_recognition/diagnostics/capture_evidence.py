"""Add a padded reference image to the local MOSS Foundations Capture diagnostic."""

from pathlib import Path
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
FOUNDATIONS = HERE.parent / 'Foundations_Pilot'
sys.path.insert(0, str(FOUNDATIONS))
from foundations_capture import PilotGUI, diagnostics, outer_border_crop, save_image
from camera_evidence import evidence_crop


def padded_card(frame, contour):
    """Use the sorter's top/bottom corner convention for its upright cards."""
    points = np.asarray(contour, dtype=np.float32).reshape(4, 2)
    top, bottom = np.split(points[points[:, 1].argsort()], 2)
    tl, tr = top[top[:, 0].argsort()]
    bl, br = bottom[bottom[:, 0].argsort()]
    return evidence_crop(frame, np.array([tl, tr, br, bl]))


def padded_from_raw(frame):
    """Use the existing outer-border diagnostic so the printed edge is visible."""
    return outer_border_crop(frame, padded_card)


class EvidenceGUI(PilotGUI):
    def __init__(self, root):
        super().__init__(root)
        self.root.title(self.root.title() + ' - 2 mm Footer Evidence')
        self.evidence_attempts = 0
        scanner = self.scanner
        process = scanner._process_card_from_contour

        def capture(frame, contour):
            if self.evidence_attempts < 20:
                self.evidence_attempts += 1
                try:
                    image = padded_from_raw(frame)
                    if image is None:
                        diagnostics.emit('footer_evidence_skipped', reason='outer_border_not_verified')
                    else:
                        self._capture_dir.mkdir(parents=True, exist_ok=True)
                        filename = self._capture_dir / f'{time.time_ns()}_evidence.png'
                        save_image(filename, image)
                        diagnostics.emit('footer_evidence_captured', image=str(filename), margin_mm=2)
                except (ValueError, OSError) as exc:
                    diagnostics.emit('footer_evidence_skipped', reason=str(exc))
            return process(frame, contour)

        scanner._process_card_from_contour = capture


if __name__ == '__main__':
    diagnostics.DiagnosticGUI = EvidenceGUI
    diagnostics.main()
