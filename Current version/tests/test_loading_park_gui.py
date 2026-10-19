"""Headless tests for the GUI's firmware-authoritative loading interlocks.

Extract only the small GUI state methods: importing the complete GUI launches
hardware/database dependency initialization, which is inappropriate for CI.
"""
import ast
import pathlib
import types
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "gui_interface_enhanced.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ScannerGUI")
needed = {"_apply_loading_sensor_status", "_update_controls_state"}
methods = [n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name in needed]
assert len(methods) == len(needed), "Expected loading state functions missing"
mod = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
namespace = {"tk": types.SimpleNamespace(NORMAL="normal", DISABLED="disabled")}
exec(compile(mod, str(SOURCE), "exec"), namespace)


class FakeWidget:
    def __init__(self):
        self.options = {}
    def configure(self, **kwargs):
        self.options.update(kwargs)
    def state(self):
        return self.options.get("state")


class GuiHarness:
    _apply_loading_sensor_status = namespace["_apply_loading_sensor_status"]
    _update_controls_state = namespace["_update_controls_state"]

    def __init__(self):
        self.machine_started = False
        self.machine_parked_for_loading = False
        self.loading_return_ready = False
        self._loading_motion_active = False
        self._loading_position_uncertain = False
        self._firmware_verified = True
        self._loading_recovery_required = False
        self.sensor_data = {}
        self._scan_in_progress = False
        self.home_btn = FakeWidget()
        self.motor_checkboxes = {"x": FakeWidget()}
        self.movement_buttons = [FakeWidget()]
        self.upload_params_btn = FakeWidget()
        self.fetch_params_btn = FakeWidget()
        self.load_cards_btn = FakeWidget()
        self.camera_load_btn = FakeWidget()
        self.return_from_loading_btn = FakeWidget()
        self.camera_return_btn = FakeWidget()
        self.start_machine_btn = FakeWidget()
        self.stop_machine_btn = FakeWidget()
        self.machine_state_label = FakeWidget()
        self.loading_hint_label = FakeWidget()

    def _arduino_connected(self):
        return True


class LoadingParkGuiTests(unittest.TestCase):
    def test_park_disables_start_home_jog_enables_return(self):
        gui = GuiHarness()
        self.assertTrue(gui._apply_loading_sensor_status(
            "Sensors,home=0,parked=1,loadready=0,started=0,parkmode=endstops3,recover=0,xmin=1,xmax=0,ymin=0,zmin=0"))
        self.assertEqual(gui.start_machine_btn.state(), "disabled")
        self.assertEqual(gui.home_btn.state(), "disabled")
        self.assertEqual(gui.movement_buttons[0].state(), "disabled")
        self.assertEqual(gui.camera_load_btn.state(), "disabled")
        self.assertEqual(gui.camera_return_btn.state(), "normal")
        self.assertIn("PARKED AT ENDSTOPS", gui.machine_state_label.options["text"])

    def test_return_allows_start_and_park(self):
        gui = GuiHarness()
        self.assertTrue(gui._apply_loading_sensor_status(
            "<Sensors,home=1,parked=0,loadready=1,started=0,parkmode=endstops3,recover=0,xmin=1,xmax=1,ymin=1,zmin=1>"))
        self.assertEqual(gui.start_machine_btn.state(), "normal")
        self.assertEqual(gui.camera_return_btn.state(), "disabled")
        self.assertEqual(gui.camera_load_btn.state(), "normal")
        self.assertIn("READY TO START", gui.machine_state_label.options["text"])

    def test_started_disables_loading_and_manual(self):
        gui = GuiHarness()
        gui._apply_loading_sensor_status(
            "Sensors,home=1,parked=0,loadready=0,started=1,parkmode=endstops3,recover=0,xmin=1,xmax=1,ymin=1,zmin=1")
        self.assertEqual(gui.start_machine_btn.state(), "disabled")
        self.assertEqual(gui.stop_machine_btn.state(), "normal")
        self.assertEqual(gui.camera_load_btn.state(), "disabled")

    def test_uncertain_position_never_enables_start(self):
        gui = GuiHarness()
        gui._loading_position_uncertain = True
        gui._update_controls_state()
        for w in [gui.start_machine_btn, gui.home_btn, gui.camera_load_btn,
                  gui.camera_return_btn]:
            self.assertEqual(w.state(), "disabled")

    def test_old_grid_park_protocol_rejected(self):
        gui = GuiHarness()
        self.assertFalse(gui._apply_loading_sensor_status(
            "Sensors,home=0,parked=1,loadready=0,started=0,"
            "parkmode=grid,recover=0,xmin=1,xmax=1,ymin=1,zmin=1"))
        self.assertFalse(gui.machine_parked_for_loading)

    def test_park_cannot_claim_endstops_without_switches(self):
        gui = GuiHarness()
        self.assertFalse(gui._apply_loading_sensor_status(
            "Sensors,home=0,parked=1,loadready=0,started=0,"
            "parkmode=endstops3,recover=0,xmin=1,xmax=1,ymin=0,zmin=0"))
        self.assertFalse(gui.machine_parked_for_loading)

    def test_recovery_allows_only_home(self):
        gui = GuiHarness()
        self.assertTrue(gui._apply_loading_sensor_status(
            "Sensors,home=0,parked=0,loadready=0,started=0,"
            "parkmode=endstops3,recover=1,xmin=1,xmax=1,ymin=1,zmin=1"))
        self.assertEqual(gui.home_btn.state(), "normal")
        self.assertEqual(gui.start_machine_btn.state(), "disabled")
        self.assertEqual(gui.camera_load_btn.state(), "disabled")
        self.assertIn("RECOVERY REQUIRED", gui.machine_state_label.options["text"])

    def test_unverified_firmware_rejects_valid_sensor_frame(self):
        gui = GuiHarness()
        gui._firmware_verified = False
        self.assertFalse(gui._apply_loading_sensor_status(
            "Sensors,home=1,parked=0,loadready=0,started=0,"
            "parkmode=endstops3,recover=0,xmin=1,xmax=1,ymin=1,zmin=1"))

    def test_sensor_protocol_must_include_home_park_and_started(self):
        gui = GuiHarness()
        self.assertFalse(gui._apply_loading_sensor_status("Sensors,started=0,home=1"))
        self.assertFalse(gui._apply_loading_sensor_status("OK,MachineStarted"))
        self.assertFalse(gui.machine_parked_for_loading)


if __name__ == "__main__":
    unittest.main()
