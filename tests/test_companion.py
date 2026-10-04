import json
import os
import socket
import tempfile
import threading
import unittest

from companion.wake_word import normalize_text, wake_match_score

class WakeWordTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_text('Hey, TABBY!'),'hey tabby')

    def test_exact_and_alias_match(self):
        self.assertEqual(wake_match_score('hey tabby'),1.0)
        self.assertEqual(wake_match_score('Hey Tabi'),1.0)
        self.assertEqual(wake_match_score('okay hey tabby can you open this'),1.0)

    def test_unrelated_speech_is_rejected(self):
        self.assertLess(wake_match_score('maybe happy today'),0.84)
        self.assertLess(wake_match_score('tabby'),0.84)

    def test_uses_injected_protocol7_transcriber(self):
        import numpy as np
        from companion.wake_word import WakeWordDetector
        class FakeTranscriber:
            def __init__(self): self.calls=[]
            def transcribe(self,audio,live=False,allow_local_fallback=True):
                self.calls.append((len(audio),live,allow_local_fallback)); return 'Hey Tabby'
        backend=FakeTranscriber()
        detector=WakeWordDetector({'companion_wake_enabled':True},lambda text,score:None,transcriber=backend)
        text=detector._transcribe(np.zeros(16000,dtype=np.float32))
        self.assertEqual(text,'Hey Tabby')
        self.assertEqual(backend.calls,[(16000,True,True)])

class WakeHandoffTests(unittest.TestCase):
    def test_protocol7_forwards_wake_to_tabby_socket(self):
        from companion.wake_runtime import _send_tabby_wake
        previous=os.environ.get('XDG_RUNTIME_DIR')
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['XDG_RUNTIME_DIR']=tmp
            path=os.path.join(tmp,'tabby.sock')
            received=[]
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(path); server.listen(1)
            def worker():
                conn,_=server.accept()
                with conn:
                    data=conn.recv(4096); received.append(json.loads(data.decode()))
                    conn.sendall(b'{"ok":true,"result":"waking"}')
                server.close()
            t=threading.Thread(target=worker); t.start()
            try:
                self.assertTrue(_send_tabby_wake('Hey Tabby',0.97))
                t.join(timeout=2)
                self.assertEqual(received[0]['command'],'wake')
                self.assertEqual(received[0]['source'],'protocol7')
                self.assertAlmostEqual(received[0]['score'],0.97)
            finally:
                if previous is None: os.environ.pop('XDG_RUNTIME_DIR',None)
                else: os.environ['XDG_RUNTIME_DIR']=previous

class MCPCompatibilityTests(unittest.TestCase):
    def test_legacy_tool_names_remain(self):
        import asyncio
        from companion.mcp_server import mcp
        names={tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(names,{
            'companion_set_state','whiteboard_show','whiteboard_hide','whiteboard_clear',
            'whiteboard_write','whiteboard_progress','whiteboard_choice','whiteboard_shape'
        })

if __name__=='__main__': unittest.main()
