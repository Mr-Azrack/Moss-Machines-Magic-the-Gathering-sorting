#!/usr/bin/env python3
"""
Persistent sorter GUI entry point.

Wraps sorter_gui_main.ScannerGUI without changing its working behavior and
adds automatic persistence for operator-editable GUI/session configuration.
Operational machine state is deliberately NOT restored after a restart.
"""

import json
import os
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np

from sorter_gui_main import ScannerGUI as BaseSorterGUI


_SETTINGS_VERSION = 4
_AUTOSAVE_MS = 500

# Current tuned motion defaults. Lower pulse-delay values mean faster motion.
_MOTION_DEFAULTS = {
    "speed": "500",
    "zspeed": "28",
    "zespeed": "36",
}

# These are live/operational states, not configuration. Restoring them after a
# power interruption could make the interface claim hardware is active when it
# is not, so they are intentionally excluded.
_EXCLUDED_VARIABLES = {
    "monitor_var",
    "_error_badge_var",
    "_ref_search_var",
}
_EXCLUDED_VARIABLE_DICTIONARIES = {
    "motor_vars",
}

# Some filter controls depend on the selected game. Restore these after the
# game has been restored and its available values have been repopulated.
_DEPENDENT_FILTERS = {
    "set_var",
    "rarity_var",
    "foil_var",
}

_SORT_LABELS = {
    "color": "Color",
    "mana_value": "Mana Value",
    "set": "Set",
    "price": "Price",
    "type": "Type",
    "buy": "Buy Mode",
    "alpha": "Alpha (A-Z)",
    "rarity": "Rarity",
    "finish": "Finish (Foil)",
}

# The physical pickup tray is itself a large four-sided shape, so the original
# contour detector can mistake the blank tray floor for a card. Require a small
# amount of printed detail inside the contour before treating it as a card.
_CARD_INTERIOR_MIN_EDGE_DENSITY = 0.008
_CARD_INTERIOR_ERODE_PX = 18


def _settings_path():
    """Return a local settings path that survives repo pulls and updates."""
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        root = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(root, "MossCardSorter")
    return os.path.join(folder, "session_settings.json")


