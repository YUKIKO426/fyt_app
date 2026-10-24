# audio_engine.py — P-core pinned, direct ALSA, low-latency

import os
import queue
import threading
import numpy as np
import sounddevice as sd
from scipy import signal
import psutil

from config import SAMPLE_RATE, CHUNK, CHANNELS, INPUT_DEVICE

AUDIO_CPU_AFFINITY = [0, 1, 2, 3]

settings = {
    "volume"      : 25.0,
    "sub_bass_db" : 15.0,
    "bass_db"     : 12.0,
    "presence_db" : 10.0,
    "air_db"      : 6.0,
    "loudness"    : 75.0,
    "gate_db"     : -45.0,
    "running"     : False,
}

level_meter = {"rms": 0.0, "peak": 0.0}


def _pin_audio_thread_to_pcores():
    try:
        proc = psutil.Process()
        proc.cpu_affinity(AUDIO_CPU_AFFINITY)
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(80))
        print(f"[audio] pinned to P-cores {AUDIO_CPU_AFFINITY}, SCHED_FIFO")
    except Exception as e:
        print(f"[audio] affinity failed (root needed for RT): {e}")


class AudioEngine:
    def __init__(self):
        self.in_q       = queue.Queue(maxsize=50)
        self.out_q      = queue.Queue(maxsize=200)
        self._thread    = None
        self._stream    = None
        self._stop_flag = False
        self._build_filters()
        self._init_compressor_state()

    def _build_filters(self):
        nyq = SAMPLE_RATE / 2
        self.hp_sos = signal.butter(2, 70/nyq, btype='high', output='sos')
        self.hp_zi  = signal.sosfilt_zi(self.hp_sos)

        self.sub_b,  self.sub_a  = signal.iirpeak(60/nyq,    Q=1.2)
        self.bass_b, self.bass_a = signal.iirpeak(150/nyq,   Q=1.0)
        self.pres_b, self.pres_a = signal.iirpeak(3000/nyq,  Q=0.8)
        self.air_b,  self.air_a  = signal.iirpeak(11000/nyq, Q=0.7)

        self.sub_zi  = signal.lfilter_zi(self.sub_b,  self.sub_a)
        self.bass_zi = signal.lfilter_zi(self.bass_b, self.bass_a)
        self.pres_zi = signal.lfilter_zi(self.pres_b, self.pres_a)
        self.air_zi  = signal.lfilter_zi(self.air_b,  self.air_a)

    def _init_compressor_state(self):
        self.env = 0.0

    def _db_to_lin(self, db):
        return 10 ** (db / 20.0)

    def _highpass(self, x):
        y, self.hp_zi = signal.sosfilt(self.hp_sos, x, zi=self.hp_zi)
        return y

    def _peaking(self, x, b, a, zi, gain_db):
        if abs(gain_db) < 0.01:
            return x, zi
        g = self._db_to_lin(gain_db)
        y, zi_new = signal.lfilter(b * g, a, x, zi=zi)
        return y, zi_new

    def _noise_gate(self, x):
        threshold_lin = self._db_to_lin(settings["gate_db"]) * 32767
        rms = np.sqrt(np.mean(x * x) + 1e-9)
        return x if rms > threshold_lin else np.zeros_like(x)

    def _compress(self, x, threshold_db, ratio, attack_s, release_s):
        attack_coef  = np.exp(-1.0 / (attack_s  * SAMPLE_RATE))
        release_coef = np.exp(-1.0 / (release_s * SAMPLE_RATE))
        threshold    = self._db_to_lin(threshold_db)

        abs_x = np.abs(x)
        env   = self.env
        gain  = np.ones_like(x)

        for i in range(len(x)):
            if abs_x[i] > env:
                env = attack_coef * env + (1 - attack_coef) * abs_x[i]
            else:
                env = release_coef * env + (1 - release_coef) * abs_x[i]

            if env > threshold:
                over_db = 20 * np.log10(env / threshold + 1e-9)
                gain_db = -over_db * (1 - 1/ratio)
                gain[i] = 10 ** (gain_db / 20.0)

        self.env = env
        return x * gain

    def _limiter(self, x, ceiling_db=-0.5):
        ceiling = self._db_to_lin(ceiling_db) * 32767
        return np.clip(x, -ceiling, ceiling)

    def process(self, raw_bytes):
        x = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float64)

        # level meter (pre-DSP raw)
        rms_raw = float(np.sqrt(np.mean(x * x)))
        peak_raw = float(np.max(np.abs(x)))
        level_meter["rms"]  = min(1.0, rms_raw / 30000.0)
        level_meter["peak"] = min(1.0, peak_raw / 32767.0)

        x = self._noise_gate(x)
        x = self._highpass(x)

        x, self.sub_zi  = self._peaking(x, self.sub_b,  self.sub_a,  self.sub_zi,  settings["sub_bass_db"])
        x, self.bass_zi = self._peaking(x, self.bass_b, self.bass_a, self.bass_zi, settings["bass_db"])
        x, self.pres_zi = self._peaking(x, self.pres_b, self.pres_a, self.pres_zi, settings["presence_db"])
        x, self.air_zi  = self._peaking(x, self.air_b,  self.air_a,  self.air_zi,  settings["air_db"])

        l = settings["loudness"] / 100.0
        x = self._compress(x, threshold_db=-20 * l - 10, ratio=2 + 6*l,
                           attack_s=0.005, release_s=0.150)
        x = self._compress(x, threshold_db=-12 * l - 6, ratio=4 + 8*l,
                           attack_s=0.001, release_s=0.050)

        x = x * settings["volume"]
        x = self._limiter(x, ceiling_db=-0.5)

        x = np.clip(x, -32767, 32767).astype(np.int16)
        stereo = np.column_stack((x, x)).flatten()
        return stereo.tobytes()

    def _mic_cb(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] status: {status}")
        try:
            self.in_q.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def _worker(self):
        _pin_audio_thread_to_pcores()
        while not self._stop_flag:
            try:
                raw = self.in_q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                processed = self.process(raw)
            except Exception as e:
                print(f"[audio] DSP error: {e}")
                continue
            try:
                self.out_q.put_nowait(processed)
            except queue.Full:
                try:
                    self.out_q.get_nowait()
                    self.out_q.put_nowait(processed)
                except queue.Empty:
                    pass

    def start(self):
        if self._stop_flag is False and self._thread and self._thread.is_alive():
            return
        self._stop_flag = False
        settings["running"] = True

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='int16',
            blocksize=CHUNK,
            device=INPUT_DEVICE,
            callback=self._mic_cb,
            latency='low',
            never_drop_input=False,
        )
        self._stream.start()

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        print(f"[audio] engine started — device={INPUT_DEVICE or 'default'}")

    def stop(self):
        self._stop_flag = True
        settings["running"] = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        print("[audio] engine stopped")

    def drain_output(self, max_chunks=10):
        chunks = []
        for _ in range(max_chunks):
            try:
                chunks.append(self.out_q.get_nowait())
            except queue.Empty:
                break
        return chunks


