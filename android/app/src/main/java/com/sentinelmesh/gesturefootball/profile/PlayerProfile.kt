package com.sentinelmesh.gesturefootball.profile

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * Private on-device biomechanical profile. Never uploaded.
 * Written to filesDir/player_profile.json after the calibration flow.
 */
data class PlayerProfile(
    val heightCm: Float,
    val weightKg: Float,
    /** Mid-shoulder → mid-hip length in metres (replaces the 0.52 m guess). */
    val torsoM: Float,
    /** Min foot speed (m/s) to count as a kick — personalised from practice swing. */
    val kickMs: Float,
    /** Separate threshold for the lower-rate UNO Q detector; absent in legacy profiles. */
    val unoQKickMs: Float? = null,
    /** Min wrist speed (m/s) for darts/basketball throws; null = use engine default. */
    val throwMs: Float? = null,
    /** Dominant kicking foot / throwing hand from practice. */
    val dominantFoot: String = "R",
    /** Selected sport: football | darts | basketball. */
    val sport: String = SPORT_FOOTBALL,
    /** Mirrored wrist-X thresholds for L / C / R aim (user's left = L). */
    val aimLMax: Float = 0.34f,
    val aimCMin: Float = 0.40f,
    val aimCMax: Float = 0.60f,
    val aimRMin: Float = 0.66f,
    val calibratedAt: Long = System.currentTimeMillis(),
) {
    val usesHandThrow: Boolean get() = isHandSport(sport)

    fun toJson(): JSONObject = JSONObject()
        .put("heightCm", heightCm.toDouble())
        .put("weightKg", weightKg.toDouble())
        .put("torsoM", torsoM.toDouble())
        .put("kickMs", kickMs.toDouble())
        .apply { unoQKickMs?.let { put("unoQKickMs", it.toDouble()) } }
        .apply { throwMs?.let { put("throwMs", it.toDouble()) } }
        .put("dominantFoot", dominantFoot)
        .put("sport", sport)
        .put("aimLMax", aimLMax.toDouble())
        .put("aimCMin", aimCMin.toDouble())
        .put("aimCMax", aimCMax.toDouble())
        .put("aimRMin", aimRMin.toDouble())
        .put("calibratedAt", calibratedAt)

    companion object {
        const val FILENAME = "player_profile.json"
        /** Winter-ish torso fraction of stature (shoulder→hip). */
        const val TORSO_FRAC = 0.288f
        const val DEFAULT_KICK_MS = 3.0f
        const val SPORT_FOOTBALL = "football"
        const val SPORT_DARTS = "darts"
        const val SPORT_BASKETBALL = "basketball"
        val SPORTS = setOf(SPORT_FOOTBALL, SPORT_DARTS, SPORT_BASKETBALL)

        fun isHandSport(sport: String): Boolean =
            sport == SPORT_DARTS || sport == SPORT_BASKETBALL

        fun normalizeSport(raw: String?): String {
            val s = raw?.lowercase()?.trim().orEmpty()
            return if (s in SPORTS) s else SPORT_FOOTBALL
        }

        fun fromJson(o: JSONObject): PlayerProfile = PlayerProfile(
            heightCm = o.getDouble("heightCm").toFloat(),
            weightKg = o.getDouble("weightKg").toFloat(),
            torsoM = o.getDouble("torsoM").toFloat(),
            kickMs = o.getDouble("kickMs").toFloat(),
            unoQKickMs = if (o.has("unoQKickMs")) {
                o.optDouble("unoQKickMs").toFloat()
            } else {
                null
            },
            throwMs = if (o.has("throwMs")) {
                o.optDouble("throwMs").toFloat()
            } else {
                null
            },
            dominantFoot = o.optString("dominantFoot", "R"),
            sport = normalizeSport(o.optString("sport", SPORT_FOOTBALL)),
            aimLMax = o.optDouble("aimLMax", 0.34).toFloat(),
            aimCMin = o.optDouble("aimCMin", 0.40).toFloat(),
            aimCMax = o.optDouble("aimCMax", 0.60).toFloat(),
            aimRMin = o.optDouble("aimRMin", 0.66).toFloat(),
            calibratedAt = o.optLong("calibratedAt", System.currentTimeMillis()),
        )

        fun torsoMetresFromHeight(heightCm: Float): Float =
            (heightCm / 100f) * TORSO_FRAC
    }
}

object PlayerProfileStore {
    fun file(context: Context): File = File(context.filesDir, PlayerProfile.FILENAME)

    fun load(context: Context): PlayerProfile? {
        val f = file(context)
        if (!f.exists()) return null
        return try {
            PlayerProfile.fromJson(JSONObject(f.readText()))
        } catch (_: Exception) {
            null
        }
    }

    fun save(context: Context, profile: PlayerProfile) {
        file(context).writeText(profile.toJson().toString(2))
    }

    fun clear(context: Context) {
        file(context).delete()
    }
}
