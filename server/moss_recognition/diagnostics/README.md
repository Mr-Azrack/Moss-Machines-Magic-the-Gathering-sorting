# MOSS printed footer diagnostic

The Windows diagnostic uses the existing Foundations Capture and its outer-border
detector. Copy `capture_evidence.py`, `evidence_replay.py`,
`read_footer_winrt.ps1`, and the parent `camera_evidence.py` to the diagnostic
folder next to `Foundations_Pilot`. Run `capture_evidence.py` in place of the
ordinary diagnostic during a supervised camera trial. It saves up to 20 padded
evidence images in the capture session, using the same card positioning and
movement controls as the normal Foundations diagnostic. It logs why an image
was skipped when the outer contour or 2 mm margin could not be verified.

Close the capture program before replaying the images. Use
`python evidence_replay.py --captures <capture-session-dir>` to read them with
Windows' built-in OCR, or use `--samples <samples.csv>` for earlier samples.
A report includes the tentative collector number, set abbreviation, and explicit
foil separator reading, alongside the raw OCR for each preprocessing pass.
Multiple renderings of one crop can share recognition errors. Compare the
output with the physical card before treating any field as correct.

This is an offline diagnostic. It makes no hosted requests and makes no sorting
or inventory decisions. Independent set symbol recognition and validation of
foil and nonfoil physical samples are separate camera checks.