engine = AudioEngine()


# ── HTTP stream server ────────────────────────────────────
from aiohttp import web
import asyncio

async def http_stream_handler(request):
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type"  : "audio/wav",
            "Cache-Control" : "no-cache",
        },
    )
    await resp.prepare(request)

    wav_header = b'RIFF' + b'\xff\xff\xff\xff' + b'WAVEfmt ' + \
                 (16).to_bytes(4, 'little') + \
                 (1).to_bytes(2, 'little') + \
                 (2).to_bytes(2, 'little') + \
                 SAMPLE_RATE.to_bytes(4, 'little') + \
                 (SAMPLE_RATE * 4).to_bytes(4, 'little') + \
                 (4).to_bytes(2, 'little') + \
                 (16).to_bytes(2, 'little') + \
                 b'data' + b'\xff\xff\xff\xff'
    await resp.write(wav_header)

    silence = b'\x00\x00' * CHUNK * 2
    try:
        while True:
            chunks = engine.drain_output(max_chunks=20)
            if chunks:
                for c in chunks:
                    await resp.write(c)
            else:
                await resp.write(silence)
            await asyncio.sleep(0.010)
    except (ConnectionResetError, web.HTTPException):
        pass
    except Exception as e:
        print(f"[http] stream ended: {e}")
    return resp


def create_http_app():
    app = web.Application()
    app.router.add_get("/stream", http_stream_handler)
    return app
