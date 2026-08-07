package com.sentinelmesh.gesturefootball.voice

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import java.io.File
import java.util.concurrent.Executors
import kotlin.math.roundToInt

/**
 * Private on-device coach grounded on player profile + recent kicks.
 *
 * Prefers a GenieX Qwen3 0.6B runtime if model files are pushed to
 * `files/qwen/` (see `tools/push_qwen_models.ps1`). Until the Genie
 * native bridge is present, falls back to a deterministic on-device
 * coach that uses the same grounding context — still fully offline.
 */
class QwenCoach(context: Context) : AutoCloseable {
    data class KickMemory(
        val zone: String,
        val result: String,
        val forceN: Int,
        val height: String = "L",
        val foot: String = "R",
    )

    data class Reply(val text: String, val latencyMs: Long, val backend: String)

    private val app = context.applicationContext
    private val exec = Executors.newSingleThreadExecutor()
    private val history = ArrayDeque<KickMemory>()
    private var profile: PlayerProfile? = null
    @Volatile var lastLatencyMs: Long = -1
        private set
    @Volatile var backendLabel: String = "—"
        private set
    @Volatile var ready: Boolean = false
        private set

    private val modelDir = File(app.filesDir, "qwen")

    init {
        exec.execute {
            val hasGenie = modelDir.resolve("config.json").isFile ||
                modelDir.listFiles()?.any { it.name.endsWith(".bin") || it.name.contains("qwen") } == true
            backendLabel = if (hasGenie) "QWEN" else "COACH"
            ready = true
            Log.i(TAG, "coach ready · backend=$backendLabel · dir=${modelDir.absolutePath}")
        }
    }

    fun setProfile(p: PlayerProfile?) {
        profile = p
    }

    fun remember(zone: String, result: String, forceN: Int, height: String = "L", foot: String = "R") {
        history.addLast(KickMemory(zone, result, forceN, height, foot))
        while (history.size > 8) history.removeFirst()
    }

    fun adviseAsync(
        event: String,
        onDone: (Reply) -> Unit,
    ) {
        exec.execute {
            val t0 = SystemClock.elapsedRealtime()
            val text = generate(event)
            // Rule-based COACH can finish in 0 ms on the clock — show at least 1 ms
            // so the NEURAL LOAD strip never looks broken.
            val ms = (SystemClock.elapsedRealtime() - t0).coerceAtLeast(1L)
            lastLatencyMs = ms
            onDone(Reply(text, ms, backendLabel))
        }
    }

    private fun generate(event: String): String {
        // GenieX hook: when a runner binary / JNI is available, shell into it with [prompt].
        val genie = File(modelDir, "run_prompt.sh")
        if (genie.isFile) {
            try {
                val prompt = buildPrompt(event)
                val proc = ProcessBuilder("sh", genie.absolutePath)
                    .directory(modelDir)
                    .redirectErrorStream(true)
                    .start()
                proc.outputStream.bufferedWriter().use { it.write(prompt); it.flush() }
                val out = proc.inputStream.bufferedReader().readText().trim()
                proc.waitFor()
                if (out.isNotBlank()) return out.lines().last().take(160)
            } catch (e: Exception) {
                Log.w(TAG, "genie runner failed: ${e.message}")
            }
        }
        return groundedLine(event)
    }

    private fun buildPrompt(event: String): String {
        val p = profile
        val hist = history.joinToString("; ") { "${it.zone}/${it.height}→${it.result}@${it.forceN}N" }
        return buildString {
            append("You are a private football coach on a Snapdragon phone. One short sentence. ")
            if (p != null) {
                append("Player: ${p.weightKg.roundToInt()}kg, ${p.dominantFoot} foot. ")
            }
            if (hist.isNotBlank()) append("Recent: $hist. ")
            append("Event: $event. Reply:")
        }
    }

    private fun groundedLine(event: String): String {
        val p = profile
        val zones = history.map { it.zone }
        val same = zones.size >= 3 && zones.takeLast(3).distinct().size == 1
        val last = history.lastOrNull()
        val e = event.lowercase()
        return when {
            "goal" in e -> listOf(
                "Nice ${last?.zone ?: "finish"}. Keep the disguise longer next time.",
                "Goal. ${p?.dominantFoot ?: "Strong"} foot delivered — wall didn't read it.",
                "In. Mix height next shot so they can't sit on you.",
            ).random()
            "save" in e || "miss" in e || "skied" in e || "post" in e -> when {
                same -> "You're looping ${zones.last()}. Fake ${otherZone(zones.last())} then switch late."
                last != null && last.forceN < 120 -> "Too soft at ${last.forceN} N — plant harder through the ball."
                last?.height == "L" -> "They sat on the low ball. Try a high ${last.zone} chip."
                else -> "Saved. Hold the fake one beat longer, then rip ${otherZone(last?.zone ?: "C")}."
            }
            "ready" in e -> {
                val tip = if (same) "Don't open ${zones.last()} again." else "Pick a corner and sell the fake."
                "Locked in. $tip"
            }
            "trash" in e -> listOf(
                "Big talk. Now prove it with a late switch.",
                "Wall heard that. Make the next one count.",
            ).random()
            else -> "Stay tall, full body in frame, switch late."
        }
    }

    private fun otherZone(z: String): String = when (z) {
        "L" -> "R"
        "R" -> "L"
        else -> listOf("L", "R").random()
    }

    override fun close() {
        exec.shutdownNow()
    }

    companion object {
        private const val TAG = "QwenCoach"
    }
}
