package com.sentinelmesh.gesturefootball.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

/** Map ASR text → game actions + speak a short private coach line. */
class VoiceCoach(
    context: Context,
    private val onSpeaking: (Boolean) -> Unit = {},
) : TextToSpeech.OnInitListener {
    enum class Intent {
        READY, LEFT, CENTER, RIGHT, NEXT, SKIP, TRASH, UNKNOWN
    }

    data class Command(val intent: Intent, val reply: String)

    private var tts: TextToSpeech? = TextToSpeech(context.applicationContext, this)
    private var ready = false

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) {
            tts?.language = Locale.US
            tts?.setSpeechRate(1.05f)
            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = onSpeaking(true)
                override fun onDone(utteranceId: String?) = onSpeaking(false)
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = onSpeaking(false)
                override fun onStop(utteranceId: String?, interrupted: Boolean) = onSpeaking(false)
            })
        }
    }

    fun parse(text: String): Command {
        val t = text.lowercase(Locale.US)
        return when {
            Regex("\\b(ready|redvee|redd?y|let'?s go|lock in|i'?m ready|start( the)? (match|game)|yes)\\b").containsMatchIn(t) ->
                Command(Intent.READY, "")
            Regex("\\b(left|go left|far left)\\b").containsMatchIn(t) ->
                Command(Intent.LEFT, "Aiming left.")
            Regex("\\b(right|go right|far right)\\b").containsMatchIn(t) ->
                Command(Intent.RIGHT, "Aiming right.")
            Regex("\\b(center|centre|middle)\\b").containsMatchIn(t) ->
                Command(Intent.CENTER, "Through the middle.")
            Regex("\\b(easy|weak|wall|scared|you'?re done|come on)\\b").containsMatchIn(t) ->
                Command(
                    Intent.TRASH,
                    trashReplies.random(),
                )
            Regex("\\b(skip|skip it|pass)\\b").containsMatchIn(t) ->
                Command(Intent.SKIP, "")
            Regex("\\b(next|done|continue|finish|okay|ok)\\b").containsMatchIn(t) ->
                Command(Intent.NEXT, "")
            else -> Command(Intent.UNKNOWN, "")
        }
    }

    fun speak(line: String) {
        if (!ready || line.isBlank()) return
        tts?.speak(line, TextToSpeech.QUEUE_FLUSH, null, "gf-coach")
    }

    fun close() {
        tts?.stop()
        tts?.shutdown()
        tts = null
    }

    companion object {
        private val trashReplies = listOf(
            "Big talk. Now swing.",
            "The Wall heard that.",
            "Save it for the net.",
            "Noted. Prove it.",
        )
    }
}
