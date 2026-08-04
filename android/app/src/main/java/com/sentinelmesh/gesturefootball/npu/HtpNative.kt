package com.sentinelmesh.gesturefootball.npu

import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.system.Os
import android.util.Log
import java.io.File

/** Shared Hexagon HTP / QNN session setup for pose + Whisper. */
object HtpNative {
    private const val TAG = "HtpNative"

    fun prepare(context: Context) {
        val nativeDir = context.applicationInfo.nativeLibraryDir
        val adsp = listOf(
            nativeDir,
            "/vendor/dsp/cdsp",
            "/vendor/lib/rfsa/adsp",
            "/vendor/dsp",
            "/dsp",
        ).joinToString(";")
        try {
            Os.setenv("ADSP_LIBRARY_PATH", adsp, true)
        } catch (_: Exception) {
        }
        val skel = File(nativeDir, "libQnnHtpV79Skel.so")
        if (skel.exists()) {
            Log.i(TAG, "HTP skel ready: ${skel.length()} bytes @ $nativeDir")
        } else {
            Log.w(TAG, "Missing skel at ${skel.absolutePath}")
        }
    }

    fun openQnnSession(env: OrtEnvironment, model: File): OrtSession {
        val attempts = listOf(
            mapOf("backend_type" to "htp", "htp_performance_mode" to "burst"),
            mapOf("backend_type" to "htp", "htp_performance_mode" to "high_performance"),
        )
        var last: Exception? = null
        for (opts in attempts) {
            try {
                val so = OrtSession.SessionOptions()
                so.addQnn(opts)
                val session = env.createSession(model.absolutePath, so)
                Log.i(TAG, "QNN HTP OK · ${model.name}")
                return session
            } catch (e: Exception) {
                last = e
            }
        }
        throw last ?: IllegalStateException("QNN session failed: ${model.name}")
    }
}
