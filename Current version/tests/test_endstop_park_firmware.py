"""Regression guard: LOAD CARDS may only seek the *physical* limit switches.

These static checks complement real Arduino compilation and an empty-hopper
mechanical test; they do not certify actual motion or endstop wiring.
"""
from pathlib import Path
import re
import unittest

source = (Path(__file__).resolve().parents[1] / 'Main' / 'Main.ino').read_text(encoding='utf-8')


def section(start, end):
    return source[source.index(start):source.index(end, source.index(start) + len(start))]


class EndstopParkingSafety(unittest.TestCase):
    def test_park_seeks_z_up_before_both_xy_endstops(self):
        body = section('void ParkForLoading() {', '\nvoid ReturnFromLoading() {')
        z = body.index('digitalRead(Zmin)')
        y = body.index('digitalRead(Ymin)')
        x = body.index('digitalRead(Xmax)')
        self.assertLess(z, y)
        self.assertLess(y, x)
        self.assertIn('Move1(1, 3, zespeed)', body)
        self.assertIn('Move4(0, 1, 0, 5)', body)
        self.assertIn('Move4(0, 1, 3, 0)', body)
        self.assertIn('loadingParked = true;', body)

    def test_load_never_runs_pickup_drop_or_vacuum(self):
        body = section('void ParkForLoading() {', '\nvoid ReturnFromLoading() {')
        for forbidden in ['Tray(', 'pick(', 'ForLoop(', 'Move1(0,', 'Vacuum1, HIGH',
                          'releaseCardAtTravelHeight(', 'initial_pickup_distance']:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertIn('stopVacuumCarryPulse()', body)
        self.assertIn('digitalWrite(Vacuum2, LOW)', body)
        self.assertIn('Z_ENDSTOP_MIN != ENDSTOP_TRIGGERED', body)
        self.assertIn('X_ENDSTOP_MAX != ENDSTOP_TRIGGERED', body)
        self.assertIn('Y_ENDSTOP_MIN != ENDSTOP_TRIGGERED', body)

    def test_return_uses_homing_offsets_and_z_descends_last(self):
        body = section('void ReturnFromLoading() {', '\nvoid StopMachine() {')
        xy = body.index('Move4(1, 0, Xcal * 3 + 55, Ycal * 2 + 38)')
        z = body.index('Move1(0, PICKUP_Z_FROM_LIMIT_STEPS, zspeed)')
        ready = body.index('loadingReturnReady = true;')
        self.assertLess(xy, z)
        self.assertLess(z, ready)
        self.assertNotIn('Tray(', body)
        self.assertNotIn('pick(', body)
        self.assertNotIn('Vacuum1, HIGH', body)

    def test_firmware_version_lets_gui_block_previous_firmware(self):
        self.assertIn('#define FIRMWARE_ID             "MOSS-ENDSTOP-PARK-4"', source)
        self.assertIn('if (Tempval1 == "QueryFirmware")', source)
        self.assertIn('response += ",parkmode=endstops3"', source)
        self.assertIn('response += ",recover="', source)

    def test_unknown_sort_commands_no_longer_pickup_any_card(self):
        body = section('if (sortCommand) {', '\n  long manualSteps = 5;')
        self.assertIn('EC_UNKNOWN_CMD', body)
        self.assertNotIn('ForLoop(1, 34)', body)

    def test_homing_and_park_move_have_interrupt_checks(self):
        self.assertIn('machineStarted || loadingMotionActive', source)
        self.assertIn('checkEmergencyStop()', section('void Move4(', '\nvoid Move1CalibrationDown('))
        self.assertIn('checkEmergencyStop()', section('void Move1(boolean dir,', '\nvoid PrintLCD('))
        self.assertIn('loadingRecoveryRequired = true;', source)

    def test_no_downward_move_at_tray_release(self):
        body = section('boolean releaseCardAtTravelHeight()', '\nboolean pick(')
        self.assertNotIn('Move1(', body)


if __name__ == '__main__':
    unittest.main()
