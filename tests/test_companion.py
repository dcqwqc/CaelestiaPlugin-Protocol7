import json
import os
import socket
import tempfile
import threading
import unittest

from companion.wake_word import normalize_text, wake_match_score, close_match_score, companion_phrase_match, companion_phrase_action

class WakeWordTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_text('Hey, TABBY!'),'hey tabby')

    def test_exact_and_alias_match(self):
        self.assertEqual(wake_match_score('hey tabby', 'Hey Tabby'),1.0)
        self.assertEqual(wake_match_score('Hey Tabi', 'Hey Tabby'),1.0)
        self.assertEqual(wake_match_score('okay hey tabby can you open this', 'Hey Tabby'),1.0)

    def test_lume_aliases_are_configurable(self):
        self.assertEqual(wake_match_score("hey loom", "Hey Lume", ["Hey Loom", "Hey Lumi"]), 1.0)
        self.assertEqual(close_match_score("goodbye lume", "Bye Lume", "Goodbye Lume"), 1.0)
        self.assertLess(close_match_score("lets say goodbye lume tomorrow", "Bye Lume", "Goodbye Lume"), .92)
        action, score = companion_phrase_action("hey lumi", "Hey Lume", "Bye Lume",
            wake_aliases="Hey Lumi", close_aliases="Goodbye Lume")
        self.assertEqual((action,score), ("wake",1.0))
        self.assertLess(wake_match_score("hey tabby"), .84)

    def test_unrelated_speech_is_rejected(self):
        self.assertLess(wake_match_score('maybe happy today', 'Hey Tabby'),0.84)
        self.assertLess(wake_match_score('tabby', 'Hey Tabby'),0.84)

    def test_close_phrase_is_distinct_from_wake(self):
        action,score=companion_phrase_match('Bye Tabby', 'Hey Tabby', 'Bye Tabby')
        self.assertEqual(action,'close')
        self.assertEqual(score,1.0)
        action,score=companion_phrase_match('Hey Tabby', 'Hey Tabby', 'Bye Tabby')
        self.assertEqual(action,'wake')
        self.assertEqual(score,1.0)

    def test_close_aliases(self):
        self.assertEqual(wake_match_score('Goodbye Tabby','Bye Tabby'),1.0)
        self.assertEqual(wake_match_score('By Tabby','Bye Tabby'),1.0)

    def test_close_phrase_must_be_standalone(self):
        self.assertEqual(close_match_score('Bye Tabi','Bye Tabby'),1.0)
        self.assertEqual(close_match_score('Goodbye Tabby','Bye Tabby'),1.0)
        self.assertLess(close_match_score('we can say bye tabby later','Bye Tabby'),0.92)
        self.assertLess(close_match_score('okay bye tabby please','Bye Tabby'),0.92)
        self.assertGreaterEqual(close_match_score('bye tabbi','Bye Tabby'),0.92)

    def test_active_companion_only_accepts_close_phrase(self):
        action,score=companion_phrase_action('Hey Tabi','Hey Tabby','Bye Tabby',companion_active=True)
        self.assertEqual(action,'close')
        self.assertLess(score,0.84)
        action,score=companion_phrase_action('Bye Tabi','Hey Tabby','Bye Tabby',companion_active=True)
        self.assertEqual(action,'close')
        self.assertEqual(score,1.0)

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

class CloseGateTests(unittest.TestCase):
    def test_close_is_rejected_while_tabby_is_speaking(self):
        from unittest.mock import patch
        from companion.wake_runtime import WakeRuntime
        runtime=WakeRuntime.__new__(WakeRuntime); runtime.enabled=True
        runtime._fallback_cli=lambda command: (_ for _ in ()).throw(AssertionError('no fallback'))
        with patch.object(WakeRuntime,'_tabby_status',return_value={'summoned':True,'state':'speaking'}),              patch('companion.wake_runtime._send_tabby_close') as send_close:
            runtime._on_close('Bye Tabi',1.0)
            send_close.assert_not_called()

    def test_close_recorded_while_speaking_is_rejected_even_if_now_listening(self):
        from unittest.mock import patch
        from companion.wake_runtime import WakeRuntime
        runtime=WakeRuntime.__new__(WakeRuntime); runtime.enabled=True
        runtime._fallback_cli=lambda command: (_ for _ in ()).throw(AssertionError('no fallback'))
        with patch.object(WakeRuntime,'_tabby_status',return_value={'summoned':True,'state':'listening'}),              patch('companion.wake_runtime._send_tabby_close') as send_close:
            runtime._on_close('Bye Tabi',1.0,origin_state='speaking')
            send_close.assert_not_called()

    def test_close_is_accepted_while_tabby_is_listening(self):
        from unittest.mock import patch
        from companion.wake_runtime import WakeRuntime
        runtime=WakeRuntime.__new__(WakeRuntime); runtime.enabled=True
        runtime._fallback_cli=lambda command: None
        with patch.object(WakeRuntime,'_tabby_status',return_value={'summoned':True,'state':'listening'}),              patch('companion.wake_runtime._send_tabby_close',return_value=True) as send_close:
            runtime._on_close('Bye Tabi',1.0,origin_state='listening')
            send_close.assert_called_once()


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
                self.assertEqual(received[0]['command'],'summon')
                self.assertEqual(received[0]['source'],'protocol7')
                self.assertAlmostEqual(received[0]['score'],0.97)
            finally:
                if previous is None: os.environ.pop('XDG_RUNTIME_DIR',None)
                else: os.environ['XDG_RUNTIME_DIR']=previous

    def test_protocol7_forwards_close_to_tabby_socket(self):
        from companion.wake_runtime import _send_tabby_close
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
                    conn.sendall(b'{"ok":true,"result":"closed"}')
                server.close()
            t=threading.Thread(target=worker); t.start()
            try:
                self.assertTrue(_send_tabby_close('Bye Tabby',0.96))
                t.join(timeout=2)
                self.assertEqual(received[0]['command'],'close')
                self.assertEqual(received[0]['source'],'protocol7')
                self.assertAlmostEqual(received[0]['score'],0.96)
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
