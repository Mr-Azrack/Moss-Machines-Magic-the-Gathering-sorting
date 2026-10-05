#!/usr/bin/env python3
"""
Sorter GUI entry point.

Extends the existing enhanced GUI without changing its unrelated behavior.
Adds:
- Z- / Z+ manual jog controls using the existing step-size control.
- Faster, thread-safe Arduino sensor monitoring.
- Correct mapping of firmware endstop keys (xmin/xmax/etc.) to GUI labels.
- Explicit Sort,<value> framing for card-routing commands.
- A longer serial reply window while a physical sort is in progress.
"""

import queue
import time
import tkinter as tk
from tkinter import messagebox

from gui_interface_enhanced import ScannerGUI as BaseScannerGUI


_ENDSTOP_KEY_MAP = {
    "xmin": "x_min",
    "xmax": "x_max",
    "ymin": "y_min",
    "ymax": "y_max",
    "zmin": "z_min",
    "zmax": "z_max",
}

_CONTROL_COMMANDS = {
    "StartMachine",
    "StopMachine",
    "HomeButton",
    "QuerySensors",
    "QueryParams",
}

_CONTROL_PREFIXES = (
    "SetMotor,",
    "SetParam,",
    "CalibrateX1",
    "CalibrateX2",
    "CalibrateY1",
    "CalibrateY2",
    "CalibrateZ1",
    "CalibrateZ2",
)

_SORT_REPLY_TIMEOUT_SECONDS = 120


