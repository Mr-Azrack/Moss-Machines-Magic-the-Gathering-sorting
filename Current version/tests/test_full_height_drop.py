"""Source regression checks for the sorter Z=0 card-release path.

These complement Arduino Mega compilation; actual mechanics/card landing
still require physical validation with the machine.
"""
from pathlib import Path
import unittest

source = (Path(__file__).resolve().parents[1] / 'Main' / 'Main.ino').read_text(encoding='utf-8')


def body_between(start_token, end_token):
    first = source.index(start_token)
    second = source.index(end_token, first + len(start_token))
    return source[first:second]


class FullHeightDropTests(unittest.TestCase):
    def test_release_never_moves_z(self):
        release = body_between('boolean releaseCardAtTravelHeight()', '\nboolean pick(')
        self.assertNotIn('Move1(', release)
        self.assertNotIn('dropSteps', release)
        self.assertIn('zPositionSteps != 0', release)
        self.assertIn('StopMachine()', release)

    def test_existing_release_sequence_preserved(self):
        release = body_between('boolean releaseCardAtTravelHeight()', '\nboolean pick(')
        actions = [
            'stopVacuumCarryPulse()',
            'delayWithStop(100)',
            'delayWithStop(DROP_SETTLE_MS)',
        ]
        last = -1
        for action in actions:
            with self.subTest(action=action):
                pos = release.index(action)
                self.assertGreater(pos, last)
                last = pos
        self.assertNotIn('MotorsOnOff(', release)

    def test_pickup_retains_normal_depth_control(self):
        pickup = body_between('boolean pick(long steps)', '\nvoid Homemachine()')
        self.assertIn('Move1(0, steps, zspeed)', pickup)
        self.assertIn('Move1(1, steps, zespeed)', pickup)
        self.assertIn('ReadRange(3)', pickup)

    def test_raise_before_xy_and_release_only_at_height(self):
        tray = body_between('void Tray(short var)', '\nvoid Move4(')
        lift = tray.index('Move1(1, CARRY_CLEARANCE_STEPS, zespeed)')
        height_check = tray.index('if (zPositionSteps != 0)', lift)
        x_out = tray.index(
            'Move4(moveToDirectionX, moveToDirectionY, absX, 0)', height_check)
        y_out = tray.index(
            'Move4(moveToDirectionX, moveToDirectionY, 0, absY)', x_out)
        release = tray.index('releaseCardAtTravelHeight()', y_out)
        x_return = tray.index(
            'Move4(moveBackDirectionX, moveBackDirectionY, absX, 0)', release)
        y_return = tray.index(
            'Move4(moveBackDirectionX, moveBackDirectionY, 0, absY)', x_return)
        self.assertLess(lift, height_check)
        self.assertLess(height_check, x_out)
        self.assertLess(x_out, y_out)
        self.assertLess(y_out, release)
        self.assertLess(release, x_return)
        self.assertLess(x_return, y_return)
        self.assertNotIn('Move1(', tray[x_out:])

    def test_obsolete_drop_distance_removed(self):
        self.assertNotIn('initial_drop_distance', source)
        self.assertNotIn('pick(dropSteps', source)
        self.assertIn('EC_Z_TRAVEL_HEIGHT', source)


if __name__ == '__main__':
    unittest.main()
