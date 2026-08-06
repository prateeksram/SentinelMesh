package com.sentinelmesh.gesturefootball.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import com.sentinelmesh.gesturefootball.pose.BodyGuide
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer

class OverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private var points: List<FloatArray>? = null
    private var showGuide = false
    private var guideOk = false

    private val joint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(160, 244, 247, 241)
        style = Paint.Style.FILL
    }
    private val wrist = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#FFC400")
        style = Paint.Style.FILL
    }
    private val foot = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#3EC7F4")
        style = Paint.Style.FILL
    }
    private val guideStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        strokeCap = Paint.Cap.ROUND
    }
    private val guideFill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val guideLabel = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = 36f
        isFakeBoldText = true
    }
    private val guideRect = RectF()

    fun setLandmarks(pts: List<FloatArray>?) {
        points = pts
        postInvalidateOnAnimation()
    }

    /** Show stand-here silhouette; [ok] turns the frame green when the body fills it. */
    fun setBodyGuide(show: Boolean, ok: Boolean) {
        if (showGuide == show && guideOk == ok) return
        showGuide = show
        guideOk = ok
        postInvalidateOnAnimation()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (showGuide) drawGuide(canvas)

        val pts = points ?: return
        // PreviewView is typically mirrored for front camera; landmarks are image-space.
        // Flip X to match the mirrored preview.
        for ((i, p) in pts.withIndex()) {
            val x = (1f - p[0]) * width
            val y = p[1] * height
            val paint = when (i) {
                PoseAnalyzer.L_WRI, PoseAnalyzer.R_WRI -> wrist
                PoseAnalyzer.L_FOOT, PoseAnalyzer.R_FOOT,
                PoseAnalyzer.L_ANK, PoseAnalyzer.R_ANK,
                -> foot
                else -> joint
            }
            val r = if (paint === joint) 4f else 8f
            canvas.drawCircle(x, y, r, paint)
        }
    }

    private fun drawGuide(canvas: Canvas) {
        val color = if (guideOk) Color.parseColor("#3DDC74") else Color.parseColor("#FFC400")
        // Mirror X so guide matches what the user sees in the front-camera preview.
        val left = (1f - BodyGuide.RIGHT) * width
        val right = (1f - BodyGuide.LEFT) * width
        val top = BodyGuide.TOP * height
        val bottom = BodyGuide.BOTTOM * height
        guideRect.set(left, top, right, bottom)

        guideFill.color = Color.argb(if (guideOk) 36 else 28, Color.red(color), Color.green(color), Color.blue(color))
        canvas.drawRoundRect(guideRect, 28f, 28f, guideFill)

        guideStroke.color = color
        guideStroke.strokeWidth = if (guideOk) 6f else 4f
        val corner = minOf(width, height) * 0.07f
        drawCorners(canvas, guideRect, corner)

        guideLabel.color = color
        guideLabel.textSize = height * 0.045f
        val label = if (guideOk) "IN FRAME ✓" else "STAND HERE"
        canvas.drawText(label, guideRect.centerX(), guideRect.top + height * 0.06f, guideLabel)
    }

    private fun drawCorners(canvas: Canvas, r: RectF, len: Float) {
        val paths = floatArrayOf(
            r.left, r.top + len, r.left, r.top, r.left + len, r.top,
            r.right - len, r.top, r.right, r.top, r.right, r.top + len,
            r.left, r.bottom - len, r.left, r.bottom, r.left + len, r.bottom,
            r.right - len, r.bottom, r.right, r.bottom, r.right, r.bottom - len,
        )
        // Draw as separate polylines of 3 points each
        var i = 0
        while (i < paths.size) {
            canvas.drawLine(paths[i], paths[i + 1], paths[i + 2], paths[i + 3], guideStroke)
            canvas.drawLine(paths[i + 2], paths[i + 3], paths[i + 4], paths[i + 5], guideStroke)
            i += 6
        }
    }
}
