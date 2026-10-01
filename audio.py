import sounddevice as sd
import numpy as np
import threading
import queue
from datetime import datetime

class AudioRecorder:
    def __init__(self, device_id=None, sample_rate=16000):
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = 1
        self.is_recording = False
        self.audio_queue = queue.Queue()
        # A separate, lock-protected copy lets the live transcription worker
        # inspect the recording without draining the queue used by the final
        # transcription when recording stops.
        self._recording_chunks = []
        self._chunks_lock = threading.Lock()
        self.stream = None
        self.volume_level = 0.0

    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(status)
        if self.is_recording:
            # Convert to mono if stereo
            if self.channels > 1:
                mono_data = np.mean(indata, axis=1)
            else:
                mono_data = indata.flatten()
                
            chunk = mono_data.copy()
            self.audio_queue.put(chunk)
            with self._chunks_lock:
                self._recording_chunks.append(chunk)
            # Perceptual voice meter for the Caelestia visualizer. A linear
            # `rms * 10` barely moves for normal speech and then jumps near loud
            # peaks. Mapping dBFS into 0..1 gives useful motion across whispers,
            # normal speech and loud syllables. Fast attack keeps consonants
            # snappy; gentler release avoids flicker between syllables.
            rms = float(np.sqrt(np.mean(mono_data**2)))
            peak = float(np.max(np.abs(mono_data))) if mono_data.size else 0.0
            dbfs = 20.0 * np.log10(max(rms, 1e-7))
            rms_level = float(np.clip((dbfs + 52.0) / 38.0, 0.0, 1.0))
            peak_level = float(np.clip((peak - 0.008) / 0.22, 0.0, 1.0))
            target = max(rms_level, peak_level * 0.82)
            alpha = 0.78 if target >= self.volume_level else 0.34
            self.volume_level += (target - self.volume_level) * alpha

    def start_recording(self):
        self.is_recording = True
        self.audio_queue = queue.Queue()
        with self._chunks_lock:
            self._recording_chunks = []
        self.volume_level = 0.0
        self.actual_sample_rate = self.sample_rate
        
        try:
            try:
                self.stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    device=self.device_id,
                    channels=1,
                    callback=self._audio_callback,
                    dtype='float32'
                )
                self.channels = 1
            except Exception as e:
                import os
                try:
                    with open(os.path.join(os.environ.get("TEMP", "/tmp"), "protocol7_debug.log"), "a") as f:
                        f.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] [AUDIO] Initial 16k Mono stream failed: {e}\n")
                except: pass
                
                # Ultimate fallback: Let PortAudio/Windows auto-negotiate the exact required hardware format
                self.stream = sd.InputStream(
                    device=self.device_id,
                    callback=self._audio_callback,
                    dtype='float32'
                )
                self.actual_sample_rate = int(self.stream.samplerate)
                self.channels = self.stream.channels
            self.stream.start()
        except Exception as e:
            import os
            try:
                with open(os.path.join(os.environ.get("TEMP", "/tmp"), "protocol7_debug.log"), "a") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] [AUDIO] Error starting audio recording completely: {e}\n")
            except: pass
            print(f"Error starting audio recording: {e}")
            self.is_recording = False

    def stop_recording(self):
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        
        # Collect all audio data from queue
        audio_data = []
        while not self.audio_queue.empty():
            audio_data.append(self.audio_queue.get())
        
        if audio_data:
            audio = np.concatenate(audio_data, axis=0).flatten()
            # Resample if native hardware rate differs from Whisper's target rate
            if getattr(self, 'actual_sample_rate', self.sample_rate) != self.sample_rate:
                duration = len(audio) / self.actual_sample_rate
                time_old = np.linspace(0, duration, len(audio))
                time_new = np.linspace(0, duration, int(len(audio) * self.sample_rate / self.actual_sample_rate))
                audio = np.interp(time_new, time_old, audio).astype('float32')
            return audio
        return np.array([])

    def get_recording_snapshot(self):
        """Return a stable copy of audio captured so far for live previews."""
        with self._chunks_lock:
            chunks = list(self._recording_chunks)
        if not chunks:
            return np.array([], dtype="float32")
        return np.concatenate(chunks, axis=0).flatten()
    
    def get_volume_level(self):
        return self.volume_level
