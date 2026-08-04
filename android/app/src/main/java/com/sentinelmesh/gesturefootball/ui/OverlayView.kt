package com.sentinelmesh.gesturefootball.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer

class OverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private var points: List<FloatArray>? = null
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

    fun setLandmarks(pts: List<FloatArray>?) {
        points = pts
        postInvalidateOnAnimation()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val pts = points ?: return
        // PreviewView is typically mirrored for front camera; landmarks are image-space.
        // Flip X to match the mirrored preview.
        for ((i, p) in pts.withIndex()) {
            val x = (1f - p[0]) * width
            val y = p[1] * height
            val paint = when (i) {
                PoseAnalyzer.L_WRI, PoseAnalyzer.R_WRI -> wrist
                PoseAnalyzer.L_FOOT, PoseAnalyzer.R_FOOT -> foot
                else -> joint
            }
            val r = if (paint === joint) 4f else 8f
            canvas.drawCircle(x, y, r, paint)
        }
    }
}
