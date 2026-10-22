"""Run preview and compatible batch regressions with network/state guards.

Desktop hotkey suites require evdev and are deliberately outside this runner.
No real provider client or user state should be needed by any selected test.
"""
import pathlib
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

connection_attempts = []


def forbidden(*args, **kwargs):
    connection_attempts.append(True)
    raise AssertionError('All network access is forbidden in offline validation')

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix="protocol7-offline-", dir="/tmp") as state, \
         mock.patch.object(socket.socket, 'connect', forbidden), \
         mock.patch.object(socket.socket, 'connect_ex', forbidden), \
         mock.patch.object(socket, 'create_connection', forbidden), \
         mock.patch.object(socket.socket, 'sendto', forbidden), \
         mock.patch.object(socket.socket, 'send', forbidden), \
         mock.patch.object(socket.socket, 'sendall', forbidden), \
         mock.patch.object(pathlib.Path, 'home', return_value=pathlib.Path(state)):
        import groq
        sdk_guard = mock.patch.object(groq.resources.audio.transcriptions.Transcriptions, 'create', forbidden)
        sdk_guard.start()
        import config
        config.CONFIG_DIR = state
        config.CONFIG_FILE = str(pathlib.Path(state) / 'config.json')
        config.HISTORY_FILE = str(pathlib.Path(state) / 'history.json')
        modules = ['tests.test_groq_chunks', 'tests.test_streaming_stt',
                   'tests.test_streaming_integration', 'tests.test_streaming_plugin_config',
                   'tests.test_preview_corrections', 'tests.test_release_corrections', 'tests.test_review_round1', 'tests.test_ghost_jobs',
                   'tests.test_cloud_dictation', 'tests.test_groq_latency',
                   'tests.test_numeric_dictation', 'tests.test_queue_send',
                   'tests.test_enter_intercept']
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(f"Offline guards: {len(connection_attempts)} forbidden network/SDK attempts")
        if connection_attempts:
            print(f"ERROR: {len(connection_attempts)} blocked network attempts", file=sys.stderr)
        sys.exit(not result.wasSuccessful() or bool(connection_attempts))
