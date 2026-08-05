package com.sentinelmesh.gesturefootball.pose

import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.os.SystemClock
import android.util.Log
import com.sentinelmesh.gesturefootball.npu.HtpNative
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Hexagon NPU MediaPipe-Pose (AI Hub precompiled QNN ONNX).
 *
 * Two-stage BlazePose pipeline:
 *  1) pose_detector 128×128 (letterboxed) → alignment keypoints
 *  2) pose_landmark_detector 256×256 rotated person crop → 25 landmarks
 *
 * Ankles/feet are synthesised from hips for ForcePose continuity.
 */
class NpuPoseEngine private constructor(
    private val env: OrtEnvironment,
    private val detector: OrtSession,
    private val landmark: OrtSession,
    private val anchors: FloatArray, // [896 * 4] = x,y,w,h normalized
) : AutoCloseable {

    data class Result(
        val landmarks33: List<FloatArray>,
        val score: Float,
        val latencyMs: Long,
    )

    /** Oriented square ROI in frame pixels (BlazePose alignment). */
    private data class OriRoi(
        val cx: Float,
        val cy: Float,
        val side: Float,
        val theta: Float,
    )

    private data class Lm(val points33: List<FloatArray>, val score: Float)
    private data class Letterbox(
        val bitmap: Bitmap,
        val scale: Float,
        val padX: Float,
        val padY: Float,
    )
    private data class DetHit(val logit: Float, val idx: Int, val raw: FloatArray)

    private val paint = Paint(Paint.FILTER_BITMAP_FLAG)
    private var tracked: OriRoi? = null
    private var missStreak = 0

    fun infer(bitmap: Bitmap): Result? {
        val t0 = SystemClock.elapsedRealtime()
        if (bitmap.width < 8 || bitmap.height < 8) return null

        val candidates = ArrayList<OriRoi>(16)
        tracked?.takeIf { missStreak < 8 }?.let { candidates.add(it) }
        candidates.addAll(detectRois(bitmap))
        candidates.addAll(centerPersonCrops(bitmap))

        var best: Lm? = null
        var bestRoi: OriRoi? = null
        val seen = HashSet<String>()
        for (roi in candidates) {
            val key = "${roi.cx.toInt()}_${roi.cy.toInt()}_${roi.side.toInt()}_${(roi.theta * 10).toInt()}"
            if (!seen.add(key)) continue
            val lm = runLandmark(bitmap, roi) ?: continue
            if (best == null || lm.score > best.score) {
                best = lm
                bestRoi = roi
            }
            if (lm.score >= MIN_LM_SCORE) break
        }

        if (best == null || bestRoi == null) {
            missStreak++
            if (missStreak >= 4) tracked = null
            return null
        }
        // Empty/wrong crops stick near ~0.086; accept only clearly better scores
        // or a geometrically plausible torso at a marginal score.
        val ok = best.score >= MIN_LM_SCORE ||
            (best.score >= MIN_LM_WEAK && torsoLooksValid(best.points33))
        if (!ok) {
            Log.d(TAG, "best landmark still low: ${best.score}")
            missStreak++
            return null
        }
        missStreak = 0
        tracked = roiFromLandmarks(best.points33, bitmap.width, bitmap.height) ?: bestRoi
        Log.i(
            TAG,
            "NPU pose ok score=${"%.3f".format(best.score)} ms=${SystemClock.elapsedRealtime() - t0}",
        )
        return Result(best.points33, best.score, SystemClock.elapsedRealtime() - t0)
    }

    /** Top-K detector ROIs. Quantized scores often saturate at sigmoid(0)≈0.5. */
    private fun detectRois(bitmap: Bitmap): List<OriRoi> {
        val boxed = letterbox(bitmap, DET_SIZE)
        val nhwc = bitmapToUint8Nhwc(boxed.bitmap)
        val inputName = detector.inputNames.first()
        val shape = longArrayOf(1, DET_SIZE.toLong(), DET_SIZE.toLong(), 3)
        val buffer = ByteBuffer.allocateDirect(nhwc.size).order(ByteOrder.nativeOrder())
        buffer.put(nhwc).rewind()

        return OnnxTensor.createTensor(env, buffer, shape, OnnxJavaType.UINT8).use { tensor ->
            detector.run(mapOf(inputName to tensor)).use { out ->
                val coords1 = readFloatGrid(out, "box_coords_1", DET_C1_SCALE, DET_C1_ZP, 512 * 12)
                    ?: return emptyList()
                val coords2 = readFloatGrid(out, "box_coords_2", DET_C2_SCALE, DET_C2_ZP, 384 * 12)
                    ?: return emptyList()
                val scores1 = readFloatGrid(out, "box_scores_1", DET_S1_SCALE, DET_S1_ZP, 512)
                    ?: return emptyList()
                val scores2 = readFloatGrid(out, "box_scores_2", DET_S2_SCALE, DET_S2_ZP, 384)
                    ?: return emptyList()

                val hits = ArrayList<DetHit>(TOP_K * 2)
                fun consider(idx: Int, logit: Float, coords: FloatArray, local: Int) {
                    if (asProb(logit) < MIN_DET_SCORE) return
                    val raw = FloatArray(12)
                    System.arraycopy(coords, local * 12, raw, 0, 12)
                    hits.add(DetHit(logit, idx, raw))
                }
                for (i in 0 until 512) consider(i, scores1[i], coords1, i)
                for (i in 0 until 384) consider(512 + i, scores2[i], coords2, i)
                if (hits.isEmpty()) {
                    // Still take global best — quantized zp=255 caps sigmoid at 0.5.
                    var bestLogit = Float.NEGATIVE_INFINITY
                    var bestIdx = -1
                    for (i in 0 until 512) if (scores1[i] > bestLogit) {
                        bestLogit = scores1[i]; bestIdx = i
                    }
                    for (i in 0 until 384) if (scores2[i] > bestLogit) {
                        bestLogit = scores2[i]; bestIdx = 512 + i
                    }
                    if (bestIdx >= 0) {
                        val raw = FloatArray(12)
                        if (bestIdx < 512) System.arraycopy(coords1, bestIdx * 12, raw, 0, 12)
                        else System.arraycopy(coords2, (bestIdx - 512) * 12, raw, 0, 12)
                        hits.add(DetHit(bestLogit, bestIdx, raw))
                    }
                }
                hits.sortByDescending { it.logit }
                val top = hits.take(TOP_K)
                val rois = ArrayList<OriRoi>(top.size)
                val scale = boxed.scale.coerceAtLeast(1e-3f)
                for (hit in top) {
                    decodeOriRoi(hit, boxed, scale, bitmap.width, bitmap.height)?.let { roi ->
                        rois.add(roi)
                        Log.d(
                            TAG,
                            "det idx=${hit.idx} p=${"%.2f".format(asProb(hit.logit))} " +
                                "roi=c(${"%.0f".format(roi.cx)},${"%.0f".format(roi.cy)}) " +
                                "s=${"%.0f".format(roi.side)} th=${"%.2f".format(roi.theta)}",
                        )
                    }
                }
                rois
            }
        }
    }

    private fun decodeOriRoi(
        hit: DetHit,
        boxed: Letterbox,
        scale: Float,
        frameW: Int,
        frameH: Int,
    ): OriRoi? {
        val idx = hit.idx
        val raw = hit.raw
        val ax = anchors[idx * 4]
        val ay = anchors[idx * 4 + 1]
        val aw = anchors[idx * 4 + 2]
        val ah = anchors[idx * 4 + 3]
        // zmurez BlazePose decode → normalized letterbox coords.
        fun decX(v: Float) = v / DET_SIZE * aw + ax
        fun decY(v: Float) = v / DET_SIZE * ah + ay
        fun toFrameX(nx: Float) = (nx * DET_SIZE - boxed.padX) / scale
        fun toFrameY(ny: Float) = (ny * DET_SIZE - boxed.padY) / scale

        // Alignment keypoints 2 (hip/mid) and 3 (upper body).
        var xc = toFrameX(decX(raw[8]))
        var yc = toFrameY(decY(raw[9]))
        val x1 = toFrameX(decX(raw[10]))
        val y1 = toFrameY(decY(raw[11]))
        var side = hypot(xc - x1, yc - y1) * 2f
        if (side < 40f || !xc.isFinite() || !yc.isFinite()) {
            xc = toFrameX(decX(raw[0]))
            yc = toFrameY(decY(raw[1]))
            val bw = raw[2] / DET_SIZE * aw
            val bh = raw[3] / DET_SIZE * ah
            side = max(bw, bh) * DET_SIZE / scale
        }
        if (side < 40f || !side.isFinite()) return null

        // BlazePose detection2roi: theta from kp2→kp3 before scaling center.
        val theta = atan2(yc - y1, xc - x1) - THETA0
        // dy=0, dscale=1.5
        yc += DET_DY * side
        side *= DET_DSCALE
        // Reject absurd boxes that cover the whole frame from noise.
        val maxSide = max(frameW, frameH) * 1.15f
        if (side > maxSide) side = min(frameW, frameH).toFloat() * 0.95f

        return OriRoi(xc, yc, side, theta)
    }

    /** Person-centric square crops for calibration (not full-portrait stretch). */
    private fun centerPersonCrops(bitmap: Bitmap): List<OriRoi> {
        val w = bitmap.width.toFloat()
        val h = bitmap.height.toFloat()
        val cx = w * 0.5f
        val cy = h * 0.42f // stand-here guide bias
        val minSide = min(w, h)
        return listOf(0.95f, 0.75f, 0.55f).map { frac ->
            OriRoi(cx, cy, minSide * frac, 0f)
        }
    }

    private fun runLandmark(bitmap: Bitmap, roi: OriRoi): Lm? {
        if (roi.side < 24f) return null
        val crop = Bitmap.createBitmap(LM_SIZE, LM_SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(crop)
        canvas.drawColor(Color.BLACK)

        // Rotated affine crop (zmurez extract_roi / AI Hub compute_box_affine).
        val half = roi.side / 2f
        val c = cos(roi.theta)
        val s = sin(roi.theta)
        // Unit square corners relative to center, then rotate+translate.
        // Order: top-left, bottom-left, top-right (matches AI Hub).
        fun corner(lx: Float, ly: Float): FloatArray {
            val x = c * lx - s * ly + roi.cx
            val y = s * lx + c * ly + roi.cy
            return floatArrayOf(x, y)
        }
        val tl = corner(-half, -half)
        val bl = corner(-half, half)
        val tr = corner(half, -half)
        val src = floatArrayOf(tl[0], tl[1], bl[0], bl[1], tr[0], tr[1])
        val dst = floatArrayOf(
            0f, 0f,
            0f, (LM_SIZE - 1).toFloat(),
            (LM_SIZE - 1).toFloat(), 0f,
        )
        val matrix = Matrix()
        if (!matrix.setPolyToPoly(src, 0, dst, 0, 3)) {
            // Fallback: axis-aligned stretch.
            val r = RectF(roi.cx - half, roi.cy - half, roi.cx + half, roi.cy + half)
            val srcR = Rect(
                r.left.toInt().coerceIn(0, bitmap.width - 1),
                r.top.toInt().coerceIn(0, bitmap.height - 1),
                r.right.toInt().coerceIn(1, bitmap.width),
                r.bottom.toInt().coerceIn(1, bitmap.height),
            )
            if (srcR.width() < 8 || srcR.height() < 8) return null
            canvas.drawBitmap(bitmap, srcR, Rect(0, 0, LM_SIZE, LM_SIZE), paint)
        } else {
            canvas.drawBitmap(bitmap, matrix, paint)
        }

        val nhwc = bitmapToUint8Nhwc(crop)
        val inputName = landmark.inputNames.first()
        val shape = longArrayOf(1, LM_SIZE.toLong(), LM_SIZE.toLong(), 3)
        val buffer = ByteBuffer.allocateDirect(nhwc.size).order(ByteOrder.nativeOrder())
        buffer.put(nhwc).rewind()
        return OnnxTensor.createTensor(env, buffer, shape, OnnxJavaType.UINT8).use { tensor ->
            landmark.run(mapOf(inputName to tensor)).use { out ->
                val score = readLandmarkScore(out)
                if (score < MIN_LM_WEAK) {
                    Log.d(TAG, "landmark score low: $score")
                    return null
                }
                val raw = readLandmarks(out) ?: return null
                val mapped = mapFromOriRoi(raw, roi, bitmap.width, bitmap.height)
                Lm(mapped, score)
            }
        }
    }

    private fun mapFromOriRoi(
        pts25: Array<FloatArray>,
        roi: OriRoi,
        frameW: Int,
        frameH: Int,
    ): List<FloatArray> {
        val out = Array(33) { floatArrayOf(0f, 0f) }
        val half = roi.side / 2f
        val c = cos(roi.theta)
        val s = sin(roi.theta)
        // Inverse of extract_roi: crop-normalized → rotated square → frame.
        for (i in 0 until 25) {
            var nx = pts25[i][0]
            var ny = pts25[i][1]
            if (nx > 1.5f) nx /= LM_SIZE
            if (ny > 1.5f) ny /= LM_SIZE
            // Point in crop [0,1] → local square [-half, half]
            val lx = (nx.coerceIn(0f, 1f) * 2f - 1f) * half
            val ly = (ny.coerceIn(0f, 1f) * 2f - 1f) * half
            val fx = (c * lx - s * ly + roi.cx) / frameW
            val fy = (s * lx + c * ly + roi.cy) / frameH
            out[i][0] = fx.coerceIn(0f, 1f)
            out[i][1] = fy.coerceIn(0f, 1f)
        }
        synthesizeFeet(out)
        return out.toList()
    }

    private fun synthesizeFeet(out: Array<FloatArray>) {
        val lHip = out[23]
        val rHip = out[24]
        val midHipY = (lHip[1] + rHip[1]) / 2f
        val footY = min(0.98f, midHipY + 0.22f)
        val span = max(0.04f, kotlin.math.abs(rHip[0] - lHip[0]))
        out[25] = floatArrayOf(lHip[0], midHipY + (footY - midHipY) * 0.45f)
        out[26] = floatArrayOf(rHip[0], midHipY + (footY - midHipY) * 0.45f)
        out[27] = floatArrayOf(lHip[0], footY)
        out[28] = floatArrayOf(rHip[0], footY)
        out[29] = floatArrayOf(lHip[0], footY)
        out[30] = floatArrayOf(rHip[0], footY)
        out[31] = floatArrayOf(lHip[0] - span * 0.15f, min(0.99f, footY + 0.01f))
        out[32] = floatArrayOf(rHip[0] + span * 0.15f, min(0.99f, footY + 0.01f))
    }

    private fun torsoLooksValid(lm: List<FloatArray>): Boolean {
        if (lm.size <= 24) return false
        val ls = lm[11]; val rs = lm[12]; val lh = lm[23]; val rh = lm[24]
        val shoulderW = kotlin.math.abs(rs[0] - ls[0])
        val hipW = kotlin.math.abs(rh[0] - lh[0])
        val midShoY = (ls[1] + rs[1]) / 2f
        val midHipY = (lh[1] + rh[1]) / 2f
        return shoulderW in 0.06f..0.55f &&
            hipW in 0.04f..0.55f &&
            midHipY > midShoY + 0.05f &&
            midShoY in 0.05f..0.75f
    }

    private fun roiFromLandmarks(lm: List<FloatArray>, frameW: Int, frameH: Int): OriRoi? {
        if (lm.size <= 24) return null
        val xs = listOf(11, 12, 23, 24).map { lm[it][0] * frameW }
        val ys = listOf(11, 12, 23, 24).map { lm[it][1] * frameH }
        val cx = xs.average().toFloat()
        val cy = ys.average().toFloat()
        val span = max(xs.max() - xs.min(), ys.max() - ys.min())
        val side = (span * 2.4f).coerceIn(64f, max(frameW, frameH).toFloat())
        return OriRoi(cx, cy, side, 0f)
    }

    private fun letterbox(src: Bitmap, size: Int): Letterbox {
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        canvas.drawColor(Color.BLACK)
        val scale = min(size.toFloat() / src.width, size.toFloat() / src.height)
        val dw = src.width * scale
        val dh = src.height * scale
        val padX = (size - dw) / 2f
        val padY = (size - dh) / 2f
        canvas.drawBitmap(
            src,
            Rect(0, 0, src.width, src.height),
            RectF(padX, padY, padX + dw, padY + dh),
            paint,
        )
        return Letterbox(out, scale, padX, padY)
    }

    private fun readLandmarkScore(out: OrtSession.Result): Float {
        try {
            val opt = out.get("scores")
            if (opt.isPresent) {
                val flat = when (val v = opt.get().value) {
                    is ByteArray -> floatArrayOf((v[0].toInt() and 0xff) * LM_SCORE_SCALE)
                    is FloatArray -> v
                    else -> flattenFloats(v)
                }
                val x = flat.firstOrNull() ?: return 0f
                return when {
                    x > 1.5f -> (x - LM_SCORE_ZP) * LM_SCORE_SCALE // still quantized 0..255
                    x in 0f..1f -> x
                    else -> sigmoid(x)
                }
            }
        } catch (_: Exception) {
        }
        return 0f
    }

    private fun readLandmarks(out: OrtSession.Result): Array<FloatArray>? {
        for (name in listOf("landmarks", "landmark")) {
            try {
                val opt = out.get(name)
                if (!opt.isPresent) continue
                val value = opt.get().value
                val flat = when (value) {
                    is ByteArray -> FloatArray(value.size) { i ->
                        ((value[i].toInt() and 0xff) - LM_ZP) * LM_SCALE
                    }
                    is FloatArray -> dequantLandmarks(value)
                    else -> dequantLandmarks(flattenFloats(value))
                }
                val n = when {
                    flat.size >= 25 * 4 -> 4
                    flat.size >= 25 * 2 -> 2
                    else -> continue
                }
                return Array(25) { i -> floatArrayOf(flat[i * n], flat[i * n + 1]) }
            } catch (_: Exception) {
            }
        }
        return null
    }

    private fun dequantLandmarks(src: FloatArray): FloatArray {
        var minV = Float.POSITIVE_INFINITY
        var maxV = Float.NEGATIVE_INFINITY
        var intish = 0
        val probe = min(src.size, 64)
        for (i in 0 until probe) {
            val v = src[i]
            if (v < minV) minV = v
            if (v > maxV) maxV = v
            if (kotlin.math.abs(v - v.toInt()) < 1e-3f) intish++
        }
        val looksQuant = minV >= -1f && maxV <= 260f && maxV > 2f && intish >= probe / 2
        return if (looksQuant) {
            FloatArray(src.size) { i -> (src[i] - LM_ZP) * LM_SCALE }
        } else {
            src
        }
    }

    private fun readFloatGrid(
        out: OrtSession.Result,
        name: String,
        scale: Float,
        zp: Int,
        expected: Int,
    ): FloatArray? {
        return try {
            val opt = out.get(name)
            if (!opt.isPresent) return null
            when (val v = opt.get().value) {
                is ByteArray -> {
                    if (v.size < expected) return null
                    FloatArray(expected) { i -> ((v[i].toInt() and 0xff) - zp) * scale }
                }
                is FloatArray -> {
                    val src = if (v.size >= expected) v else flattenFloats(v)
                    if (src.size < expected) return null
                    dequantIfNeeded(src, expected, scale, zp)
                }
                else -> {
                    val src = flattenFloats(v)
                    if (src.size < expected) return null
                    dequantIfNeeded(src, expected, scale, zp)
                }
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun dequantIfNeeded(
        src: FloatArray,
        expected: Int,
        scale: Float,
        zp: Int,
    ): FloatArray {
        var minV = Float.POSITIVE_INFINITY
        var maxV = Float.NEGATIVE_INFINITY
        var intish = 0
        val probe = min(expected, min(src.size, 64))
        for (i in 0 until probe) {
            val v = src[i]
            if (v < minV) minV = v
            if (v > maxV) maxV = v
            if (kotlin.math.abs(v - v.toInt()) < 1e-3f) intish++
        }
        val looksQuant = minV >= -1f && maxV <= 260f && maxV > 2f && intish >= probe / 2
        return if (looksQuant) {
            FloatArray(expected) { i -> (src[i] - zp) * scale }
        } else {
            src.copyOf(expected)
        }
    }

    private fun flattenFloats(value: Any?): FloatArray {
        return when (value) {
            is FloatArray -> value
            is Array<*> -> {
                val acc = ArrayList<Float>()
                fun walk(v: Any?) {
                    when (v) {
                        is Float -> acc.add(v)
                        is Number -> acc.add(v.toFloat())
                        is FloatArray -> v.forEach { acc.add(it) }
                        is ByteArray -> v.forEach { b -> acc.add((b.toInt() and 0xff).toFloat()) }
                        is Array<*> -> v.forEach { walk(it) }
                    }
                }
                walk(value)
                acc.toFloatArray()
            }
            is ByteArray -> FloatArray(value.size) { i -> (value[i].toInt() and 0xff).toFloat() }
            else -> floatArrayOf()
        }
    }

    private fun sigmoid(x: Float): Float {
        val z = x.coerceIn(-20f, 20f)
        return 1f / (1f + exp(-z))
    }

    private fun asProb(logit: Float): Float = sigmoid(logit)

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
        tracked = null
        try { detector.close() } catch (_: Exception) {}
        try { landmark.close() } catch (_: Exception) {}
    }

    companion object {
        private const val TAG = "NpuPoseEngine"
        private const val DET_SIZE = 128
        private const val LM_SIZE = 256
        private const val NUM_ANCHORS = 896
        private const val TOP_K = 6
        private const val DET_DSCALE = 1.5f
        private const val DET_DY = 0f
        private const val THETA0 = (Math.PI / 2.0).toFloat()
        // Dequantized detector logits are ≤ 0 (zp=255), so sigmoid ≤ ~0.5.
        private const val MIN_DET_SCORE = 0.30f
        private const val MIN_LM_SCORE = 0.18f
        private const val MIN_LM_WEAK = 0.10f
        private const val LM_SCORE_SCALE = 0.00390625f
        private const val LM_SCORE_ZP = 0
        private const val LM_SCALE = 0.006140740588307381f
        private const val LM_ZP = 112
        private const val DET_C1_SCALE = 0.7927474975585938f
        private const val DET_C1_ZP = 89
        private const val DET_C2_SCALE = 1.2209054231643677f
        private const val DET_C2_ZP = 99
        private const val DET_S1_SCALE = 5.552783966064453f
        private const val DET_S1_ZP = 255
        private const val DET_S2_SCALE = 4.833410263061523f
        private const val DET_S2_ZP = 254
        private const val ASSET_DIR = "npu"

        fun create(context: Context): NpuPoseEngine? {
            return try {
                HtpNative.prepare(context)
                val dir = File(context.filesDir, ASSET_DIR)
                dir.mkdirs()
                val needed = listOf(
                    "pose_detector.onnx",
                    "pose_detector_qairt_context.bin",
                    "pose_landmark_detector.onnx",
                    "pose_landmark_detector_qairt_context.bin",
                    "anchors_pose.bin",
                    "metadata.json",
                )
                for (name in needed) {
                    val dest = File(dir, name)
                    // Refresh anchors always; refresh tiny onnx wrappers too (point at context bins).
                    val force = name == "anchors_pose.bin" || name.endsWith(".onnx") || name == "metadata.json"
                    if (force || !dest.exists() || dest.length() == 0L) {
                        context.assets.open("$ASSET_DIR/$name").use { input ->
                            dest.outputStream().use { input.copyTo(it) }
                        }
                    }
                }
                val anchors = loadAnchors(File(dir, "anchors_pose.bin"))
                val env = OrtEnvironment.getEnvironment()
                val det = HtpNative.openQnnSession(env, File(dir, "pose_detector.onnx"))
                val lm = HtpNative.openQnnSession(env, File(dir, "pose_landmark_detector.onnx"))
                Log.i(TAG, "NPU pose pipeline ready (detector + landmark)")
                NpuPoseEngine(env, det, lm, anchors)
            } catch (e: Exception) {
                Log.e(TAG, "NPU pose init failed", e)
                null
            }
        }

        private fun loadAnchors(file: File): FloatArray {
            val bytes = file.readBytes()
            require(bytes.size >= NUM_ANCHORS * 4 * 4) {
                "anchors_pose.bin too small: ${bytes.size}"
            }
            val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            return FloatArray(NUM_ANCHORS * 4) { buf.float }
        }
    }
}
