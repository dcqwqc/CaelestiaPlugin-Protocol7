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
        # Final accounting stays active independently of the preview switch.
        path = config.get("streaming_stt_quota_ledger")
        if isinstance(path, str) and path.startswith("/"):
            from streaming_stt import configure_budget
            configure_budget(path)

    def _load_local_model_locked(self):
        model_size = self.config.get("model_size", "tiny.en")
        compute_type = self.config.get("compute_type", "int8")

        if self.model is None or self.current_model_size != model_size:
            print(f"Loading Whisper model: {model_size} (compute_type: {compute_type})...")
            import time
            start_load = time.time()
            # Keep the always-on wake recognizer lightweight; final dictation uses
            # cloud STT (or its own selected local model) independently.
            model_kwargs = {"cpu_threads": int(self.config["cpu_threads"])} if self.config.get("cpu_threads") else {}
            self.model = WhisperModel(model_size, device="auto", compute_type=compute_type, **model_kwargs)
            self.current_model_size = model_size
            print(f"Model loaded successfully in {time.time() - start_load:.2f}s.")

    def _load_model(self):
        with self._lock:
            use_groq = self.config.get("use_groq", False)
            api_key = self.config.get("groq_api_key", "").strip()

            if use_groq and api_key:
                try:
                    if self.groq_client is None or self.current_groq_key != api_key:
                        self.groq_client = Groq(
                            api_key=api_key,
                            timeout=float(self.config.get("groq_timeout_seconds", 10.0)),
                            max_retries=0,
                        )
                        self.current_groq_key = api_key
                    return
                except Exception as error:
                    print(f"Groq client setup failed ({type(error).__name__}); cloud transcription unavailable.")
                    self.groq_client = None
                    self.current_groq_key = None

            # Cloud-selected dictation must never silently use tiny.en on failure.
            # The local model remains available only when explicitly selected.
            if use_groq:
                if not api_key:
                    print("Groq transcription is enabled but no key is configured.")
                return
            self._load_local_model_locked()

    def groq_preview_client(self):
        """Return (client, model) for opt-in live previews, else None.

        Previews reuse exactly the Groq client and model already selected for
        final dictation: no separate credential, provider or billing route.
        This never creates a client, loads a model or falls back to local
        Whisper; previews simply wait until final dictation set Groq up.
        """
        if not self.config.get("use_groq", False):
            return None
        key = str(self.config.get("groq_api_key") or "").strip()
        client = self.groq_client
        if not key or client is None or self.current_groq_key != key:
            return None
        return client, str(self.config.get("groq_model") or "whisper-large-v3-turbo")

    @staticmethod
    def _suspicious_numeric_hallucination(text, audio_data):
        """Only suppress number-only results when voice activity is absent.

        Spoken codes, dates and quantities remain valid when voice is present.
        If the VAD is unavailable, fail open rather than discard dictation.
        """
        import re

        if not text or not re.fullmatch(r"[\d\s.,:;/%+\-#*()]+", text):
            return False
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            segments = get_speech_timestamps(
                audio_data,
                VadOptions(threshold=0.35, min_speech_duration_ms=120,
                           min_silence_duration_ms=350, speech_pad_ms=60),
                sampling_rate=16000,
            )
            return not segments
        except Exception as error:
            print(f"Numeric transcription VAD unavailable: {type(error).__name__}")
            return False

    def transcribe(self, audio_data, live=False, allow_local_fallback=True):
        # Reject audio less than 0.5 seconds (at 16000Hz, 0.5s = 8000 samples)
        if len(audio_data) < 8000:
            return ""

        # Partial/live recognition must never compete with the final cloud
        # request. Use the fast local model for live preview and wake-word
        # detection; reserve Groq for the final dictation only.
        if live and self.config.get("use_groq", False):
            # Local tiny.en partials are unreliable and can hallucinate numbers.
            # Prefer no partial over misleading text when cloud STT is enabled.
            return ""
        if live:
            with self._lock:
                self._load_local_model_locked()
        else:
            self._load_model()
        
        use_groq = self.config.get("use_groq", False)
        import time
        t0 = time.time()
        
        if use_groq and not self.groq_client and not live:
            print("Groq not initialized; refusing to substitute local Whisper.")
            return ""

        if use_groq and self.groq_client and not live:
            print("Transcribing with Groq API...")
            try:
                # Convert numpy array to wav in memory
                wav_io = io.BytesIO()
                sf.write(wav_io, audio_data, 16000, format='WAV', subtype='PCM_16')
                wav_io.seek(0)
                
                groq_model = self.config.get("groq_model", "whisper-large-v3-turbo")
                kwargs = dict(file=("audio.wav", wav_io.read()), model=groq_model,
                              response_format="json", temperature=0.0)
                api = self.groq_client.audio.transcriptions
                raw = getattr(api, "with_raw_response", None)
                from streaming_stt import final_priority
                with final_priority(len(audio_data) / 16000.0):
                    if raw is not None and callable(getattr(raw, "create", None)):
                        response = raw.create(**kwargs)
                        transcription = response.parse()
                        from streaming_stt import observe_final_response
                        observe_final_response(headers=getattr(response, "headers", None))
                    else:
                        transcription = api.create(**kwargs)
                recognized_text = transcription.text.strip()
                if self._suspicious_numeric_hallucination(recognized_text, audio_data):
                    # A quiet/no-speech capture can hallucinate digits. Do not
                    # silently inject made-up numbers into the user's editor.
                    print("[Groq Whisper] Rejected number-only non-speech result")
                    return ""
                print(f"[Groq Whisper] Internal Transcription took {time.time() - t0:.2f}s")
                return recognized_text
            except Exception as e:
                from streaming_stt import observe_final_response
                observe_final_response(error=e)
                print(f"Groq transcription failed ({type(e).__name__}); no local substitution.")
                return ""
        
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
