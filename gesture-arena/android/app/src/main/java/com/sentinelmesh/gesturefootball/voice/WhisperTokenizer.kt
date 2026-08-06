package com.sentinelmesh.gesturefootball.voice

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Lightweight decode-only tokenizer from HuggingFace tokenizer.json. */
class WhisperTokenizer private constructor(
    private val idToToken: Array<String?>,
) {
    fun decode(ids: List<Int>): String {
        val sb = StringBuilder()
        for (id in ids) {
            if (id < 0 || id >= idToToken.size) continue
            if (id in SPECIAL) continue
            val t = idToToken[id] ?: continue
            if (t.startsWith("<|") && t.endsWith("|>")) continue
            sb.append(t)
        }
        return sb.toString()
            .replace('Ġ', ' ')
            .replace('▁', ' ')
            .replace("</w>", " ")
            .trim()
            .replace(Regex("\\s+"), " ")
    }

    companion object {
        // multilingual whisper-tiny specials
        const val EOT = 50257
        const val SOT = 50258
        const val EN = 50259
        const val TRANSCRIBE = 50359
        const val NO_TIMESTAMPS = 50363
        private val SPECIAL = setOf(EOT, SOT, EN, TRANSCRIBE, NO_TIMESTAMPS, 50358, 50361, 50362)

        fun load(context: Context): WhisperTokenizer? {
            return try {
                val internal = File(context.filesDir, "whisper/tokenizer.json")
                val external = File(context.getExternalFilesDir(null), "whisper/tokenizer.json")
                val text = when {
                    internal.exists() -> internal.readText()
                    external.exists() -> external.readText()
                    else -> context.assets.open("whisper/tokenizer.json").bufferedReader().use { it.readText() }
                }
                fromJson(text)
            } catch (e: Exception) {
                e.printStackTrace()
                null
            }
        }

        private fun fromJson(text: String): WhisperTokenizer {
            val root = JSONObject(text)
            val model = root.getJSONObject("model")
            val vocab = model.getJSONObject("vocab")
            var maxId = 0
            val keys = vocab.keys()
            val map = HashMap<Int, String>(52000)
            while (keys.hasNext()) {
                val token = keys.next()
                val id = vocab.getInt(token)
                map[id] = token
                if (id > maxId) maxId = id
            }
            // added_tokens
            if (root.has("added_tokens")) {
                val arr = root.getJSONArray("added_tokens")
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    val id = o.getInt("id")
                    val content = o.getString("content")
                    map[id] = content
                    if (id > maxId) maxId = id
                }
            }
            val arr = arrayOfNulls<String>(maxId + 1)
            for ((id, tok) in map) arr[id] = tok
            return WhisperTokenizer(arr)
        }
    }
}
