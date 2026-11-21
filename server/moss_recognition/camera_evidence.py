"""Perspective-rectify a Magic crop with its printed edges intact."""

MTG_WIDTH_MM = 63
MTG_HEIGHT_MM = 88
EVIDENCE_MARGIN_MM = 2


class EvidenceMarginError(ValueError):
    """The rectified crop would require pixels outside the camera image."""


def evidence_crop(frame, corners, width=745, height=1043, *, margin_mm=EVIDENCE_MARGIN_MM):
    """Rectify TL, TR, BR, BL corners, with an optional outside margin."""
    import cv2
    import numpy as np

    source = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    if not np.isfinite(source).all() or not cv2.isContourConvex(source):
        raise ValueError('Expected an ordered four-corner card contour')
    x_pad = round(width * margin_mm / MTG_WIDTH_MM)
    y_pad = round(height * margin_mm / MTG_HEIGHT_MM)
    target = np.float32([[x_pad, y_pad], [x_pad + width - 1, y_pad],
                         [x_pad + width - 1, y_pad + height - 1], [x_pad, y_pad + height - 1]])
    transform = cv2.getPerspectiveTransform(source, target)
    padded_width, padded_height = width + 2 * x_pad, height + 2 * y_pad
    if np.linalg.matrix_rank(transform) != 3:
        raise ValueError('The card perspective is degenerate')
    outer = np.float32([[[0, 0], [padded_width - 1, 0],
                        [padded_width - 1, padded_height - 1], [0, padded_height - 1]]])
    footprint = cv2.perspectiveTransform(outer, np.linalg.inv(transform))[0]
    # An exactly in-frame corner can round to a tiny negative value in projection.
    epsilon = 1e-3
    if (not np.isfinite(footprint).all() or footprint.min() < -epsilon
            or footprint[:, 0].max() > frame.shape[1] - 1 + epsilon
            or footprint[:, 1].max() > frame.shape[0] - 1 + epsilon):
        raise EvidenceMarginError('The requested crop extends outside the camera frame')
    return cv2.warpPerspective(frame, transform, (padded_width, padded_height))
