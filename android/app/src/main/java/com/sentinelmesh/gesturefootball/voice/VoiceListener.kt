package com.sentinelmesh.gesturefootball.voice

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Energy-gated mic capture → Whisper ASR on a background thread.
 * Listens in ~2.5 s windows when RMS crosses a threshold.
 */
class VoiceListener(
    private val engine: WhisperEngine,
    private val onResult: (WhisperEngine.Result) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private val exec = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val running = AtomicBoolean(false)
    private var record: AudioRecord? = null

    fun start() {
        if (!running.compareAndSet(false, true)) return
        exec.execute { loop() }
    }

    fun stop() {
        running.set(false)
        try {
            record?.stop()
        } catch (_: Exception) {
        }
        record?.release()
        record = null
    }

    fun close() {
        stop()
        exec.shutdownNow()
        engine.close()
    }

    private fun loop() {
        val sr = MelSpectrogram.SAMPLE_RATE
        val minBuf = AudioRecord.getMinBufferSize(
            sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val ar = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sr,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, sr * 2),
        )
        if (ar.state != AudioRecord.STATE_INITIALIZED) {
            main.post { onStatus("MIC FAIL") }
            running.set(false)
            return
        }
        record = ar
        ar.startRecording()
        Log.i(TAG, "mic open · ${sr}Hz · listening")
        main.post { onStatus("VOICE · LISTENING") }

        val chunk = ShortArray(sr / 10) // 100 ms
        val utterance = ArrayList<Short>(sr * 3)
        var speaking = false
        var silenceMs = 0
        var lastAsr = 0L

        while (running.get()) {
            val n = ar.read(chunk, 0, chunk.size)
            if (n <= 0) continue
            val rms = rms(chunk, n)
            if (!speaking) {
                if (rms > START_RMS) {
                    speaking = true
                    silenceMs = 0
                    utterance.clear()
                    for (i in 0 until n) utterance.add(chunk[i])
                    main.post { onStatus("VOICE · HEARING…") }
                }
            } else {
                for (i in 0 until n) utterance.add(chunk[i])
                if (rms < STOP_RMS) silenceMs += 100 else silenceMs = 0
                val tooLong = utterance.size > sr * 4
                if (silenceMs >= 500 || tooLong) {
                    speaking = false
                    val now = System.currentTimeMillis()
                    if (now - lastAsr > 1200 && utterance.size > sr / 2) {
                        lastAsr = now
                        val pcm = utterance.toShortArray()
                        main.post { onStatus("VOICE · NPU…") }
                        try {
                            val result = engine.transcribe(pcm)
                            if (result != null && result.text.isNotBlank()) {
                                main.post {
                                    onStatus("VOICE · ${result.latencyMs} ms")
                                    onResult(result)
                                }
                            } else {
                                main.post { onStatus("VOICE · LISTENING") }
                            }
                        } catch (e: Exception) {
                            Log.e(TAG, "ASR", e)
                            main.post { onStatus("VOICE · ERR") }
                        }
                    } else {
                        main.post { onStatus("VOICE · LISTENING") }
                    }
                    utterance.clear()
                    silenceMs = 0
                }
            }
        }
        try {
            ar.stop()
        } catch (_: Exception) {
        }
        ar.release()
        if (record === ar) record = null
    }

    private fun rms(buf: ShortArray, n: Int): Float {
        var s = 0.0
        for (i in 0 until n) {
            val v = buf[i].toDouble()
            s += v * v
        }
        return sqrt(s / n).toFloat()
    }

    companion object {
        private const val TAG = "VoiceListener"
        private const val START_RMS = 600f
        private const val STOP_RMS = 280f
    }
}
