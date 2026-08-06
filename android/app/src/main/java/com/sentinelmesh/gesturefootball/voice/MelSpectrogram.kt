package com.sentinelmesh.gesturefootball.voice

import android.content.Context
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * OpenAI Whisper log-mel features: 80 × 3000 for ≤30 s @ 16 kHz.
 * Mel filterbank is the torchaudio/librosa Slaney bank (bundled binary).
 */
object MelSpectrogram {
    const val SAMPLE_RATE = 16_000
    const val N_FFT = 400
    const val HOP = 160
    const val N_MELS = 80
    const val N_FRAMES = 3000
    const val N_SAMPLES = 30 * SAMPLE_RATE

    private const val TAG = "MelSpectrogram"
    private val nFreqs = N_FFT / 2 + 1

    private val window = FloatArray(N_FFT) { i ->
        // periodic Hann — matches torch.hann_window(N_FFT)
        (0.5 - 0.5 * cos(2.0 * PI * i / N_FFT)).toFloat()
    }

    @Volatile private var melFilters: Array<FloatArray>? = null
    private val cosTab: Array<FloatArray> by lazy { buildCos() }
    private val sinTab: Array<FloatArray> by lazy { buildSin() }

    fun loadFilters(context: Context) {
        if (melFilters != null) return
        synchronized(this) {
            if (melFilters != null) return
            val expected = N_MELS * nFreqs * 4
            val bytes = loadFilterBytes(context, expected)
            val flat = FloatArray(N_MELS * nFreqs)
            ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(flat)
            val filters = Array(N_MELS) { m ->
                FloatArray(nFreqs) { k -> flat[m * nFreqs + k] }
            }
            melFilters = filters
            Log.i(TAG, "mel filters loaded · sum=${flat.sum()}")
        }
    }

    private fun loadFilterBytes(context: Context, expected: Int): ByteArray {
        val candidates = listOf(
            File(context.filesDir, "whisper/mel_filters.bin"),
            File(context.getExternalFilesDir(null), "whisper/mel_filters.bin"),
        )
        for (f in candidates) {
            if (f.isFile && f.length() == expected.toLong()) return f.readBytes()
        }
        context.assets.open("whisper/mel_filters.bin").use { inp ->
            val bytes = inp.readBytes()
            require(bytes.size == expected) { "mel_filters.bin size ${bytes.size} != $expected" }
            return bytes
        }
    }

    /** PCM float mono [-1,1] → [80, 3000] row-major float array. */
    fun extract(pcm: FloatArray): FloatArray {
        val filters = melFilters
            ?: error("MelSpectrogram.loadFilters() must be called first")
        val audio = FloatArray(N_SAMPLES)
        val n = min(pcm.size, N_SAMPLES)
        System.arraycopy(pcm, 0, audio, 0, n)

        val magnitudes = stftPower(audio) // [201, ~3000]
        val nFrames = magnitudes[0].size
        val mel = Array(N_MELS) { FloatArray(nFrames) }
        for (m in 0 until N_MELS) {
            val f = filters[m]
            for (t in 0 until nFrames) {
                var s = 0f
                for (k in 0 until nFreqs) s += f[k] * magnitudes[k][t]
                mel[m][t] = s
            }
        }
        var maxV = 1e-10f
        for (m in 0 until N_MELS) for (t in 0 until nFrames) {
            mel[m][t] = log10(max(1e-10f, mel[m][t]))
            if (mel[m][t] > maxV) maxV = mel[m][t]
        }
        for (m in 0 until N_MELS) for (t in 0 until nFrames) {
            mel[m][t] = max(mel[m][t], maxV - 8f)
            mel[m][t] = (mel[m][t] + 4f) / 4f
        }
        val out = FloatArray(N_MELS * N_FRAMES)
        for (m in 0 until N_MELS) {
            for (t in 0 until N_FRAMES) {
                out[m * N_FRAMES + t] = if (t < nFrames) mel[m][t] else 0f
            }
        }
        return out
    }

    private fun stftPower(audio: FloatArray): Array<FloatArray> {
        val pad = N_FFT / 2
        val padded = reflectPad(audio, pad)
        // torch.stft(center=True) then magnitudes[..., :-1]
        val rawFrames = 1 + (padded.size - N_FFT) / HOP
        val frames = max(1, rawFrames - 1)
        val out = Array(nFreqs) { FloatArray(frames) }
        val re = FloatArray(N_FFT)
        for (t in 0 until frames) {
            val start = t * HOP
            for (i in 0 until N_FFT) {
                re[i] = padded[start + i] * window[i]
            }
            rfftPower400(re, out, t)
        }
        return out
    }

    private fun reflectPad(audio: FloatArray, pad: Int): FloatArray {
        val n = audio.size
        val out = FloatArray(n + 2 * pad)
        // Match torch pad_mode='reflect' (no repeat of edge sample).
        for (i in 0 until pad) {
            out[i] = audio[pad - i]
        }
        System.arraycopy(audio, 0, out, pad, n)
        for (i in 0 until pad) {
            out[pad + n + i] = audio[n - 2 - i]
        }
        return out
    }

    private fun rfftPower400(windowed: FloatArray, out: Array<FloatArray>, t: Int) {
        for (k in 0 until nFreqs) {
            var re = 0.0
            var im = 0.0
            val c = cosTab[k]
            val s = sinTab[k]
            for (n in 0 until N_FFT) {
                val x = windowed[n].toDouble()
                re += x * c[n]
                im -= x * s[n]
            }
            out[k][t] = (re * re + im * im).toFloat()
        }
    }

    private fun buildCos(): Array<FloatArray> =
        Array(nFreqs) { k ->
            FloatArray(N_FFT) { n -> cos(2.0 * PI * k * n / N_FFT).toFloat() }
        }

    private fun buildSin(): Array<FloatArray> =
        Array(nFreqs) { k ->
            FloatArray(N_FFT) { n -> sin(2.0 * PI * k * n / N_FFT).toFloat() }
        }
}
