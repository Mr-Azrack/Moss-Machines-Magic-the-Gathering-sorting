"""Camera-only preview for the MOSS camera at its current height."""

from datetime import datetime
import tkinter as tk
from tkinter import ttk
import time

import cv2
from PIL import Image, ImageTk

from capture_evidence import FOUNDATIONS, available_card_evidence, outer_border_crop, save_card_evidence, save_image

ROTATIONS = {
    '90° clockwise': cv2.ROTATE_90_CLOCKWISE,
    '90° counterclockwise': cv2.ROTATE_90_COUNTERCLOCKWISE,
    'None': None,
}


def upright(frame, orientation):
    transform = ROTATIONS[orientation]
    return frame if transform is None else cv2.rotate(frame, transform)


class SidewaysCard(ValueError):
    """The visible card contour needs a quarter-turn before rectification."""


def crop_upright(frame, contour):
    _, _, width, height = cv2.boundingRect(contour)
    if width >= height:
        raise SidewaysCard
    return available_card_evidence(frame, contour)


class Preview:
    def __init__(self, root, camera):
        self.root = root
        self.camera = camera
        self.frame = None
        self.evidence = None
        self.last_orientation = None
        self.directory = FOUNDATIONS / 'captures' / f'fixed-height_{datetime.now():%Y%m%d_%H%M%S}'
        self.orientation = tk.StringVar(value='None')

        root.title('MOSS Fixed-Height Camera Preview')
        root.protocol('WM_DELETE_WINDOW', self.close)
        controls = ttk.Frame(root, padding=8)
        controls.pack(fill='x')
        ttk.Label(controls, text='Image rotation').pack(side='left')
        ttk.Combobox(controls, textvariable=self.orientation, values=tuple(ROTATIONS),
                     state='readonly', width=23).pack(side='left', padx=8)
        self.save_button = ttk.Button(controls, text='Save evidence', command=self.save, state='disabled')
        self.save_button.pack(side='right')
        self.picture = ttk.Label(root, anchor='center')
        self.picture.pack(fill='both', expand=True, padx=8)
        self.result = ttk.Label(root, padding=8, wraplength=630)
        self.result.pack(fill='x')
        root.after(10, self.update)

    def update(self):
        ok, frame = self.camera.read()
        orientation = self.orientation.get()
        self.frame = upright(frame, orientation) if ok else None
        self.last_orientation = orientation
        self.evidence, reason = self.check(self.frame)
        self.save_button.configure(state='normal' if self.evidence is not None else 'disabled')
        if self.frame is not None:
            image = Image.fromarray(cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB))
            image.thumbnail((860, 610))
            photo = ImageTk.PhotoImage(image)
            self.picture.configure(image=photo)
            self.picture.image = photo
        self.result.configure(text=reason)
        self.root.after(100, self.update)

    @staticmethod
    def check(frame):
        if frame is None:
            return None, 'Waiting for a camera frame'
        try:
            evidence = outer_border_crop(frame, crop_upright)
        except SidewaysCard:
            return None, 'Choose a rotation that makes the card upright.'
        except ValueError:
            return None, 'Could not align this card crop. Try another position in the frame.'
        if evidence is None:
            return None, 'The full outer edge is unclear. Center the card and check that it is upright.'
        if evidence.footer_at_frame_edge:
            return evidence, 'Tight crop available. The footer may be clipped, so check it before relying on OCR.'
        if evidence.outside_margin_mm == 0:
            return evidence, 'Tight crop available. Check the footer, then save.'
        return evidence, 'Full 2 mm margin detected. Check the card text is upright, then save.'

    def save(self):
        if self.evidence is None or self.last_orientation != self.orientation.get():
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            key = time.time_ns()
            save_image(self.directory / f'{key}_raw.png', self.frame)
            path = self.directory / f'{key}_evidence.png'
            save_card_evidence(path, self.evidence)
        except OSError as exc:
            self.result.configure(text=f'Unable to save evidence. {exc}')
            return
        self.root.title(f'MOSS Fixed-Height Camera Preview · Saved {path.name}')

    def close(self):
        self.root.destroy()


def main():
    # The sorter USB camera is DirectShow device zero on this Windows machine.
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    try:
        if not camera.isOpened():
            raise SystemExit('Could not open the sorter USB camera. Close the sorter app before using the preview.')
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        root = tk.Tk()
        Preview(root, camera)
        root.mainloop()
    finally:
        camera.release()


if __name__ == '__main__':
    main()
