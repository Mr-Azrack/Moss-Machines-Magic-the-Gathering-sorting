"""Exercise real serial command matching without importing camera/database packages."""
import ast
from pathlib import Path
import threading
import time
import unittest

path = Path(__file__).resolve().parents[1] / 'optimized_scanner.py'
tree = ast.parse(path.read_text(encoding='utf-8'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OptimizedCardScanner")
names = {'_expected_arduino_reply', 'send_to_arduino', 'recv_from_arduino',
         'wait_for_arduino', 'emergency_stop'}
methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(methods) == len(names)
module = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
namespace = {'time': time, '_elog': lambda code, detail='': code + detail}
exec(compile(module, str(path), 'exec'), namespace)


class FakeSerial:
    def __init__(self, response=b''):
        self.queue = bytearray()
        self.response = response
        self.writes = []
        self.is_open = True
    @property
    def in_waiting(self):
        return len(self.queue)
    def reset_input_buffer(self):
        self.queue.clear()
    def write(self, b):
        self.writes.append(bytes(b))
        self.queue.extend(self.response)
    def read(self, n):
        if not self.queue:
            return b''
        b = bytes(self.queue[:n])
        del self.queue[:n]
        return b
    def flush(self):
        pass


class Harness:
    _expected_arduino_reply = namespace['_expected_arduino_reply']
    send_to_arduino = namespace['send_to_arduino']
    recv_from_arduino = namespace['recv_from_arduino']
    emergency_stop = namespace['emergency_stop']

    def __init__(self, response=b''):
        self.ser = FakeSerial(response)
        self.start_marker = 60
        self.end_marker = 62
        self.serial_lock = threading.Lock()


class SerialProtocolTests(unittest.TestCase):
    def test_park_waits_for_real_park_ack_not_startup_ready(self):
        h = Harness(b'<Arduino is ready><OK,LoadingParked>')
        self.assertEqual(h.send_to_arduino('ParkForLoading'), 'OK,LoadingParked')
        self.assertEqual(h.ser.writes, [b'ParkForLoading'])

    def test_query_sensor_skips_stale_acks(self):
        h = Harness(b'<Arduino is ready><OK,LoadingReady><Sensors,home=1,parkmode=endstops3>')
        self.assertEqual(h.send_to_arduino('QuerySensors'),
                         'Sensors,home=1,parkmode=endstops3')

    def test_park_reports_firmware_error_not_fake_success(self):
        h = Harness(b'<Arduino is ready><Error,E517,X timeout>')
        self.assertEqual(h.send_to_arduino('ParkForLoading'), 'Error,E517,X timeout')

    def test_firmware_probe_requires_specific_identity_frame(self):
        h = Harness(b'<Arduino is ready><Firmware,MOSS-ENDSTOP-PARK-4>')
        self.assertEqual(h.send_to_arduino('QueryFirmware'),
                         'Firmware,MOSS-ENDSTOP-PARK-4')

    def test_emergency_stop_does_not_wait_on_command_lock(self):
        h = Harness()
        h.serial_lock.acquire()
        try:
            self.assertTrue(h.emergency_stop())
            self.assertEqual(h.ser.writes, [b'StopMachine'])
        finally:
            h.serial_lock.release()

    def test_short_read_without_ack_times_out(self):
        h = Harness()
        self.assertEqual(
            h.recv_from_arduino(timeout_seconds=0.01, expected_prefix='Sensors,',
                                accept_ready=False), '')


if __name__ == '__main__':
    unittest.main()
