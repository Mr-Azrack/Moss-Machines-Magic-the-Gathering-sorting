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

from sorter_gui_main import ScannerGUI as BaseSorterGUI


_SETTINGS_VERSION = 2
_AUTOSAVE_MS = 500

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

        self._restore_session_settings()
        self._session_settings_loading = False

        # Cache the restored state so the first autosave only writes if the
        # operator actually changes something.
        try:
            self._last_saved_settings_text = self._settings_text()
        except Exception:
            self._last_saved_settings_text = None

        self.root.after(_AUTOSAVE_MS, self._autosave_session_settings)

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

            # Version 2 raises travel speed slightly. Only migrate fields that
            # still contain the exact old known defaults; operator-customized
            # values are left alone.
            if saved_version < 2:
                params = variable_dicts.get("param_vars")
                if isinstance(params, dict):
                    motion_default_migration = {
                        "speed": ("700", "650"),
                        "zspeed": ("40", "38"),
                        "zespeed": ("55", "52"),
                    }
                    for key, (old_value, new_value) in motion_default_migration.items():
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
