package com.sentinelmesh.gesturefootball.pose

import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.os.SystemClock
import com.sentinelmesh.gesturefootball.npu.HtpNative
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.min


/**
 * Hexagon NPU pose via Qualcomm AI Hub precompiled QNN ONNX
 * (pose_landmark_detector on Snapdragon 8 Elite for Galaxy).
 *
 * Landmark net exposes the first 25 BlazePose points (face → hips).
 * Ankles / feet are synthesised from hips + ROI for ForcePose continuity.
 */
class NpuPoseEngine private constructor(
    private val env: OrtEnvironment,
    private val landmark: OrtSession,
) : AutoCloseable {

    data class Result(
        val landmarks33: List<FloatArray>,
        val score: Float,
        val latencyMs: Long,
    )

    private val paint = Paint(Paint.FILTER_BITMAP_FLAG)

    fun infer(bitmap: Bitmap): Result? {
        val t0 = SystemClock.elapsedRealtime()
        val roi = fullBodyRoi(bitmap.width, bitmap.height)
        val lm = runLandmark(bitmap, roi) ?: return null
        return Result(lm.points33, lm.score, SystemClock.elapsedRealtime() - t0)
    }

    private data class Lm(val points33: List<FloatArray>, val score: Float)

    private fun runLandmark(bitmap: Bitmap, roi: RectF): Lm? {
        val crop = Bitmap.createBitmap(LM_SIZE, LM_SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(crop)
        canvas.drawColor(Color.BLACK)
        val src = Rect(
            roi.left.toInt().coerceIn(0, bitmap.width - 1),
            roi.top.toInt().coerceIn(0, bitmap.height - 1),
            roi.right.toInt().coerceIn(1, bitmap.width),
            roi.bottom.toInt().coerceIn(1, bitmap.height),
        )
        if (src.width() < 8 || src.height() < 8) return null
        canvas.drawBitmap(bitmap, src, Rect(0, 0, LM_SIZE, LM_SIZE), paint)

        val nhwc = bitmapToUint8Nhwc(crop)
        val inputName = landmark.inputNames.first()
        val shape = longArrayOf(1, LM_SIZE.toLong(), LM_SIZE.toLong(), 3)
        val buffer = ByteBuffer.allocateDirect(nhwc.size).order(ByteOrder.nativeOrder())
        buffer.put(nhwc).rewind()
        OnnxTensor.createTensor(env, buffer, shape, OnnxJavaType.UINT8).use { tensor ->
            landmark.run(mapOf(inputName to tensor)).use { out ->
                val score = readScalar(out)
                if (score < MIN_SCORE) return null
                val raw = readLandmarks(out) ?: return null
                val mapped = mapToFrame(raw, roi, bitmap.width, bitmap.height)
                return Lm(mapped, score)
            }
        }
    }

    private fun readScalar(out: OrtSession.Result): Float {
        val names = listOf("scores", "score")
        for (name in names) {
            try {
                val opt = out.get(name)
                if (!opt.isPresent) continue
                return when (val value = opt.get().value) {
                    is FloatArray -> value.firstOrNull() ?: continue
                    is Array<*> -> flattenFloats(value).firstOrNull() ?: continue
                    is ByteArray -> (value.firstOrNull()?.toInt()?.and(0xff) ?: 0) * 0.00390625f
                    else -> continue
                }
            } catch (_: Exception) {
            }
        }
        // If scores tensor missing, still try landmarks
        return 0.6f
    }

    private fun readLandmarks(out: OrtSession.Result): Array<FloatArray>? {
        val names = listOf("landmarks", "landmark")
        for (name in names) {
            try {
                val opt = out.get(name)
                if (!opt.isPresent) continue
                val flat = flattenFloats(opt.get().value)
                val n = when {
                    flat.size >= 25 * 4 -> 4
                    flat.size >= 25 * 2 -> 2
                    else -> continue
                }
                return Array(25) { i ->
                    floatArrayOf(flat[i * n], flat[i * n + 1])
                }
            } catch (_: Exception) {
            }
        }
        // Fallback: first output that looks like landmarks
        for (i in 0 until out.size()) {
            try {
                val flat = flattenFloats(out.get(i).value)
                val n = when {
                    flat.size >= 25 * 4 -> 4
                    flat.size >= 25 * 2 -> 2
                    else -> continue
                }
                return Array(25) { j ->
                    floatArrayOf(flat[j * n], flat[j * n + 1])
                }
            } catch (_: Exception) {
            }
        }
        return null
    }

    private fun flattenFloats(value: Any?): FloatArray {
        return when (value) {
            is FloatArray -> value
            is Array<*> -> {
                when {
                    value.isNotEmpty() && value[0] is FloatArray -> {
                        val rows = value.filterIsInstance<FloatArray>()
                        FloatArray(rows.sumOf { it.size }).also { out ->
                            var i = 0
                            for (r in rows) {
                                r.copyInto(out, i)
                                i += r.size
                            }
                        }
                    }
                    value.isNotEmpty() && value[0] is Array<*> -> {
                        val acc = ArrayList<Float>()
                        fun walk(v: Any?) {
                            when (v) {
                                is Float -> acc.add(v)
                                is Number -> acc.add(v.toFloat())
                                is FloatArray -> v.forEach { acc.add(it) }
                                is Array<*> -> v.forEach { walk(it) }
                            }
                        }
                        walk(value)
                        acc.toFloatArray()
                    }
                    else -> value.mapNotNull {
                        when (it) {
                            is Float -> it
                            is Number -> it.toFloat()
                            else -> null
                        }
                    }.toFloatArray()
                }
            }
            is ByteArray -> FloatArray(value.size) { i ->
                ((value[i].toInt() and 0xff) - LM_ZP) * LM_SCALE
            }
            else -> floatArrayOf()
        }
    }

    private fun mapToFrame(
        pts25: Array<FloatArray>,
        roi: RectF,
        frameW: Int,
        frameH: Int,
    ): List<FloatArray> {
        val out = Array(33) { floatArrayOf(0f, 0f) }
        for (i in 0 until 25) {
            var nx = pts25[i][0]
            var ny = pts25[i][1]
            if (nx > 1.5f) nx /= LM_SIZE
            if (ny > 1.5f) ny /= LM_SIZE
            nx = nx.coerceIn(0f, 1f)
            ny = ny.coerceIn(0f, 1f)
            out[i][0] = (roi.left + nx * roi.width()) / frameW
            out[i][1] = (roi.top + ny * roi.height()) / frameH
        }
        val lHip = out[23]
        val rHip = out[24]
        val midHipY = (lHip[1] + rHip[1]) / 2f
        val footY = min(0.98f, (roi.bottom / frameH) * 0.98f)
        val span = max(0.04f, kotlin.math.abs(rHip[0] - lHip[0]))
        out[25] = floatArrayOf(lHip[0], midHipY + (footY - midHipY) * 0.45f)
        out[26] = floatArrayOf(rHip[0], midHipY + (footY - midHipY) * 0.45f)
        out[27] = floatArrayOf(lHip[0], footY)
        out[28] = floatArrayOf(rHip[0], footY)
        out[29] = floatArrayOf(lHip[0], footY)
        out[30] = floatArrayOf(rHip[0], footY)
        out[31] = floatArrayOf(lHip[0] - span * 0.15f, min(0.99f, footY + 0.01f))
        out[32] = floatArrayOf(rHip[0] + span * 0.15f, min(0.99f, footY + 0.01f))
        return out.toList()
    }

    private fun fullBodyRoi(w: Int, h: Int): RectF {
        val side = min(w, h) * 0.92f
        val left = (w - side) / 2f
        val top = (h - side) / 2f
        return RectF(left, top, left + side, top + side)
    }

    private fun bitmapToUint8Nhwc(bitmap: Bitmap): ByteArray {
        val w = bitmap.width
        val h = bitmap.height
        val pixels = IntArray(w * h)
        bitmap.getPixels(pixels, 0, w, 0, 0, w, h)
        val out = ByteArray(w * h * 3)
        var i = 0
        for (p in pixels) {
            out[i++] = ((p shr 16) and 0xff).toByte()
            out[i++] = ((p shr 8) and 0xff).toByte()
            out[i++] = (p and 0xff).toByte()
        }
        return out
    }

    override fun close() {
        landmark.close()
    }

    companion object {
        private const val LM_SIZE = 256
        private const val MIN_SCORE = 0.12f
        private const val LM_SCALE = 0.006140740588307381f
        private const val LM_ZP = 112
        private const val ASSET_DIR = "npu"

        fun create(context: Context): NpuPoseEngine? {
            return try {
                HtpNative.prepare(context)
                val dir = File(context.filesDir, ASSET_DIR)
                dir.mkdirs()
                val needed = listOf(
                    "pose_landmark_detector.onnx",
                    "pose_landmark_detector_qairt_context.bin",
                )
                for (name in needed) {
                    val dest = File(dir, name)
                    if (!dest.exists() || dest.length() == 0L) {
                        context.assets.open("$ASSET_DIR/$name").use { input ->
                            dest.outputStream().use { input.copyTo(it) }
                        }
                    }
                }
                for (name in listOf(
                    "pose_detector.onnx",
                    "pose_detector_qairt_context.bin",
                    "metadata.json",
                )) {
                    val dest = File(dir, name)
                    if (!dest.exists()) {
                        try {
                            context.assets.open("$ASSET_DIR/$name").use { input ->
                                dest.outputStream().use { input.copyTo(it) }
                            }
                        } catch (_: Exception) {
                        }
                    }
                }
                val env = OrtEnvironment.getEnvironment()
                val lm = HtpNative.openQnnSession(env, File(dir, "pose_landmark_detector.onnx"))
                NpuPoseEngine(env, lm)
            } catch (e: Exception) {
                e.printStackTrace()
                null
            }
        }
    }
}