class ScannerGUI(BaseSorterGUI):
    def __init__(self, root):
        self._session_settings_path = _settings_path()
        self._session_settings_loading = True
        self._last_saved_settings_text = None

        super().__init__(root)

        # The base sorter keeps its own defaults for backwards compatibility.
        # The persistent launcher is the normal application entry point, so set
        # the current tuned defaults here before any saved session is restored.
        self._apply_current_motion_defaults()

        self._install_card_presence_filter()
        self._install_confidence_gate()
        self._configure_confidence_control()
        self._apply_touch_sizing()
        self._restore_session_settings()
        self._session_settings_loading = False

        # Cache the restored state so the first autosave only writes if the
        # operator actually changes something.
        try:
            self._last_saved_settings_text = self._settings_text()
        except Exception:
            self._last_saved_settings_text = None

        self.root.after(_AUTOSAVE_MS, self._autosave_session_settings)

    def apply_compact_mode(self):
        """Preserve compact layout while keeping requested controls touch-sized."""
        result = super().apply_compact_mode()
        try:
            self._apply_touch_sizing()
            self._configure_confidence_control()
        except Exception:
            pass
        return result

    def _apply_current_motion_defaults(self):
        """Align the normal GUI defaults with the current firmware tuning."""
        try:
            for key, value in _MOTION_DEFAULTS.items():
                variable = self.param_vars.get(key)
                if variable is not None:
                    variable.set(value)
        except Exception:
            pass

    def _scanner_tab_widget(self):
        """Return the Scanner tab frame without depending on base private names."""
        try:
            tabs = self.notebook.tabs()
            if not tabs:
                return None
            return self.notebook.nametowidget(tabs[0])
        except Exception:
            return None

    @staticmethod
    def _walk_descendants(parent):
        if parent is None:
            return
        for child in parent.winfo_children():
            yield child
            yield from ScannerGUI._walk_descendants(child)

    @staticmethod
    def _add_vertical_ipad(widget, amount):
        """Increase a widget's allocated height without altering its width."""
        try:
            manager = widget.winfo_manager()
            if manager == "pack":
                info = widget.pack_info()
                current = int(info.get("ipady", 0) or 0)
                widget.pack_configure(ipady=max(current, amount))
            elif manager == "grid":
                info = widget.grid_info()
                current = int(info.get("ipady", 0) or 0)
                widget.grid_configure(ipady=max(current, amount))
        except Exception:
            pass

    def _apply_touch_sizing(self):
        """Double tab height and make Scanner-tab buttons/dropdowns touch friendly."""
        style = ttk.Style(self.root)

        # Top notebook tabs: roughly twice the previous vertical height while
        # preserving the existing labels and horizontal footprint as much as possible.
        style.configure("TNotebook.Tab", padding=(8, 10))

        # Scanner-tab-only ttk styles so Arduino/Export/etc. controls are not enlarged.
        style.configure("Scanner.TCombobox", padding=(6, 8), font=("Segoe UI", 11))
        style.configure("Scanner.TButton", padding=(8, 8), font=("Segoe UI", 10, "bold"))

        scanner_tab = self._scanner_tab_widget()
        if scanner_tab is None:
            return

        for widget in self._walk_descendants(scanner_tab):
            try:
                if isinstance(widget, tk.Button):
                    current = int(float(widget.cget("pady") or 0))
                    widget.configure(pady=max(8, current * 2))
                elif isinstance(widget, ttk.Button):
                    widget.configure(style="Scanner.TButton")
                elif isinstance(widget, ttk.Combobox):
                    widget.configure(style="Scanner.TCombobox")
                elif isinstance(widget, tk.Menubutton):
                    current = int(float(widget.cget("pady") or 0))
                    widget.configure(pady=max(8, current * 2))
                elif isinstance(widget, (tk.Spinbox, tk.Entry)):
                    self._add_vertical_ipad(widget, 5)
            except Exception:
                continue

    def _configure_confidence_control(self):
        """Make the existing threshold control explicitly represent 1-100% confidence."""
        scanner_tab = self._scanner_tab_widget()
        if scanner_tab is None:
            return

        try:
            threshold_var_name = str(self.match_threshold_var)
        except Exception:
            threshold_var_name = ""

        for widget in self._walk_descendants(scanner_tab):
            try:
                if isinstance(widget, tk.Label) and widget.cget("text") == "Match Threshold:":
                    widget.configure(text="Min Confidence (%):")
                elif isinstance(widget, tk.Spinbox):
                    if str(widget.cget("textvariable")) == threshold_var_name:
                        widget.configure(from_=1, to=100)
            except Exception:
                continue

    def _minimum_confidence(self):
        try:
            return max(1.0, min(100.0, float(self.match_threshold_var.get())))
        except Exception:
            return 90.0

    def _confidence_to_phash_distance(self, confidence):
        """Convert displayed confidence percent to the scanner's 0-256 pHash distance."""
        confidence = max(0.0, min(100.0, float(confidence)))
        return max(0, min(256, int(256.0 * (1.0 - confidence / 100.0))))

    def _passes_minimum_confidence(self, card_info):
        if not card_info:
            return False
        try:
            confidence = float(card_info.get("confidence", 0) or 0)
        except Exception:
            confidence = 0.0
        return confidence >= self._minimum_confidence()

    def _install_confidence_gate(self):
        """Reject recognition results below the GUI's actual minimum confidence."""
        scanner = getattr(self, "scanner", None)
        if scanner is not None:
            original_process = getattr(scanner, "_process_card_from_contour", None)
            if callable(original_process):
                def gated_process(frame, card_approx):
                    # The scanner's threshold is a Hamming-distance ceiling, not
                    # a percentage. Convert the user's percentage before every
                    # scan so 90 really means approximately 90% or better.
                    scanner.scan_threshold = self._confidence_to_phash_distance(
                        self._minimum_confidence()
                    )
                    card_info = original_process(frame, card_approx)
                    if not self._passes_minimum_confidence(card_info):
                        return None
                    return card_info

                scanner._process_card_from_contour = gated_process

        # If an optional recognition plugin is installed, apply the same final
        # confidence gate there so it cannot bypass the GUI setting.
        plugin = getattr(self, "recognition_plugin", None)
        if plugin is not None:
            original_recognize = getattr(plugin, "recognize", None)
            if callable(original_recognize):
                def gated_recognize(*args, **kwargs):
                    card_info = original_recognize(*args, **kwargs)
                    if not self._passes_minimum_confidence(card_info):
                        return None
                    return card_info

                plugin.recognize = gated_recognize

    def _install_card_presence_filter(self):
        """Reject blank tray-floor rectangles before recognition starts."""
        scanner = getattr(self, "scanner", None)
        if scanner is None:
            return

        original_detector = getattr(scanner, "_find_card_contour", None)
        if not callable(original_detector):
            return

        def filtered_detector(frame):
            card_approx = original_detector(frame)
            if card_approx is None:
                return None
            if self._contour_has_card_detail(frame, card_approx):
                return card_approx
            return None

        scanner._find_card_contour = filtered_detector

    @staticmethod
    def _contour_has_card_detail(frame, card_approx):
        """Return True when the interior contains normal printed-card detail."""
        try:
            if frame is None or card_approx is None:
                return False

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mask = np.zeros(gray.shape, dtype=np.uint8)
            polygon = np.asarray(card_approx, dtype=np.int32).reshape(-1, 2)
            cv2.fillConvexPoly(mask, polygon, 255)

            # Ignore the card/tray border itself. The tray floor can have a
            # perfect rectangular outline, but its center is nearly featureless.
            kernel_size = (_CARD_INTERIOR_ERODE_PX * 2) + 1
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (kernel_size, kernel_size),
            )
            interior_mask = cv2.erode(mask, kernel, iterations=1)
            interior_pixels = cv2.countNonZero(interior_mask)
            if interior_pixels < 1000:
                return False

            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            edges = cv2.Canny(blurred, 45, 135)
            interior_edges = cv2.bitwise_and(edges, edges, mask=interior_mask)
            edge_pixels = cv2.countNonZero(interior_edges)
            edge_density = edge_pixels / float(interior_pixels)

            return edge_density >= _CARD_INTERIOR_MIN_EDGE_DENSITY
        except Exception:
            # If the extra guard itself ever fails, preserve the existing
            # detector behavior rather than disabling scanning entirely.
            return True

    def _persistable_variables(self):
        """Return direct Tk variables that represent operator configuration."""
        result = {}
        for name, value in vars(self).items():
            if name in _EXCLUDED_VARIABLES:
                continue
            if name.startswith("_"):
                continue
            if isinstance(value, tk.Variable):
                try:
                    result[name] = value.get()
                except Exception:
                    pass
        return result

    def _persistable_variable_dicts(self):
        """Return dictionaries such as param_vars that contain Tk variables."""
        result = {}
        for name, value in vars(self).items():
            if name in _EXCLUDED_VARIABLE_DICTIONARIES:
                continue
            if name.startswith("_"):
                continue
            if not isinstance(value, dict):
                continue

            saved = {}
            for key, subvalue in value.items():
                if isinstance(subvalue, tk.Variable):
                    try:
                        saved[str(key)] = subvalue.get()
                    except Exception:
                        pass
            if saved:
                result[name] = saved
        return result

    def _collect_session_settings(self):
        return {
            "version": _SETTINGS_VERSION,
            "variables": self._persistable_variables(),
            "variable_dicts": self._persistable_variable_dicts(),
        }

    def _settings_text(self):
        return json.dumps(
            self._collect_session_settings(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    def _save_session_settings(self, force=False):
        """Atomically save configuration only when it has changed."""
        if self._session_settings_loading and not force:
            return

        try:
            text = self._settings_text()
            if not force and text == self._last_saved_settings_text:
                return

            folder = os.path.dirname(self._session_settings_path)
            os.makedirs(folder, exist_ok=True)

            temp_path = self._session_settings_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except Exception:
                    pass

            os.replace(temp_path, self._session_settings_path)
            self._last_saved_settings_text = text
        except Exception as exc:
            try:
                self.log_status(f"Session settings save failed: {exc}", error=True)
            except Exception:
                pass

    def _autosave_session_settings(self):
        try:
            self._save_session_settings()
        finally:
            try:
                self.root.after(_AUTOSAVE_MS, self._autosave_session_settings)
            except Exception:
                pass

    @staticmethod
    def _set_if_valid(variable, value):
        try:
            variable.set(value)
            return True
        except Exception:
            return False

    def _restore_combo_value_if_available(self, variable_name, combo_name, value):
        """Restore a dependent filter only when the current combo supports it."""
        variable = getattr(self, variable_name, None)
        combo = getattr(self, combo_name, None)
        if variable is None:
            return

        if combo is None:
            self._set_if_valid(variable, value)
            return

        try:
            values = list(combo.cget("values"))
        except Exception:
            values = []

        if not values or str(value) in [str(item) for item in values]:
            self._set_if_valid(variable, value)

    def _restore_session_settings(self):
        """Restore saved operator configuration without restoring machine state."""
        try:
            if not os.path.isfile(self._session_settings_path):
                return

            with open(self._session_settings_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)

            saved_version = int(saved.get("version", 1) or 1)
            variables = saved.get("variables", {})
            variable_dicts = saved.get("variable_dicts", {})

            # Motion-default migrations only touch the exact known prior
            # defaults. Any operator-customized speed remains untouched.
            params = variable_dicts.get("param_vars")
            if isinstance(params, dict):
                if saved_version < 2:
                    first_motion_migration = {
                        "speed": ("700", "650"),
                        "zspeed": ("40", "38"),
                        "zespeed": ("55", "52"),
                    }
                    for key, (old_value, new_value) in first_motion_migration.items():
                        if str(params.get(key, "")) == old_value:
                            params[key] = new_value

                if saved_version < 3:
                    second_motion_migration = {
                        "speed": ("650", "600"),
                        "zspeed": ("38", "36"),
                        "zespeed": ("52", "50"),
                    }
                    for key, (old_value, new_value) in second_motion_migration.items():
                        if str(params.get(key, "")) == old_value:
                            params[key] = new_value

                if saved_version < 4:
                    third_motion_migration = {
                        "speed": ("600", "500"),
                        "zspeed": ("36", "28"),
                        "zespeed": ("50", "36"),
                    }
                    for key, (old_value, new_value) in third_motion_migration.items():
                        if str(params.get(key, "")) == old_value:
                            params[key] = new_value

            # Preserve dependent filters until after game selection has rebuilt
            # the Set/Rarity/Foil choices.
            dependent_values = {
                name: variables.get(name)
                for name in _DEPENDENT_FILTERS
                if name in variables
            }

            # Restore all direct variables except dependent filters.
            for name, value in variables.items():
                if name in _DEPENDENT_FILTERS or name in _EXCLUDED_VARIABLES:
                    continue
                variable = getattr(self, name, None)
                if isinstance(variable, tk.Variable):
                    self._set_if_valid(variable, value)

            # Restore dictionaries of configurable variables. param_vars is the
            # important one today, and this also picks up future GUI config
            # dictionaries automatically without persisting motor relay state.
            for dict_name, saved_values in variable_dicts.items():
                if dict_name in _EXCLUDED_VARIABLE_DICTIONARIES:
                    continue
                current_dict = getattr(self, dict_name, None)
                if not isinstance(current_dict, dict):
                    continue
                for key, value in saved_values.items():
                    variable = current_dict.get(key)
                    if isinstance(variable, tk.Variable):
                        self._set_if_valid(variable, value)

            # Rebuild the selected game's dependent filter choices before
            # restoring Set/Rarity/Foil.
            try:
                if hasattr(self, "game_var") and self.game_var.get():
                    self.on_game_change()
            except Exception:
                pass

            self._restore_combo_value_if_available(
                "set_var", "set_combo", dependent_values.get("set_var")
            ) if "set_var" in dependent_values else None
            self._restore_combo_value_if_available(
                "rarity_var", "rarity_combo", dependent_values.get("rarity_var")
            ) if "rarity_var" in dependent_values else None
            self._restore_combo_value_if_available(
                "foil_var", "foil_combo", dependent_values.get("foil_var")
            ) if "foil_var" in dependent_values else None

            # Keep controls whose display is not directly bound to the saved
            # Tk variable visually synchronized with the restored value.
            try:
                sort_key = self.sorting_var.get()
                label = _SORT_LABELS.get(sort_key)
                if label and hasattr(self, "sorting_combo"):
                    self.sorting_combo.set(label)
            except Exception:
                pass

            try:
                if hasattr(self, "firmware_path_var") and hasattr(self, "firmware_name_label"):
                    firmware_path = self.firmware_path_var.get().strip()
                    self.firmware_name_label.configure(
                        text=os.path.basename(firmware_path) if firmware_path else "No .ino selected"
                    )
            except Exception:
                pass

            # Apply restored settings that have associated runtime handlers.
            try:
                self.auto_scan = bool(self.auto_scan_var.get())
                self.toggle_auto_scan()
            except Exception:
                pass

            try:
                self.update_vector_backend()
            except Exception:
                pass

            try:
                self.toggle_inventory()
            except Exception:
                pass

            try:
                self.apply_compact_mode()
            except Exception:
                pass

            try:
                self.log_status("✓ Previous session settings restored")
            except Exception:
                pass

        except Exception as exc:
            try:
                self.log_status(f"Session settings restore failed: {exc}", error=True)
            except Exception:
                pass

    def on_close(self):
        """Save the final configuration before the existing cleanup runs."""
        try:
            self._save_session_settings(force=True)
        except Exception:
            pass
        return super().on_close()


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
