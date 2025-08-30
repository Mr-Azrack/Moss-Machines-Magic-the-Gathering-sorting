# sitecustomize.py
# Auto-imported by Python at startup if present on sys.path (your project folder).
# Safely no-ops OpenCV GUI calls on builds without HighGUI (e.g., Windows Store Python).
try:
    import cv2  # type: ignore
    _orig_imshow = getattr(cv2, "imshow", None)
    _orig_waitKey = getattr(cv2, "waitKey", None)
    _orig_destroyAllWindows = getattr(cv2, "destroyAllWindows", None)

    def _safe_imshow(*args, **kwargs):
        try:
            if _orig_imshow is not None:
                return _orig_imshow(*args, **kwargs)
        except Exception:
            return None

    def _safe_waitKey(*args, **kwargs):
        try:
            if _orig_waitKey is not None:
                return _orig_waitKey(*args, **kwargs)
        except Exception:
            return -1
        return -1

    def _safe_destroyAllWindows(*args, **kwargs):
        try:
            if _orig_destroyAllWindows is not None:
                return _orig_destroyAllWindows(*args, **kwargs)
        except Exception:
            return None
        return None

    cv2.imshow = _safe_imshow
    cv2.waitKey = _safe_waitKey
    cv2.destroyAllWindows = _safe_destroyAllWindows

except Exception:
    pass
