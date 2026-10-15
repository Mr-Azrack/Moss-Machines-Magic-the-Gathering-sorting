#!/usr/bin/env python3
"""
Sorter GUI entry point.

Extends the existing enhanced GUI without changing its unrelated behavior.
Adds:
- Z- / Z+ manual jog controls using the existing step-size control.
- Faster, thread-safe Arduino sensor monitoring.
- Correct mapping of firmware endstop keys (xmin/xmax/etc.) to GUI labels.
- Explicit Sort,<value> framing for card-routing commands.
- A longer serial reply window while a physical sort or homing operation is in progress.
- An immediate StopMachine write that can interrupt an active sort transaction.
- A touchscreen-friendly detected COM-port dropdown with Refresh.
- A touchscreen Arduino .ino firmware loader using arduino-cli.
- Compact UI enabled by default.
- Status log positioned under the live camera feed.
"""

import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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
_ARDUINO_FQBN = "arduino:avr:mega:cpu=atmega2560"


class ScannerGUI(BaseScannerGUI):
    def __init__(self, root):
        # Keep sensor updates off the Tk worker thread.  The monitor thread only
        # talks to the Arduino and puts the newest snapshot here; Tk consumes it.
        self._sensor_queue = queue.Queue(maxsize=2)
        super().__init__(root)
        self.root.after(50, self._drain_sensor_queue)

    def _setup_gui(self):
        """Build the existing GUI, default to compact mode, and relocate status."""
        # The base class creates compact_mode_var before calling _setup_gui().
        # Set it here so the checkbox is already selected when the GUI appears.
        try:
            self.compact_mode_var.set(True)
        except Exception:
            pass

        super()._setup_gui()
        self._move_status_log_under_camera()

        # Apply the base GUI's existing compact-layout behavior after all widgets
        # have been created and the status log has been relocated.
        try:
            self.apply_compact_mode()
        except Exception:
            pass

    def _move_status_log_under_camera(self):
        """Move the existing live status log from the controls to camera panel."""
        old_text = getattr(self, "status_text", None)
        camera_canvas = getattr(self, "canvas", None)
        if old_text is None or camera_canvas is None:
            return

        old_frame = old_text.master
        old_parent = old_frame.master
        existing_text = ""

        try:
            old_text.configure(state=tk.NORMAL)
            existing_text = old_text.get("1.0", "end-1c")
        except Exception:
            existing_text = ""

        # Remove the old STATUS LOG heading created by the base Scanner tab.
        try:
            for child in old_parent.winfo_children():
                if child is old_frame:
                    continue
                try:
                    if isinstance(child, tk.Label) and child.cget("text") == "STATUS LOG":
                        child.destroy()
                except Exception:
                    continue
        except Exception:
            pass

        try:
            old_frame.destroy()
        except Exception:
            pass

        camera_parent = camera_canvas.master

        self.camera_status_heading = tk.Label(
            camera_parent,
            text="STATUS LOG",
            bg="#1e1e1e",
            fg="#ffffff",
            font=self.header_font,
        )
        self.camera_status_heading.pack(pady=(8, 3))

        self.camera_status_frame = tk.Frame(
            camera_parent,
            bg="#1a1a1a",
            relief=tk.SUNKEN,
            bd=1,
        )
        self.camera_status_frame.pack(
            fill=tk.BOTH,
            expand=True,
            padx=10,
            pady=(0, 5),
        )

        self.status_text = tk.Text(
            self.camera_status_frame,
            height=10,
            bg="#1a1a1a",
            fg="#00ff00",
            font=("Consolas", 8),
            wrap=tk.WORD,
            state=tk.NORMAL,
        )
        status_scroll = tk.Scrollbar(
            self.camera_status_frame,
            command=self.status_text.yview,
        )
        self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        if existing_text:
            self.status_text.insert(tk.END, existing_text)
            self.status_text.see(tk.END)

        self.status_text.configure(state=tk.DISABLED)

    def _setup_arduino_tab(self, parent):
        """Build the existing Arduino tab, then add sorter-specific controls."""
        super()._setup_arduino_tab(parent)

        # Replace the original typed COM entry with a detected-port selector.
        # The same serial_port_var is preserved, so existing connection logic is
        # unchanged and no keyboard is required for normal use.
        self._install_serial_port_selector(parent)
        self._install_firmware_loader()

        # Keep the GUI's initial motion/calibration values aligned with the
        # tuned firmware defaults so an Upload cannot restore older speeds.
        try:
            self.param_vars["speed"].set("600")
            self.param_vars["zcal"].set("935")
            self.param_vars["zspeed"].set("36")
            self.param_vars["zespeed"].set("50")
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

    def _walk_widgets(self, parent):
        """Yield all descendants of a Tk widget."""
        for child in parent.winfo_children():
            yield child
            yield from self._walk_widgets(child)

    def _install_serial_port_selector(self, parent):
        """Replace the base GUI's COM text entry with a compact readonly dropdown."""
        try:
            target_var = str(self.serial_port_var)
            port_entry = None

            for widget in self._walk_widgets(parent):
                if not isinstance(widget, tk.Entry):
                    continue
                try:
                    if str(widget.cget("textvariable")) == target_var:
                        port_entry = widget
                        break
                except Exception:
                    continue

            if port_entry is None:
                self.log_status("COM selector setup: port entry not found", error=True)
                return

            holder = port_entry.master
            self._arduino_connection_frame = holder.master
            siblings = list(holder.winfo_children())
            try:
                entry_index = siblings.index(port_entry)
            except ValueError:
                entry_index = -1

            before_widget = None
            if entry_index >= 0 and entry_index + 1 < len(siblings):
                before_widget = siblings[entry_index + 1]

            port_entry.destroy()

            # Match the original baud-rate field instead of consuming a large
            # portion of the Connection row.
            self.serial_port_combo = ttk.Combobox(
                holder,
                textvariable=self.serial_port_var,
                state="readonly",
                width=8,
                font=("Arial", 9),
            )
            combo_pack = {"side": tk.LEFT, "padx": 3}
            if before_widget is not None:
                combo_pack["before"] = before_widget
            self.serial_port_combo.pack(**combo_pack)

            self.serial_port_refresh_btn = tk.Button(
                holder,
                text="↻ Refresh",
                command=self._refresh_serial_ports,
                bg="#455A64",
                fg="white",
                activebackground="#546E7A",
                activeforeground="white",
                font=("Arial", 9, "bold"),
                padx=8,
                pady=4,
                relief=tk.FLAT,
            )
            refresh_pack = {"side": tk.LEFT, "padx": (3, 8)}
            if before_widget is not None:
                refresh_pack["before"] = before_widget
            self.serial_port_refresh_btn.pack(**refresh_pack)

            self._refresh_serial_ports(log_result=False)
        except Exception as exc:
            try:
                self.log_status(f"COM selector setup failed: {exc}", error=True)
            except Exception:
                pass

    def _refresh_serial_ports(self, log_result=True):
        """Refresh detected serial devices and keep/select a valid COM port."""
        ports = []
        try:
            from serial.tools import list_ports

            ports = [port.device for port in list_ports.comports()]
        except Exception as exc:
            if log_result:
                self.log_status(f"Unable to list COM ports: {exc}", error=True)

        ports = sorted(set(ports))

        combo = getattr(self, "serial_port_combo", None)
        if combo is not None:
            combo["values"] = ports

        current = self.serial_port_var.get().strip()
        if current in ports:
            selected = current
        elif ports:
            selected = ports[0]
        else:
            selected = ""

        self.serial_port_var.set(selected)

        if log_result:
            if ports:
                self.log_status(f"Detected COM ports: {', '.join(ports)}")
            else:
                self.log_status("No COM ports detected", error=True)

    def _install_firmware_loader(self):
        """Add a touchscreen firmware picker/uploader below Connection."""
        conn_frame = getattr(self, "_arduino_connection_frame", None)
        if conn_frame is None:
            self.log_status("Firmware loader setup: Connection frame not found", error=True)
            return

        default_firmware = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "Main",
            "Main.ino",
        )
        if not os.path.isfile(default_firmware):
            default_firmware = ""

        self.firmware_path_var = tk.StringVar(value=default_firmware)

        firmware_row = tk.Frame(conn_frame, bg="#1a1a1a")
        firmware_row.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Label(
            firmware_row,
            text="Firmware:",
            bg="#1a1a1a",
            fg="#fff",
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.firmware_name_label = tk.Label(
            firmware_row,
            text=os.path.basename(default_firmware) if default_firmware else "No .ino selected",
            bg="#303030",
            fg="#ffffff",
            font=("Arial", 9),
            width=16,
            anchor="w",
            padx=5,
            pady=4,
        )
        self.firmware_name_label.pack(side=tk.LEFT, padx=3)

        self.firmware_browse_btn = tk.Button(
            firmware_row,
            text="Browse",
            command=self._browse_firmware,
            bg="#455A64",
            fg="white",
            activebackground="#546E7A",
            activeforeground="white",
            font=("Arial", 9, "bold"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
        )
        self.firmware_browse_btn.pack(side=tk.LEFT, padx=4)

        self.firmware_upload_btn = tk.Button(
            firmware_row,
            text="Upload Firmware",
            command=self._upload_firmware,
            bg="#1976D2",
            fg="white",
            activebackground="#1E88E5",
            activeforeground="white",
            font=("Arial", 9, "bold"),
            padx=12,
            pady=4,
            relief=tk.FLAT,
        )
        self.firmware_upload_btn.pack(side=tk.LEFT, padx=4)

    def _browse_firmware(self):
        """Choose an Arduino sketch without requiring keyboard input."""
        current = self.firmware_path_var.get().strip()
        initial_dir = os.path.dirname(current) if current else os.path.dirname(os.path.abspath(__file__))
        path = filedialog.askopenfilename(
            title="Select Arduino Firmware",
            initialdir=initial_dir,
            filetypes=[("Arduino sketch", "*.ino"), ("All files", "*.*")],
        )
        if not path:
            return

        self.firmware_path_var.set(path)
        self.firmware_name_label.configure(text=os.path.basename(path))
        self.log_status(f"Firmware selected: {path}")

    def _find_arduino_cli(self):
        """Find arduino-cli from PATH or common Arduino IDE 2 locations."""
        for executable in ("arduino-cli", "arduino-cli.exe"):
            found = shutil.which(executable)
            if found:
                return found

        candidates = []
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        local_app_data = os.environ.get("LOCALAPPDATA")

        for root in (program_files, program_files_x86):
            if root:
                candidates.append(
                    os.path.join(
                        root,
                        "Arduino IDE",
                        "resources",
                        "app",
                        "lib",
                        "backend",
                        "resources",
                        "arduino-cli.exe",
                    )
                )

        if local_app_data:
            candidates.append(
                os.path.join(
                    local_app_data,
                    "Programs",
                    "Arduino IDE",
                    "resources",
                    "app",
                    "lib",
                    "backend",
                    "resources",
                    "arduino-cli.exe",
                )
            )

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

        return None

    def _release_serial_for_firmware_upload(self):
        """Release the selected COM port so arduino-cli can own it."""
        self.arduino_monitoring = False
        try:
            if hasattr(self, "monitor_var"):
                self.monitor_var.set(False)
        except Exception:
            pass

        serial_obj = None
        try:
            if self.scanner:
                serial_obj = getattr(self.scanner, "ser", None)
        except Exception:
            serial_obj = None

        if serial_obj is not None:
            try:
                serial_obj.close()
            except Exception:
                pass
            try:
                self.scanner.ser = None
            except Exception:
                pass

        self.machine_started = False
        try:
            self.machine_state_label.configure(text="● STOPPED", fg="#ff4444")
            self.start_machine_btn.configure(state=tk.DISABLED)
            self.stop_machine_btn.configure(state=tk.DISABLED)
            self.connect_btn.configure(text="Connect", state=tk.NORMAL, bg="#FF9800")
            self._update_controls_state()
        except Exception:
            pass

    def _upload_firmware(self):
        """Compile and upload the selected .ino to the selected Mega COM port."""
        if self.machine_started:
            messagebox.showwarning(
                "Firmware Upload",
                "Stop the machine before uploading firmware.",
            )
            return

        firmware_path = self.firmware_path_var.get().strip()
        port = self.serial_port_var.get().strip()

        if not firmware_path or not os.path.isfile(firmware_path):
            messagebox.showwarning("Firmware Upload", "Select a valid .ino file first.")
            return
        if not firmware_path.lower().endswith(".ino"):
            messagebox.showwarning("Firmware Upload", "The selected file must be an .ino sketch.")
            return
        if not port:
            messagebox.showwarning("Firmware Upload", "Select the Arduino COM port first.")
            return

        sketch_dir = os.path.dirname(firmware_path)
        sketch_name = os.path.splitext(os.path.basename(firmware_path))[0]
        if os.path.basename(sketch_dir).lower() != sketch_name.lower():
            messagebox.showerror(
                "Firmware Upload",
                "Arduino requires the sketch folder to match the .ino filename.\n\n"
                f"Expected folder: {sketch_name}\n"
                "Select the .ino from its normal Arduino sketch folder.",
            )
            return

        cli = self._find_arduino_cli()
        if not cli:
            messagebox.showerror(
                "Firmware Upload",
                "arduino-cli was not found.\n\n"
                "Install Arduino CLI or Arduino IDE 2.x on this machine, then try again.",
            )
            return

        self._release_serial_for_firmware_upload()
        self.firmware_upload_btn.configure(text="Uploading…", state=tk.DISABLED)
        self.firmware_browse_btn.configure(state=tk.DISABLED)
        self.log_status(f"Compiling {os.path.basename(firmware_path)} for Arduino Mega...")

        def worker():
            try:
                compile_result = subprocess.run(
                    [cli, "compile", "--fqbn", _ARDUINO_FQBN, sketch_dir],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if compile_result.returncode != 0:
                    details = (compile_result.stderr or compile_result.stdout or "Compile failed").strip()
                    raise RuntimeError(f"Compile failed:\n{details[-1800:]}")

                self.root.after(0, lambda: self.log_status("✓ Firmware compile successful"))

                upload_result = subprocess.run(
                    [
                        cli,
                        "upload",
                        "-p",
                        port,
                        "--fqbn",
                        _ARDUINO_FQBN,
                        sketch_dir,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if upload_result.returncode != 0:
                    details = (upload_result.stderr or upload_result.stdout or "Upload failed").strip()
                    raise RuntimeError(f"Upload failed:\n{details[-1800:]}")

                self.root.after(0, self._firmware_upload_success)
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): self._firmware_upload_failed(err))

        threading.Thread(target=worker, daemon=True).start()

    def _firmware_upload_success(self):
        """Restore firmware-loader controls after a successful upload."""
        self.firmware_upload_btn.configure(text="Upload Firmware", state=tk.NORMAL)
        self.firmware_browse_btn.configure(state=tk.NORMAL)
        self.log_status("✓ Firmware uploaded successfully")
        self.root.after(1200, lambda: self._refresh_serial_ports(log_result=False))
        messagebox.showinfo(
            "Firmware Upload",
            "Firmware uploaded successfully.\n\n"
            "Allow the Arduino to reboot, then tap Connect.",
        )

    def _firmware_upload_failed(self, error_text):
        """Restore firmware-loader controls and report a failed upload."""
        self.firmware_upload_btn.configure(text="Upload Firmware", state=tk.NORMAL)
        self.firmware_browse_btn.configure(state=tk.NORMAL)
        self.log_status(error_text, error=True)
        messagebox.showerror("Firmware Upload", error_text)

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

    def stop_machine(self):
        """Send StopMachine immediately, even while a sort owns the serial lock."""
        serial_obj = None
        try:
            if self.scanner:
                serial_obj = getattr(self.scanner, "ser", None)
        except Exception:
            serial_obj = None

        if serial_obj is None:
            return super().stop_machine()

        try:
            # Do not use scanner.send_to_arduino() here.  A running Sort command
            # holds its transaction lock while waiting for the physical cycle to
            # finish.  Writing directly lets the firmware see StopMachine now.
            serial_obj.write(b"StopMachine")
            serial_obj.flush()

            self.machine_started = False
            self.machine_state_label.configure(text="● STOPPED", fg="#ff4444")
            self.start_machine_btn.configure(state=tk.NORMAL)
            self.stop_machine_btn.configure(state=tk.DISABLED)
            self._update_controls_state()
            self.log_status("✓ EMERGENCY STOP sent to Arduino")
        except Exception as exc:
            self.log_status(f"✗ Emergency stop failed: {exc}", error=True)
            self.log_error("E405", f"StopMachine:{exc}")

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

        # Sort and homing commands can involve long physical motion. Keep their
        # transaction open until the Arduino reports completion so the GUI does
        # not falsely report a timeout while the machine is still moving.
        serial_obj = None
        old_timeout = None
        if wire_cmd.startswith("Sort,") or wire_cmd in ("StartMachine", "HomeButton"):
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
