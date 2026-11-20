# MOSS printed footer diagnostic

The Windows diagnostic uses the existing Foundations Capture and its outer-border
detector. Copy `capture_evidence.py`, `evidence_replay.py`,
`read_footer_winrt.ps1`, and the parent `camera_evidence.py` to the diagnostic
folder next to `Foundations_Pilot`. Run `capture_evidence.py` in place of the
ordinary diagnostic during a supervised camera trial. It saves up to 20 evidence images using the same movement controls as the
normal Foundations diagnostic. It prefers a 2 mm outside margin. When the
camera frame cannot provide that margin, it saves a tight crop and notes if
the printed footer touches the camera edge.

For camera-only testing, use `fixed_height_preview.py` at the existing mount
position. Image rotation defaults to None. It requests the verified 1600×1200
camera mode, displays crop status, and saves evidence only when it detects a
card contour. A frame-edge warning asks the operator to inspect the footer.

Close the capture program before replaying the images. Use
`python evidence_replay.py --captures <capture-session-dir>` to read them with
Windows' built-in OCR, or use `--samples <samples.csv>` for earlier samples.
A report includes the tentative collector number, set abbreviation, and explicit
foil separator reading, alongside the raw OCR for each preprocessing pass.
Multiple renderings of one crop can share recognition errors. Compare the
output with the physical card before treating any field as correct. The report
flags possible frame clipping and missing collector-number or set readings.

This is an offline diagnostic. It makes no hosted requests and makes no sorting
or inventory decisions. Independent set symbol recognition and validation of
foil and nonfoil physical samples are separate camera checks.
