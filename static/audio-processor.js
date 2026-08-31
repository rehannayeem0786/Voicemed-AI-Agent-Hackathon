// VoiceMed AI — AudioWorklet DSP module
//
// Two processors, mirroring the official AssemblyAI browser client:
//   capture-processor  mic → resample to 24 kHz → PCM16 → main thread
//   playback-processor main thread → ring buffer → resample to device → speakers
//
// WIRE_RATE is the Voice Agent API rate: audio/pcm, 24 kHz, 16-bit LE, mono.
// Scratch buffers are allocated once — allocating on the audio thread causes
// audible glitches.

const WIRE_RATE = 24000;
const RING_SECONDS = 30;

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / WIRE_RATE;
    this._pos = 0;      // fractional read position inside the current chunk
    this._prev = 0;     // last sample of the previous chunk (for interpolation)
    this._src = null;
    this._out = null;
    this._rmsWindow = 0;
    this._rmsCount = 0;
    this._sinceLevel = 0;
  }

  _toPcm(samples, len) {
    const pcm = new Int16Array(len);
    for (let i = 0; i < len; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return pcm;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    // Rolling mic level for the waveform UI (posted ~10x/sec).
    for (let i = 0; i < ch.length; i++) this._rmsWindow += ch[i] * ch[i];
    this._rmsCount += ch.length;
    this._sinceLevel += ch.length;
    if (this._sinceLevel >= sampleRate / 10) {
      const rms = Math.sqrt(this._rmsWindow / Math.max(1, this._rmsCount));
      this.port.postMessage({ type: "level", rms });
      this._rmsWindow = 0;
      this._rmsCount = 0;
      this._sinceLevel = 0;
    }

    if (this._ratio === 1) {
      const pcm = this._toPcm(ch, ch.length);
      this.port.postMessage({ type: "audio", pcm }, [pcm.buffer]);
      return true;
    }

    // Linear-interpolation resample down to WIRE_RATE.
    const n = ch.length;
    if (!this._src || this._src.length < n + 1) {
      this._src = new Float32Array(n + 1);
      this._out = new Float32Array(Math.ceil((n + 1) / this._ratio) + 2);
    }
    const src = this._src;
    const out = this._out;
    src[0] = this._prev;
    src.set(ch, 1);
    let outLen = 0;
    let pos = this._pos;
    while (pos < n) {
      const i = Math.floor(pos);
      const frac = pos - i;
      out[outLen++] = src[i] + (src[i + 1] - src[i]) * frac;
      pos += this._ratio;
    }
    this._pos = pos - n;
    this._prev = ch[n - 1];
    if (outLen) {
      const pcm = this._toPcm(out, outLen);
      this.port.postMessage({ type: "audio", pcm }, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("capture-processor", CaptureProcessor);

// A ring buffer instead of one AudioBufferSource per chunk — per-chunk
// scheduling drifts and clicks under jitter. Posting 'stop' empties the ring
// instantly, which is what barge-in needs.
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ring = new Float32Array(sampleRate * RING_SECONDS);
    this._writePos = 0;
    this._readPos = 0;
    this._available = 0;
    this._step = WIRE_RATE / sampleRate; // 24k → device rate
    this._rsPos = 0;
    this._rsPrev = 0;
    // After a gap the speaker sits at zero, so interpolating from the
    // pre-gap _rsPrev would click. Reset it instead.
    this._drained = false;
    this.port.onmessage = (e) => {
      const d = e.data;
      if (d === "stop") {
        this._writePos = this._readPos = this._available = 0;
        this._rsPos = this._rsPrev = 0;
        this._drained = true;
        return;
      }
      if (d instanceof ArrayBuffer) {
        const int16 = new Int16Array(d);
        if (!int16.length) return; // int16[-1] would make _rsPrev NaN
        if (this._drained) {
          this._rsPrev = 0;
          this._rsPos = 0;
          this._drained = false;
        }
        if (this._step === 1) {
          for (let i = 0; i < int16.length; i++) this._push(int16[i] / 32768);
          return;
        }
        const n = int16.length;
        let pos = this._rsPos;
        while (pos < n) {
          const i = Math.floor(pos);
          const frac = pos - i;
          const a = i === 0 ? this._rsPrev : int16[i - 1] / 32768;
          const b = int16[i] / 32768;
          this._push(a + (b - a) * frac);
          pos += this._step;
        }
        this._rsPos = pos - n;
        this._rsPrev = int16[n - 1] / 32768;
      }
    };
  }

  _push(v) {
    if (this._available < this._ring.length) {
      this._ring[this._writePos] = v;
      this._writePos = (this._writePos + 1) % this._ring.length;
      this._available++;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0] && outputs[0][0];
    if (!out) return true;
    const n = out.length;
    const take = Math.min(n, this._available);
    for (let i = 0; i < take; i++) {
      out[i] = this._ring[this._readPos];
      this._readPos = (this._readPos + 1) % this._ring.length;
    }
    this._available -= take;
    for (let i = take; i < n; i++) out[i] = 0; // underrun → silence
    return true;
  }
}
registerProcessor("playback-processor", PlaybackProcessor);