class ScannerGUI(BaseScannerGUI):
    def __init__(self, root):
        # Keep sensor updates off the Tk worker thread.  The monitor thread only
        # talks to the Arduino and puts the newest snapshot here; Tk consumes it.
        self._sensor_queue = queue.Queue(maxsize=2)
        super().__init__(root)
        self.root.after(50, self._drain_sensor_queue)

    def _setup_arduino_tab(self, parent):
        """Build the existing Arduino tab, then add Z jog controls."""
        super()._setup_arduino_tab(parent)

        # Keep the GUI's initial value aligned with the calibrated firmware
        # default so an Upload cannot accidentally restore the old Z Cal 140.
        try:
            self.param_vars["zcal"].set("880")
        except Exception:
            pass

        # The first X/Y movement button's parent is the existing movement grid.
        # Reuse that exact grid so layout and enable/disable behavior stay intact.
        try:
            move_button_frame = self.movement_buttons[0].master

            z_minus = tk.Button(
                move_button_frame,
                text="Z-",
                command=lambda: self.send_arduino_command(
                    f"CalibrateZ1,{self.move_steps_var.get()}"
                ),
                bg="#607D8B",
                fg="white",
                font=("Arial", 8),
                padx=8,
                pady=3,
                relief=tk.FLAT,
            )
            z_minus.grid(row=2, column=0, padx=2, pady=1)
            self.movement_buttons.append(z_minus)

            tk.Label(
                move_button_frame,
                text="Z",
                bg="#1a1a1a",
                fg="#fff",
                font=("Arial", 9, "bold"),
                width=3,
            ).grid(row=2, column=1, padx=5)

            z_plus = tk.Button(
                move_button_frame,
                text="Z+",
                command=lambda: self.send_arduino_command(
                    f"CalibrateZ2,{self.move_steps_var.get()}"
                ),
                bg="#607D8B",
                fg="white",
                font=("Arial", 8),
                padx=8,
                pady=3,
                relief=tk.FLAT,
            )
            z_plus.grid(row=2, column=2, padx=2, pady=1)
            self.movement_buttons.append(z_plus)
        except Exception as exc:
            # Do not prevent the rest of the GUI from loading if layout changes.
            try:
                self.log_status(f"Z jog control setup failed: {exc}", error=True)
            except Exception:
                pass

    def send_arduino_command(self, cmd):
        """Keep the existing manual-command rules and include Z jog commands."""
        cmd_base = cmd.split(",", 1)[0]
        if self.machine_started and cmd_base in ("CalibrateZ1", "CalibrateZ2"):
            self.log_status(
                "⚠ Cannot send manual commands while machine is started", error=True
            )
            self.log_error("E508", f"cmd={cmd_base}")
            messagebox.showwarning(
                "Machine Started",
                "Stop the machine first to use manual controls",
            )
            return

        return super().send_arduino_command(cmd)

    def _send_arduino(self, cmd):
        """Frame card-routing values as explicit Sort commands."""
        text = str(cmd or "").strip()
        if not text:
            return None

        is_control = (
            text in _CONTROL_COMMANDS
            or text.startswith("Sort,")
            or any(text.startswith(prefix) for prefix in _CONTROL_PREFIXES)
        )

        wire_cmd = text if is_control else f"Sort,{text}"

        # A calibrated sort now contains roughly 92k Z steps down and back up,
        # plus X/Y travel and card release.  Keep the scan transaction locked
        # until the Arduino reports completion so the same card cannot be
        # rescanned and the live monitor cannot consume the sort reply.
        serial_obj = None
        old_timeout = None
        if wire_cmd.startswith("Sort,"):
            try:
                serial_obj = getattr(self.scanner, "ser", None)
                if serial_obj is not None:
                    old_timeout = serial_obj.timeout
                    serial_obj.timeout = _SORT_REPLY_TIMEOUT_SECONDS
            except Exception:
                serial_obj = None
                old_timeout = None

        try:
            return super()._send_arduino(wire_cmd)
        finally:
            if serial_obj is not None and old_timeout is not None:
                try:
                    serial_obj.timeout = old_timeout
                except Exception:
                    pass

    def _queue_sensor_snapshot(self, snapshot):
        """Keep only the newest sensor snapshot so the display never falls behind."""
        try:
            while self._sensor_queue.full():
                self._sensor_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            self._sensor_queue.put_nowait(snapshot)
        except queue.Full:
            pass

    def _drain_sensor_queue(self):
        """Apply the newest Arduino sensor snapshot on Tk's main thread."""
        latest = None
        try:
            while True:
                latest = self._sensor_queue.get_nowait()
        except queue.Empty:
            pass

        if latest is not None:
            self._apply_sensor_snapshot(latest)

        try:
            self.root.after(50, self._drain_sensor_queue)
        except Exception:
            pass

    def _apply_sensor_snapshot(self, snapshot):
        """Update ToF, endstop, and machine-state widgets from one snapshot."""
        try:
            if "range" in snapshot:
                value = int(snapshot["range"])
                self.sensor_data["range"] = value
                self.range_label.configure(text=f"Range Sensor: {value} mm")

            for firmware_key, gui_key in _ENDSTOP_KEY_MAP.items():
                if firmware_key not in snapshot:
                    continue

                value = int(snapshot[firmware_key])
                self.sensor_data[gui_key] = value

                label = self.endstop_labels.get(gui_key)
                if label is not None:
                    status = "●" if value == 1 else "○"
                    color = "#00ff00" if value == 1 else "#888888"
                    name = gui_key.replace("_", "-").upper()
                    label.configure(text=f"{name}: {status}", fg=color)

            if "started" in snapshot:
                started_state = int(snapshot["started"]) == 1
                self.sensor_data["started"] = int(started_state)

                if started_state != self.machine_started:
                    self.machine_started = started_state
                    if started_state:
                        self.machine_state_label.configure(
                            text="● STARTED", fg="#44ff44"
                        )
                        self.start_machine_btn.configure(state=tk.DISABLED)
                        self.stop_machine_btn.configure(state=tk.NORMAL)
                    else:
                        self.machine_state_label.configure(
                            text="● STOPPED", fg="#ff4444"
                        )
                        self.start_machine_btn.configure(state=tk.NORMAL)
                        self.stop_machine_btn.configure(state=tk.DISABLED)
                    self._update_controls_state()
        except Exception as exc:
            self.log_status(f"Sensor display error: {exc}", error=True)

    def _arduino_monitor_loop(self):
        """Poll Arduino sensor data quickly without touching Tk from this thread."""
        while self.arduino_monitoring:
            connected = bool(self.scanner and getattr(self.scanner, "ser", None))
            plugin = getattr(self, "arduino_plugin", None)
            if not connected and plugin is not None:
                try:
                    connected = bool(plugin.is_connected())
                except Exception:
                    connected = False

            if connected:
                try:
                    response = self._send_arduino("QuerySensors")
                    if response and "Sensors" in response:
                        snapshot = {}
                        parts = response.replace("<", "").replace(">", "").split(",")
                        for part in parts[1:]:
                            if "=" not in part:
                                continue
                            key, value = part.split("=", 1)
                            snapshot[key.strip()] = value.strip()

                        self._queue_sensor_snapshot(snapshot)
                except Exception as exc:
                    self.log_status(f"Monitor error: {exc}", error=True)
                    self.log_error("E405", f"monitor_loop:{exc}")

            # With Main.ino's 100 ms serial timeout this gives a responsive
            # display without flooding a 9600-baud link.
            time.sleep(0.10)


def main():
    root = tk.Tk()
    app = ScannerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.on_close()


if __name__ == "__main__":
    main()
