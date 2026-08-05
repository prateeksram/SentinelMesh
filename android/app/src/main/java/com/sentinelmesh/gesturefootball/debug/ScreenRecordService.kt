package com.sentinelmesh.gesturefootball.debug

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.min

/**
 * Debug screen recording: captures exactly what the app shows (camera preview +
 * overlays + hints) into files/Movies/gf_screen_<stamp>.mp4 for adb pull later.
 *
 * MediaProjection requires a foreground service with type mediaProjection on
 * Android 10+, and a registered callback on Android 14+.
 */
class ScreenRecordService : Service() {

    private var projection: MediaProjection? = null
    private var recorder: MediaRecorder? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var outFile: File? = null
    private val main = Handler(Looper.getMainLooper())
    private val stopCap = Runnable { finishRecording() }

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            // User revoked via the status-bar cast chip — finalize what we have.
            main.post { finishRecording() }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val code = intent.getIntExtra(EXTRA_CODE, Activity.RESULT_CANCELED)
                val data = if (Build.VERSION.SDK_INT >= 33) {
                    intent.getParcelableExtra(EXTRA_DATA, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION") intent.getParcelableExtra(EXTRA_DATA)
                }
                if (code != Activity.RESULT_OK || data == null) {
                    stopSelf()
                } else {
                    startAsForeground()
                    startRecording(code, data)
                }
            }
            ACTION_STOP -> finishRecording()
        }
        return START_NOT_STICKY
    }

    private fun startAsForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Debug screen recording", NotificationManager.IMPORTANCE_LOW)
        )
        val n: Notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("Gesture Football")
            .setContentText("Recording the app screen for debugging")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    private fun startRecording(code: Int, data: Intent) {
        try {
            val dir = getExternalFilesDir(Environment.DIRECTORY_MOVIES)
                ?: File(filesDir, "Movies").also { it.mkdirs() }
            dir.mkdirs()
            // Keep only the last few clips so the folder stays pullable.
            dir.listFiles { f -> f.name.startsWith("gf_screen_") }
                ?.sortedByDescending { it.lastModified() }
                ?.drop(3)?.forEach { it.delete() }

            val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val file = File(dir, "gf_screen_$stamp.mp4")
            outFile = file

            // Scale to <=1280 on the long edge — keeps 5 min clips ~90 MB.
            val dm = resources.displayMetrics
            val scale = min(1f, 1280f / maxOf(dm.widthPixels, dm.heightPixels))
            val w = ((dm.widthPixels * scale).toInt() / 2) * 2
            val h = ((dm.heightPixels * scale).toInt() / 2) * 2

            val rec = if (Build.VERSION.SDK_INT >= 31) {
                MediaRecorder(this)
            } else {
                @Suppress("DEPRECATION") MediaRecorder()
            }
            rec.setVideoSource(MediaRecorder.VideoSource.SURFACE)
            rec.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            rec.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
            rec.setVideoEncodingBitRate(2_500_000)
            rec.setVideoFrameRate(30)
            rec.setVideoSize(w, h)
            rec.setOutputFile(file.absolutePath)
            rec.prepare()
            recorder = rec

            val mgr = getSystemService(MediaProjectionManager::class.java)
            val proj = mgr.getMediaProjection(code, data)
            proj.registerCallback(projectionCallback, main) // mandatory on Android 14+
            projection = proj
            virtualDisplay = proj.createVirtualDisplay(
                "gf-screen", w, h, dm.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                rec.surface, null, null,
            )
            rec.start()
            main.postDelayed(stopCap, MAX_MS)
            running = true
            status("REC · screen")
            Log.i(TAG, "screen recording → ${file.absolutePath}")
        } catch (e: Exception) {
            Log.e(TAG, "screen recording failed", e)
            status("REC failed · ${e.message}")
            cleanup(deleteFile = true)
            stopSelf()
        }
    }

    private fun finishRecording() {
        if (recorder == null) {
            stopSelf()
            return
        }
        val name = outFile?.name ?: "gf_screen.mp4"
        try {
            recorder?.stop()
            Log.i(TAG, "screen recording saved ${outFile?.length()} bytes → $name")
            cleanup(deleteFile = false)
            status("Saved · $name")
        } catch (e: Exception) {
            Log.e(TAG, "stop failed — dropping clip", e)
            cleanup(deleteFile = true)
            status("REC failed")
        }
        stopSelf()
    }

    private fun cleanup(deleteFile: Boolean) {
        main.removeCallbacks(stopCap)
        try {
            recorder?.reset()
            recorder?.release()
        } catch (_: Exception) {
        }
        recorder = null
        try {
            virtualDisplay?.release()
        } catch (_: Exception) {
        }
        virtualDisplay = null
        try {
            projection?.unregisterCallback(projectionCallback)
            projection?.stop()
        } catch (_: Exception) {
        }
        projection = null
        if (deleteFile) outFile?.delete()
        outFile = null
        running = false
    }

    override fun onDestroy() {
        cleanup(deleteFile = false)
        super.onDestroy()
    }

    private fun status(msg: String) {
        main.post { onStatus?.invoke(msg) }
    }

    companion object {
        private const val TAG = "ScreenRecord"
        private const val CHANNEL = "gf_screen_record"
        private const val NOTIF_ID = 41
        private const val ACTION_START = "com.sentinelmesh.gesturefootball.SCREEN_REC_START"
        private const val ACTION_STOP = "com.sentinelmesh.gesturefootball.SCREEN_REC_STOP"
        private const val EXTRA_CODE = "code"
        private const val EXTRA_DATA = "data"

        /** Safety cap so a forgotten recording still finalizes and stays small. */
        const val MAX_MS = 5 * 60_000L

        @Volatile
        var running = false
            private set

        /** UI feedback hook — set by MainActivity, invoked on the main thread. */
        @Volatile
        var onStatus: ((String) -> Unit)? = null

        fun start(context: Context, resultCode: Int, data: Intent) {
            val i = Intent(context, ScreenRecordService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_CODE, resultCode)
                .putExtra(EXTRA_DATA, data)
            context.startForegroundService(i)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, ScreenRecordService::class.java).setAction(ACTION_STOP)
            )
        }
    }
}
