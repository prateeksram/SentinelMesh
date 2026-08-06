package com.sentinelmesh.gesturefootball.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Latest-frame JPEG renderer for the UNO Q camera relay. */
class RemoteCameraView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val lock = Any()
    private val worker = Executors.newSingleThreadExecutor()
    private val generation = AtomicInteger(0)
    private val client = OkHttpClient.Builder()
        .connectTimeout(2, TimeUnit.SECONDS)
        .readTimeout(2, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private var bitmap: Bitmap? = null
    private var activeUrl: String? = null
    private var mirror = true

    fun start(url: String, mirror: Boolean = true) {
        this.mirror = mirror
        if (activeUrl == url) return
        activeUrl = url
        val token = generation.incrementAndGet()
        worker.execute { poll(url, token) }
    }

    fun stop() {
        activeUrl = null
        generation.incrementAndGet()
        synchronized(lock) {
            bitmap?.recycle()
            bitmap = null
        }
        postInvalidate()
    }

    fun close() {
        stop()
        worker.shutdownNow()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    private fun poll(url: String, token: Int) {
        var lastSeq = -1L
        while (generation.get() == token && !Thread.currentThread().isInterrupted) {
            try {
                val request = Request.Builder()
                    .url(if (lastSeq >= 0L) "$url?after=$lastSeq" else url)
                    .header("Cache-Control", "no-cache")
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.code == 204) {
                        Thread.sleep(35)
                        return@use
                    }
                    if (!response.isSuccessful) {
                        Thread.sleep(150)
                        return@use
                    }
                    val seq = response.header("X-Edge-Seq")?.toLongOrNull() ?: -1L
                    if (seq == lastSeq) {
                        Thread.sleep(45)
                        return@use
                    }
                    val next = response.body?.byteStream()?.use(BitmapFactory::decodeStream)
                    if (next != null && generation.get() == token) {
                        synchronized(lock) {
                            val old = bitmap
                            bitmap = next
                            old?.recycle()
                        }
                        lastSeq = seq
                        postInvalidateOnAnimation()
                    }
                }
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            } catch (_: Exception) {
                try {
                    Thread.sleep(180)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                }
            }
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(Color.BLACK)
        synchronized(lock) {
            val frame = bitmap ?: return
            if (frame.isRecycled) return
            val scale = maxOf(width.toFloat() / frame.width, height.toFloat() / frame.height)
            val drawnWidth = frame.width * scale
            val drawnHeight = frame.height * scale
            val left = (width - drawnWidth) / 2f
            val top = (height - drawnHeight) / 2f
            canvas.save()
            if (mirror) canvas.scale(-1f, 1f, width / 2f, height / 2f)
            canvas.drawBitmap(
                frame,
                null,
                android.graphics.RectF(left, top, left + drawnWidth, top + drawnHeight),
                paint,
            )
            canvas.restore()
        }
    }
}
