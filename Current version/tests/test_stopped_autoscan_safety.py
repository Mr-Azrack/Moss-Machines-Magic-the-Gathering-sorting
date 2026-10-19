"""Regression tests: stopped/loading machine must never auto-scan or sort.

The camera UI depends on OpenCV/Tk hardware; test the pure GUI state gate
directly and verify its use at entry, pre-inventory, and tray/reject dispatch.
"""
import ast
from pathlib import Path
import types
import unittest

source = (Path(__file__).resolve().parents[1] / 'gui_interface_enhanced.py').read_text(encoding='utf-8')
tree = ast.parse(source)
gui = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScannerGUI')
meth = next(n for n in gui.body if isinstance(n, ast.FunctionDef)
            and n.name == '_sort_cycle_allowed_for_camera')
module = ast.fix_missing_locations(ast.Module(body=[meth], type_ignores=[]))
namespace = {}
exec(compile(module, '<scanner guard>', 'exec'), namespace)


class State:
    _sort_cycle_allowed_for_camera = namespace['_sort_cycle_allowed_for_camera']

    def __init__(self, connected=True):
        self._connected = connected
        self._firmware_verified = True
        self.machine_started = False
        self.machine_parked_for_loading = False
        self._loading_motion_active = False
        self._loading_position_uncertain = False
        self._loading_recovery_required = False
        self.sensor_data = {'home': 1}

    def _arduino_connected(self):
        return self._connected


class CameraSortSafety(unittest.TestCase):
    def test_stopped_machine_never_auto_scans(self):
        self.assertFalse(State()._sort_cycle_allowed_for_camera())

    def test_running_verified_homed_can_scan(self):
        g = State()
        g.machine_started = True
        self.assertTrue(g._sort_cycle_allowed_for_camera())

    def test_load_park_blocks_even_if_started_flag_stale(self):
        g = State()
        g.machine_started = True
        g.machine_parked_for_loading = True
        self.assertFalse(g._sort_cycle_allowed_for_camera())
        g.machine_parked_for_loading = False
        g._loading_motion_active = True
        self.assertFalse(g._sort_cycle_allowed_for_camera())

    def test_unknown_home_firmware_or_recovery_blocks(self):
        for key, val in [('_firmware_verified', False),
                         ('_loading_position_uncertain', True),
                         ('_loading_recovery_required', True)]:
            g = State()
            g.machine_started = True
            setattr(g, key, val)
            with self.subTest(key=key):
                self.assertFalse(g._sort_cycle_allowed_for_camera())
        g = State()
        g.machine_started = True
        g.sensor_data['home'] = 0
        self.assertFalse(g._sort_cycle_allowed_for_camera())

    def test_offline_recognition_still_supported(self):
        self.assertTrue(State(connected=False)._sort_cycle_allowed_for_camera())

    def test_video_loop_requires_sort_gate_before_auto_scan(self):
        loop = next(n for n in gui.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_video_loop')
        camera_gate_calls = [n for n in ast.walk(loop) if isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute)
                             and n.func.attr == '_sort_cycle_allowed_for_camera']
        self.assertTrue(camera_gate_calls)

    def test_scan_entry_and_dispatch_check_live_machine_state(self):
        scan = next(n for n in gui.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_perform_scan')
        calls = [n for n in ast.walk(scan) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == '_sort_cycle_allowed_for_camera']
        self.assertGreaterEqual(len(calls), 5)
        text = ast.get_source_segment(source, scan)
        self.assertLess(text.index('if not self._sort_cycle_allowed_for_camera()'),
                        text.index('self.scanner._process_card_from_contour'))
        self.assertLess(text.rfind('if not self._sort_cycle_allowed_for_camera()'),
                        text.rfind('self._send_arduino(bin_result)'))

    def test_firmware_declares_active_low_pullups_and_park_sanity(self):
        code = (Path(__file__).resolve().parents[1] / 'Main' / 'Main.ino').read_text(encoding='utf-8')
        self.assertIn('pinMode(pin, INPUT_PULLUP)', code)
        park = code[code.index('void ParkForLoading() {'):code.index('void ReturnFromLoading() {')]
        self.assertIn('Switch active at home', park)
        self.assertIn('Z limit not reached', park)
        self.assertIn('Y limit not reached', park)
        self.assertIn('X limit not reached', park)
        self.assertNotIn('Tray(', park)
        self.assertNotIn('pick(', park)


if __name__ == '__main__':
    unittest.main()
