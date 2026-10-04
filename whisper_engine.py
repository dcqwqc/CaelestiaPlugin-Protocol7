import os
import io
import soundfile as sf
from faster_whisper import WhisperModel
from groq import Groq

class WhisperEngine:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.current_model_size = None
        import threading
        self._lock = threading.Lock()
        self.groq_client = None
        self.current_groq_key = None

    def _load_local_model_locked(self):
        model_size = self.config.get("model_size", "tiny.en")
        compute_type = self.config.get("compute_type", "int8")

        if self.model is None or self.current_model_size != model_size:
            print(f"Loading Whisper model: {model_size} (compute_type: {compute_type})...")
            import time
            start_load = time.time()
            self.model = WhisperModel(model_size, device="auto", compute_type=compute_type)
            self.current_model_size = model_size
            print(f"Model loaded successfully in {time.time() - start_load:.2f}s.")

    def _load_model(self):
        with self._lock:
            use_groq = self.config.get("use_groq", False)
            api_key = self.config.get("groq_api_key", "").strip()

            if use_groq and api_key:
                try:
                    if self.groq_client is None or self.current_groq_key != api_key:
                        self.groq_client = Groq(api_key=api_key)
                        self.current_groq_key = api_key
                    return
                except Exception as error:
                    print(f"Groq client setup failed ({error}); falling back to local Whisper.")
                    self.groq_client = None
                    self.current_groq_key = None

            # Groq can be enabled before a key is entered. In that state (or if
            # client setup failed), keep dictation functional with the selected
            # local model instead of leaving self.model as None.
            self._load_local_model_locked()

    def transcribe(self, audio_data, live=False, allow_local_fallback=True):
        # Reject audio less than 0.5 seconds (at 16000Hz, 0.5s = 8000 samples)
        if len(audio_data) < 8000:
            return ""

        self._load_model()
        
        use_groq = self.config.get("use_groq", False)
        import time
        t0 = time.time()
        
        if use_groq and self.groq_client:
            print("Transcribing with Groq API...")
            try:
                # Convert numpy array to wav in memory
                wav_io = io.BytesIO()
                sf.write(wav_io, audio_data, 16000, format='WAV', subtype='PCM_16')
                wav_io.seek(0)
                
                groq_model = self.config.get("groq_model", "whisper-large-v3-turbo")
                transcription = self.groq_client.audio.transcriptions.create(
                    file=("audio.wav", wav_io.read()),
                    model=groq_model,
                    response_format="json"
                )
                print(f"[Groq Whisper] Internal Transcription took {time.time() - t0:.2f}s")
                return transcription.text.strip()
            except Exception as e:
                if not allow_local_fallback:
                    print(f"Groq API Error: {e}; local fallback disabled for this request.")
                    return ""
                print(f"Groq API Error: {e}; falling back to local Whisper.")
                with self._lock:
                    self._load_local_model_locked()
        
        # Determine language and task
        language = self.config.get("language", "en")
        auto_detect = self.config.get("auto_detect_language", True)
        translate = self.config.get("translate", False)
        
        task = "translate" if translate else "transcribe"
        
        kwargs = {
            "task": task,
            "condition_on_previous_text": False
        }
        
        if not auto_detect and language:
            kwargs["language"] = language

        print("Transcribing with Whisper (beam_size=1)...")
        
        # Use beam_size=1 (greedy) for massive speedup. Beam size 5 is overkill for dictation and slow on CPU.
        # We also disable VAD or make it very lenient so it doesn't aggressively cut off quiet speech or natural pauses.
        try:
            segments, info = self.model.transcribe(
                audio_data, 
                beam_size=1, 
                # VAD makes final dictations cleaner, but it waits for a
                # silence boundary and makes partial results feel delayed.
                vad_filter=not live,
                vad_parameters=dict(min_silence_duration_ms=2000, speech_pad_ms=400),
                **kwargs
            )
            text = "".join(segment.text for segment in segments)
        except Exception as e:
            if "cublas" in str(e).lower() or "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                print(f"CUDA Error detected during transcription ({e}). Falling back to CPU...")
                # Re-initialize the model strictly on CPU to recover gracefully
                self.model = WhisperModel(self.current_model_size, device="cpu", compute_type=self.config.get("compute_type", "int8"))
                segments, info = self.model.transcribe(
                    audio_data, 
                    beam_size=1, 
                    vad_filter=not live,
                    vad_parameters=dict(min_silence_duration_ms=2000, speech_pad_ms=400),
                    **kwargs
                )
                text = "".join(segment.text for segment in segments)
            else:
                raise e
        
        print(f"[Whisper] Internal Transcription took {time.time() - t0:.2f}s")
        return text.strip()
