package com.sentinelmesh.gesturefootball.voice

import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.sentinelmesh.gesturefootball.npu.HtpNative
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Whisper-Tiny on Hexagon via AI Hub precompiled QNN ONNX (encoder + decoder).
 * Models loaded from filesDir/whisper/ (pushed via adb — not in APK).
 */
class WhisperEngine private constructor(
    private val env: OrtEnvironment,
    private val encoder: OrtSession,
    private val decoder: OrtSession,
    private val tokenizer: WhisperTokenizer,
) : AutoCloseable {

    data class Result(val text: String, val latencyMs: Long, val tokens: Int)

    fun transcribe(pcm16: ShortArray): Result? {
        if (pcm16.isEmpty()) return null
        val pcm = FloatArray(pcm16.size) { i -> pcm16[i] / 32768f }
        return transcribeMel(MelSpectrogram.extract(pcm))
    }

    /** Run encoder+decoder on a precomputed [80×3000] log-mel (row-major). */
    fun transcribeMel(mel: FloatArray): Result? {
        if (mel.size < MelSpectrogram.N_MELS * MelSpectrogram.N_FRAMES) return null
        val t0 = SystemClock.elapsedRealtime()
        var sum = 0.0
        var mx = Float.NEGATIVE_INFINITY
        var mn = Float.POSITIVE_INFINITY
        for (v in mel) {
            sum += v
            if (v > mx) mx = v
            if (v < mn) mn = v
        }
        Log.i(TAG, "mel stats min=$mn max=$mx mean=${sum / mel.size}")
        val cross = runEncoder(mel) ?: return null
        val ids = runDecoder(cross)
        val text = tokenizer.decode(ids)
        val ms = SystemClock.elapsedRealtime() - t0
        Log.i(TAG, "ASR \"$text\" · $ms ms · ids=$ids")
        return Result(text, ms, ids.size)
    }

    private fun runEncoder(mel: FloatArray): Map<String, FloatArray>? {
        val shape = longArrayOf(1, MelSpectrogram.N_MELS.toLong(), MelSpectrogram.N_FRAMES.toLong())
        val half = floatsToFp16(mel)
        val buf = ByteBuffer.allocateDirect(half.size * 2).order(ByteOrder.nativeOrder())
        buf.asShortBuffer().put(half).rewind()
        OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.FLOAT16).use { tensor ->
            val inputName = encoder.inputNames.first()
            encoder.run(mapOf(inputName to tensor)).use { out ->
                val caches = HashMap<String, FloatArray>()
                for (name in encoder.outputNames) {
                    caches[name] = readFloats(out, name) ?: return null
                }
                return caches
            }
        }
    }

    private fun runDecoder(cross: Map<String, FloatArray>): List<Int> {
        val heads = 6
        val headDim = 64
        val decodeLen = MEAN_DECODE_LEN
        val layers = 4

        // self caches zeros [6,1,64,199] / [6,1,199,64]
        val selfK = Array(layers) { FloatArray(heads * 1 * headDim * (decodeLen - 1)) }
        val selfV = Array(layers) { FloatArray(heads * 1 * (decodeLen - 1) * headDim) }

        val attn = FloatArray(decodeLen) { MASK_NEG }
        // Multilingual Whisper needs the full SOT prompt before text tokens.
        val prompt = intArrayOf(
            WhisperTokenizer.SOT,
            WhisperTokenizer.EN,
            WhisperTokenizer.TRANSCRIBE,
            WhisperTokenizer.NO_TIMESTAMPS,
        )
        val tokens = ArrayList<Int>(prompt.toList())
        var position = 0
        val maxNew = 32
        var lastLogits: FloatArray? = null

        // Warm KV cache with the prompt (sample only after the last prompt token).
        for (step in prompt.indices) {
            attn[decodeLen - step - 1] = 0f
            lastLogits = decodeStep(
                token = prompt[step],
                position = position,
                attn = attn,
                decodeLen = decodeLen,
                heads = heads,
                headDim = headDim,
                layers = layers,
                selfK = selfK,
                selfV = selfV,
                cross = cross,
            ) ?: return tokens
            position += 1
        }

        for (gen in 0 until maxNew) {
            val logits = lastLogits ?: return tokens
            val best = argmax(logits, 51865)
            if (best == WhisperTokenizer.EOT) return tokens
            tokens.add(best)
            attn[decodeLen - (prompt.size + gen) - 1] = 0f
            lastLogits = decodeStep(
                token = best,
                position = position,
                attn = attn,
                decodeLen = decodeLen,
                heads = heads,
                headDim = headDim,
                layers = layers,
                selfK = selfK,
                selfV = selfV,
                cross = cross,
            ) ?: return tokens
            position += 1
        }
        return tokens
    }

    private fun decodeStep(
        token: Int,
        position: Int,
        attn: FloatArray,
        decodeLen: Int,
        heads: Int,
        headDim: Int,
        layers: Int,
        selfK: Array<FloatArray>,
        selfV: Array<FloatArray>,
        cross: Map<String, FloatArray>,
    ): FloatArray? {
        val feeds = LinkedHashMap<String, OnnxTensor>()
        return try {
            feeds["input_ids"] = intTensor(longArrayOf(1, 1), intArrayOf(token))
            feeds["attention_mask"] = fp16Tensor(longArrayOf(1, 1, 1, decodeLen.toLong()), attn)
            for (i in 0 until layers) {
                feeds["k_cache_self_${i}_in"] = fp16Tensor(
                    longArrayOf(heads.toLong(), 1, headDim.toLong(), (decodeLen - 1).toLong()),
                    selfK[i],
                )
                feeds["v_cache_self_${i}_in"] = fp16Tensor(
                    longArrayOf(heads.toLong(), 1, (decodeLen - 1).toLong(), headDim.toLong()),
                    selfV[i],
                )
            }
            for (i in 0 until layers) {
                val kName = "k_cache_cross_$i"
                val vName = "v_cache_cross_$i"
                feeds[kName] = fp16Tensor(
                    longArrayOf(heads.toLong(), 1, headDim.toLong(), 1500),
                    cross[kName]!!,
                )
                feeds[vName] = fp16Tensor(
                    longArrayOf(heads.toLong(), 1, 1500, headDim.toLong()),
                    cross[vName]!!,
                )
            }
            feeds["position_ids"] = intTensor(longArrayOf(1), intArrayOf(position))
            decoder.run(feeds).use { out ->
                for (i in 0 until layers) {
                    readFloats(out, "k_cache_self_${i}_out")?.copyInto(selfK[i])
                    readFloats(out, "v_cache_self_${i}_out")?.copyInto(selfV[i])
                }
                readFloats(out, "logits")
            }
        } finally {
            feeds.values.forEach { it.close() }
        }
    }

    private fun argmax(logits: FloatArray, vocab: Int): Int {
        var best = 0
        var bestV = Float.NEGATIVE_INFINITY
        val n = minOf(vocab, logits.size)
        for (i in 0 until n) {
            val v = logits[i]
            if (v > bestV) {
                bestV = v
                best = i
            }
        }
        return best
    }

    private fun intTensor(shape: LongArray, data: IntArray): OnnxTensor {
        val buf = ByteBuffer.allocateDirect(data.size * 4).order(ByteOrder.nativeOrder())
        buf.asIntBuffer().put(data).rewind()
        return OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.INT32)
    }

    private fun fp16Tensor(shape: LongArray, data: FloatArray): OnnxTensor {
        val half = floatsToFp16(data)
        val buf = ByteBuffer.allocateDirect(half.size * 2).order(ByteOrder.nativeOrder())
        buf.asShortBuffer().put(half).rewind()
        return OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.FLOAT16)
    }

    private fun readFloats(out: OrtSession.Result, name: String): FloatArray? {
        return try {
            val opt = out.get(name)
            if (!opt.isPresent) return null
            when (val v = opt.get().value) {
                is FloatArray -> v
                is Array<*> -> flatten(v)
                is ShortArray -> fp16ToFloats(v)
                else -> {
                    // Buffer-backed
                    val tensor = opt.get() as OnnxTensor
                    val buf = tensor.floatBuffer
                    if (buf != null) {
                        val arr = FloatArray(buf.remaining())
                        buf.get(arr)
                        arr
                    } else {
                        val sb = tensor.shortBuffer
                        if (sb != null) {
                            val s = ShortArray(sb.remaining())
                            sb.get(s)
                            fp16ToFloats(s)
                        } else null
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "read $name: ${e.message}")
            null
        }
    }

    private fun flatten(value: Array<*>): FloatArray {
        val acc = ArrayList<Float>()
        fun walk(v: Any?) {
            when (v) {
                is Float -> acc.add(v)
                is Number -> acc.add(v.toFloat())
                is FloatArray -> v.forEach { acc.add(it) }
                is ShortArray -> fp16ToFloats(v).forEach { acc.add(it) }
                is Array<*> -> v.forEach { walk(it) }
            }
        }
        walk(value)
        return acc.toFloatArray()
    }

    override fun close() {
        encoder.close()
        decoder.close()
    }

    companion object {
        private const val TAG = "WhisperEngine"
        private const val MEAN_DECODE_LEN = 200
        private const val MASK_NEG = -100f
        private const val DIR = "whisper"

        fun create(context: Context): WhisperEngine? {
            return try {
                HtpNative.prepare(context)
                val dir = resolveModelDir(context) ?: run {
                    Log.w(TAG, "Whisper models missing — push to Android/data/.../files/whisper/")
                    return null
                }
                MelSpectrogram.loadFilters(context)
                val tokenizer = WhisperTokenizer.load(context) ?: return null
                val env = OrtEnvironment.getEnvironment()
                val enc = HtpNative.openQnnSession(env, File(dir, "encoder.onnx"))
                val dec = HtpNative.openQnnSession(env, File(dir, "decoder.onnx"))
                WhisperEngine(env, enc, dec, tokenizer)
            } catch (e: Exception) {
                e.printStackTrace()
                null
            }
        }

        fun resolveModelDir(context: Context): File? {
            // Prefer internal filesDir — adb-pushed external dirs are often shell-owned
            // and invisible to the app UID on Samsung.
            val candidates = listOf(
                File(context.filesDir, DIR),
                File(context.getExternalFilesDir(null), DIR),
            )
            for (d in candidates) {
                if (File(d, "encoder.onnx").exists() &&
                    File(d, "encoder_qairt_context.bin").exists() &&
                    File(d, "decoder.onnx").exists() &&
                    File(d, "decoder_qairt_context.bin").exists()
                ) {
                    Log.i(TAG, "models @ ${d.absolutePath}")
                    return d
                }
            }
            return null
        }

        private fun floatsToFp16(src: FloatArray): ShortArray {
            val out = ShortArray(src.size)
            for (i in src.indices) out[i] = floatToFp16(src[i])
            return out
        }

        private fun fp16ToFloats(src: ShortArray): FloatArray {
            val out = FloatArray(src.size)
            for (i in src.indices) out[i] = fp16ToFloat(src[i])
            return out
        }

        private fun floatToFp16(f: Float): Short {
            val bits = java.lang.Float.floatToIntBits(f)
            val sign = (bits ushr 16) and 0x8000
            val exp = ((bits ushr 23) and 0xff) - 127 + 15
            val mant = bits and 0x7fffff
            val h = when {
                exp <= 0 -> sign
                exp >= 31 -> sign or 0x7c00
                else -> sign or (exp shl 10) or (mant ushr 13)
            }
            return h.toShort()
        }

        private fun fp16ToFloat(h: Short): Float {
            val v = h.toInt() and 0xffff
            val sign = (v and 0x8000) shl 16
            val exp = (v ushr 10) and 0x1f
            val mant = v and 0x3ff
            val bits = when (exp) {
                0 -> if (mant == 0) sign else {
                    // subnormal
                    var m = mant
                    var e = -14
                    while (m and 0x400 == 0) {
                        m = m shl 1
                        e--
                    }
                    sign or ((e + 127) shl 23) or ((m and 0x3ff) shl 13)
                }
                31 -> sign or 0x7f800000 or (mant shl 13)
                else -> sign or ((exp - 15 + 127) shl 23) or (mant shl 13)
            }
            return java.lang.Float.intBitsToFloat(bits)
        }
    }
}
