"""Prevent START from ever invoking HOME or issuing axis motion.

Only a separate HomeButton or ReturnFromLoading may establish the pickup
coordinate. This prevents START from descending into an already loaded stack.
Static regression tests complement the Arduino Mega compile and hardware tests.
"""
from pathlib import Path
import unittest

source = (Path(__file__).resolve().parents[1] / "Main" / "Main.ino").read_text(encoding="utf-8")
begin = source.index('  if (Tempval1 == "StartMachine") {')
end = source.index('  } else if (Tempval1 == "StopMachine") {', begin)
start_block = source[begin:end]


class StartDoesNotHomeTests(unittest.TestCase):
    def test_start_does_not_home_or_move(self):
        forbidden = [
            'Homemachine(', 'Move1(', 'Move4(', 'Tray(', 'pick(',
            'ForLoop(', 'ParkForLoading(', 'ReturnFromLoading(',
            'digitalWrite(', 'startVacuumCarryPulse(',
        ]
        for command in forbidden:
            with self.subTest(command=command):
                self.assertNotIn(command, start_block)

    def test_home_and_z_validated_before_arming(self):
        checks = [
            'if (loadingParked || loadingMotionActive || loadingRecoveryRequired)',
            'if (!atHomePosition)',
            'if (zPositionSteps != 0)',
            'machineStarted = true;',
            'Serial.println("<OK,MachineStarted>");',
        ]
        offsets = [start_block.index(x) for x in checks]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('EC_NOT_AT_HOME', start_block)
        self.assertIn('EC_Z_TRAVEL_HEIGHT', start_block)
        self.assertIn('EC_LOADING_POSITION', start_block)

    def test_home_remains_explicit_separate_command(self):
        self.assertIn('else if (Tempval1 == "HomeButton") {Homemachine();', source)
        self.assertIn('void Homemachine() {', source)
        self.assertIn('loadingReturnReady = true;', source)

    def test_start_does_not_require_immediately_preceding_return(self):
        self.assertNotIn('if (!(loadingReturnReady', start_block)
        self.assertNotIn('if (loadingReturnReady', start_block)

    def test_start_remains_off_on_rejected_position(self):
        # All rejection branches precede machineStarted=true.  There must
        # not be a second implicit start path inside this command block.
        self.assertEqual(start_block.count('machineStarted = true;'), 1)
        self.assertGreaterEqual(start_block.count('return;'), 4)


if __name__ == '__main__':
    unittest.main()
