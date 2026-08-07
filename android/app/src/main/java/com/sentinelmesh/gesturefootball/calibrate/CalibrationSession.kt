package com.sentinelmesh.gesturefootball.calibrate

import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.forcepose.HandThrowEngine
import com.sentinelmesh.gesturefootball.pose.BodyGuide
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Conversational on-device calibration.
 *
 * Flow: biometrics → (wait ready) T-pose → (wait) aim L/C/R → (wait) 3 practice
 * releases → done. Practice is sport-specific: football = leg kicks;
 * darts/basketball = hand throws. Shared steps stay identical.
 */
class CalibrationSession(
    sport: String = PlayerProfile.SPORT_FOOTBALL,
) {
    val sport: String = PlayerProfile.normalizeSport(sport)
    val usesHandThrow: Boolean = PlayerProfile.isHandSport(this.sport)

    enum class Step {
        BIOMETRICS,
        TPOSE,
        AIM_L,
        AIM_C,
        AIM_R,
        PRACTICE,
        DONE,
    }

    data class Ui(
        val step: Step,
        val title: String,
        val hint: String,
        val progress: Float,
        val showBiometrics: Boolean,
        val canFinishSwing: Boolean = false,
        val holding: Boolean = false,
        /** Spoken instruction / correction — empty means stay quiet. */
        val voice: String = "",
        /** True while waiting for the player to say "ready" before capturing. */
        val waitingConfirm: Boolean = false,
        val swingIndex: Int = 0,
        val swingTotal: Int = PRACTICE_SWINGS,
        val usesHandThrow: Boolean = false,
    )

    var step: Step = Step.BIOMETRICS
        private set

    var heightCm: Float = 175f
    var weightKg: Float = 75f

    private var torsoM: Float = PlayerProfile.torsoMetresFromHeight(175f)
    private var kickMs: Float = ForcePoseEngine.FLOOR_MS
    private var throwMs: Float = HandThrowEngine.DEFAULT_THROW_MS
    private var dominantFoot: String = "R"

    private var aimLMax: Float = 0.34f
    private var aimCMin: Float = 0.40f
    private var aimCMax: Float = 0.60f
    private var aimRMin: Float = 0.66f

    private var holdMs = 0L
    private var lastTs = 0L
    private var lastHasPose = false
    private var lastBodyOk = false
    private var lastGestureOk = false
    private var lastProblem: String? = null
    private val wristSamples = ArrayList<Float>(48)

    /** After advancing into an aim step, require the wrist OUT of the zone first. */
    private var aimArmed = false
    private var outOfZoneMs = 0L

    /** Capture stages start gated — player must say ready / tap next. */
    private var waitingConfirm = false
    private var lastConfirmAskAt = 0L

    // Practice: collect 3 validated swings, take median peak.
    private val swingPeaks = ArrayList<Float>(PRACTICE_SWINGS)
    private val swingFeet = ArrayList<String>(PRACTICE_SWINGS)
    private var lastKickReject: String? = null
    private var thumbsHoldMs = 0L

    fun ui(): Ui = when (step) {
        Step.BIOMETRICS -> Ui(
            Step.BIOMETRICS,
            "YOUR BODY",
            "Enter height & weight — stays on this phone only.",
            0f,
            showBiometrics = true,
            voice = "Let's calibrate. Type your height and weight, then tap next.",
        )
        Step.TPOSE -> gatedHoldUi(
            step = Step.TPOSE,
            needMs = TPOSE_HOLD_MS,
            readyHint = "Stand still, arms out like a T. Hold ${secs(TPOSE_HOLD_MS)}s",
            holdingTitle = "HOLD T-POSE",
            confirmAsk = "Show a thumbs up when ready for the T pose. Or tap I'm Ready.",
            readyVoice = "Got you. Arms straight out like a T, and hold still.",
        )
        Step.AIM_L -> gatedHoldUi(
            step = Step.AIM_L,
            needMs = AIM_HOLD_MS,
            readyHint = "Raise your hand ABOVE your shoulder to YOUR left",
            holdingTitle = "AIM LEFT",
            confirmAsk = "Show a thumbs up when ready to aim left. Or tap I'm Ready.",
            readyVoice = "Point your hand to your left corner, above your shoulder, and hold.",
        )
        Step.AIM_C -> gatedHoldUi(
            step = Step.AIM_C,
            needMs = AIM_HOLD_MS,
            readyHint = "Raise your hand ABOVE your shoulder to the centre",
            holdingTitle = "AIM CENTRE",
            confirmAsk = "Show a thumbs up when ready for centre. Or tap I'm Ready.",
            readyVoice = "Left locked. Move your hand to the centre, above your shoulder, and hold.",
        )
        Step.AIM_R -> gatedHoldUi(
            step = Step.AIM_R,
            needMs = AIM_HOLD_MS,
            readyHint = "Raise your hand ABOVE your shoulder to YOUR right",
            holdingTitle = "AIM RIGHT",
            confirmAsk = "Show a thumbs up when ready to aim right. Or tap I'm Ready.",
            readyVoice = "Centre locked. Now your right corner, above your shoulder, and hold.",
        )
        Step.PRACTICE -> practiceUi()
        Step.DONE -> {
            val release = if (usesHandThrow) {
                "throw ≥ ${"%.1f".format(throwMs)} m/s · $dominantFoot hand"
            } else {
                "kick ≥ ${"%.1f".format(kickMs)} m/s · $dominantFoot foot"
            }
            Ui(
                Step.DONE,
                "PROFILE SAVED",
                "${sport.uppercase()} ready. ${"%.0f".format(weightKg)} kg · " +
                    "torso ${"%.2f".format(torsoM)} m · $release",
                1f,
                showBiometrics = false,
                voice = "Calibration complete.",
                usesHandThrow = usesHandThrow,
            )
        }
    }

    private fun practiceUi(): Ui {
        val n = swingPeaks.size
        if (usesHandThrow) {
            val target = when (sport) {
                PlayerProfile.SPORT_BASKETBALL -> "the hoop"
                else -> "the board"
            }
            if (waitingConfirm) {
                return Ui(
                    Step.PRACTICE,
                    "PRACTICE THROW",
                    "Show a thumbs up when ready for throw ${n + 1} of $PRACTICE_SWINGS. Or tap I'm Ready.",
                    progress = n / PRACTICE_SWINGS.toFloat(),
                    showBiometrics = false,
                    waitingConfirm = true,
                    swingIndex = n,
                    voice = "Show a thumbs up when ready, then throw $PRACTICE_SWINGS times toward $target.",
                    usesHandThrow = true,
                )
            }
            val rejectHint = when (lastKickReject) {
                "soft" -> "Harder — snap the throw like you mean it."
                "short" -> "Follow through — throw toward $target, don't just tap."
                else -> null
            }
            return Ui(
                Step.PRACTICE,
                "THROW ${n + 1} OF $PRACTICE_SWINGS",
                rejectHint ?: "Aim with your hand, then throw hard toward $target — ${n + 1} of $PRACTICE_SWINGS.",
                progress = n / PRACTICE_SWINGS.toFloat(),
                showBiometrics = false,
                canFinishSwing = n >= PRACTICE_SWINGS,
                swingIndex = n,
                voice = rejectHint
                    ?: "Throw hard toward $target. Throw ${n + 1} of $PRACTICE_SWINGS.",
                usesHandThrow = true,
            )
        }
        if (waitingConfirm) {
            return Ui(
                Step.PRACTICE,
                "PRACTICE SWING",
                "Show a thumbs up when ready for swing ${n + 1} of $PRACTICE_SWINGS. Or tap I'm Ready.",
                progress = n / PRACTICE_SWINGS.toFloat(),
                showBiometrics = false,
                waitingConfirm = true,
                swingIndex = n,
                voice = "Show a thumbs up when ready, then swing $PRACTICE_SWINGS times with your leg.",
                usesHandThrow = false,
            )
        }
        val rejectHint = when (lastKickReject) {
            "soft" -> "Harder — swing like you mean it."
            "short" -> "Follow through — kick the ball, don't just tap."
            else -> null
        }
        return Ui(
            Step.PRACTICE,
            "SWING ${n + 1} OF $PRACTICE_SWINGS",
            rejectHint ?: "Kick the real ball hard — swing ${n + 1} of $PRACTICE_SWINGS.",
            progress = n / PRACTICE_SWINGS.toFloat(),
            showBiometrics = false,
            canFinishSwing = n >= PRACTICE_SWINGS,
            swingIndex = n,
            voice = rejectHint
                ?: "Kick the real ball hard. Swing ${n + 1} of $PRACTICE_SWINGS.",
            usesHandThrow = false,
        )
    }

    private fun gatedHoldUi(
        step: Step,
        needMs: Long,
        readyHint: String,
        holdingTitle: String,
        confirmAsk: String,
        readyVoice: String,
    ): Ui {
        if (waitingConfirm) {
            return Ui(
                step,
                "THUMBS UP",
                confirmAsk,
                progress = (thumbsHoldMs / THUMBS_HOLD_MS.toFloat()).coerceIn(0f, 1f),
                showBiometrics = false,
                waitingConfirm = true,
                voice = confirmAsk,
            )
        }
        val leftMs = (needMs - holdMs).coerceAtLeast(0L)
        val leftSec = leftMs / 1000f
        val problem = lastProblem
        return when {
            !lastHasPose -> Ui(
                step, "FIND YOU",
                "Step into the outline — head to feet inside STAND HERE",
                progress = 0f, showBiometrics = false,
                voice = "Step back so I can see your whole body, head to feet.",
            )
            !lastBodyOk -> Ui(
                step, "FIT THE FRAME",
                "Seen you — step back until the outline turns green",
                progress = 0f, showBiometrics = false,
                voice = "I can see you. Step back until the outline turns green.",
            )
            problem != null -> Ui(
                step, "ADJUST",
                problem,
                progress = 0f, showBiometrics = false,
                voice = problem,
            )
            !lastGestureOk -> Ui(
                step, "PERSON DETECTED",
                readyHint,
                progress = 0f, showBiometrics = false,
                voice = readyVoice,
            )
            else -> Ui(
                step, holdingTitle,
                "Hold still… ${"%.1f".format(leftSec)}s left",
                progress = (holdMs / needMs.toFloat()).coerceIn(0f, 1f),
                showBiometrics = false,
                holding = true,
                voice = "Good — hold it there.",
            )
        }
    }

    private fun secs(ms: Long): String {
        val s = ms / 1000f
        return if (s == s.toInt().toFloat()) s.toInt().toString() else "%.1f".format(s)
    }

    fun submitBiometrics(heightCm: Float, weightKg: Float) {
        this.heightCm = heightCm.coerceIn(120f, 230f)
        this.weightKg = weightKg.coerceIn(35f, 160f)
        torsoM = PlayerProfile.torsoMetresFromHeight(this.heightCm)
        enterGated(Step.TPOSE)
    }

    /** Thumbs-up / button / spoken READY — arms the next capture stage. */
    fun confirmReady() {
        if (!waitingConfirm) return
        waitingConfirm = false
        lastConfirmAskAt = 0L
        holdMs = 0
        thumbsHoldMs = 0
        lastTs = 0
        lastGestureOk = false
        lastProblem = null
        aimArmed = false
        outOfZoneMs = 0
        wristSamples.clear()
    }

    fun skipAimDefaults() {
        if (step == Step.AIM_L || step == Step.AIM_C || step == Step.AIM_R) {
            enterGated(Step.PRACTICE)
        }
    }

    fun skipTpose() {
        if (step == Step.TPOSE) enterGated(Step.AIM_L)
    }

    /** Finish practice early only after at least one validated swing. */
    fun confirmPractice() {
        if (step != Step.PRACTICE || swingPeaks.isEmpty()) return
        finalizePractice()
    }

    fun buildProfile(): PlayerProfile = PlayerProfile(
        heightCm = heightCm,
        weightKg = weightKg,
        torsoM = torsoM,
        kickMs = kickMs,
        throwMs = if (usesHandThrow) throwMs else null,
        dominantFoot = dominantFoot,
        sport = sport,
        aimLMax = aimLMax,
        aimCMin = aimCMin,
        aimCMax = aimCMax,
        aimRMin = aimRMin,
    )

    /**
     * Feed pose each frame while calibrating (not BIOMETRICS / DONE / waitingConfirm).
     * @return true if step advanced this frame
     */
    fun onPose(
        nowMs: Long,
        bodyOk: Boolean,
        landmarks: List<FloatArray>?,
        wristXMirrored: Float?,
        wristY: Float?,
        shoulderY: Float?,
        liveForce: Float,
        kick: ForcePoseEngine.KickEvent?,
        kickFoot: String?,
        footSpeed: Float,
        kickReject: String? = null,
    ): Boolean {
        lastHasPose = landmarks != null
        lastBodyOk = lastHasPose && bodyOk
        if (kickReject != null) lastKickReject = kickReject

        if (waitingConfirm) {
            val dt = if (lastTs == 0L) 0L else (nowMs - lastTs).coerceIn(0L, 80L)
            lastTs = nowMs
            if (PoseAnalyzer.isThumbsUp(landmarks)) {
                thumbsHoldMs += dt
                if (thumbsHoldMs >= THUMBS_HOLD_MS) {
                    confirmReady()
                    return true
                }
            } else {
                thumbsHoldMs = 0
            }
            // Soft re-prompt every ~12s of silence (MainActivity speaks on voice change).
            if (nowMs - lastConfirmAskAt > 12_000L) lastConfirmAskAt = nowMs
            return false
        }

        if (!lastBodyOk && step != Step.PRACTICE) {
            holdMs = 0
            lastGestureOk = false
            lastProblem = if (!lastHasPose) {
                "Step back so I can see your whole body."
            } else {
                "Step back until the outline turns green."
            }
            lastTs = nowMs
            return false
        }
        val dt = if (lastTs == 0L) 0L else (nowMs - lastTs).coerceIn(0L, 80L)
        lastTs = nowMs

        return when (step) {
            Step.TPOSE -> tickTpose(dt, landmarks!!)
            Step.AIM_L -> tickAim(dt, wristXMirrored, wristY, shoulderY, "L")
            Step.AIM_C -> tickAim(dt, wristXMirrored, wristY, shoulderY, "C")
            Step.AIM_R -> tickAim(dt, wristXMirrored, wristY, shoulderY, "R")
            Step.PRACTICE -> {
                lastGestureOk = true
                lastProblem = null
                tickPractice(kick, kickFoot, footSpeed)
            }
            else -> false
        }
    }

    private fun tickTpose(dt: Long, landmarks: List<FloatArray>): Boolean {
        val problem = BodyGuide.tposeProblem(landmarks)
        lastProblem = problem
        lastGestureOk = problem == null && (
            BodyGuide.isLooseTpose(landmarks) || isTpose(landmarks)
            )
        if (!lastGestureOk) {
            holdMs = 0
            return false
        }
        val heightM = heightCm / 100f
        val base = heightM * PlayerProfile.TORSO_FRAC
        val span = armSpanUnits(landmarks)
        val torso = torsoLenUnits(landmarks)
        if (span > 0.05f && torso > 0.05f) {
            val fromSpan = heightM * (torso / span)
            torsoM = (0.65f * base + 0.35f * fromSpan).coerceIn(0.35f, 0.75f)
        } else {
            torsoM = base
        }
        holdMs += dt
        if (holdMs >= TPOSE_HOLD_MS) {
            enterGated(Step.AIM_L)
            return true
        }
        return false
    }

    private fun tickAim(
        dt: Long,
        wristX: Float?,
        wristY: Float?,
        shoulderY: Float?,
        target: String,
    ): Boolean {
        if (wristX == null || wristY == null || shoulderY == null) {
            holdMs = 0
            lastGestureOk = false
            lastProblem = "I can't see your hand — keep it in the frame."
            wristSamples.clear()
            return false
        }

        val inZone = when (target) {
            "L" -> wristX < 0.38f
            "R" -> wristX > 0.62f
            else -> wristX in 0.38f..0.62f
        }
        val aboveShoulder = wristY < shoulderY - 0.02f

        // Re-arm: must leave the zone (or drop below shoulder) before hold starts.
        if (!aimArmed) {
            if (!inZone || !aboveShoulder) {
                outOfZoneMs += dt
                if (outOfZoneMs >= 300L) aimArmed = true
            } else {
                outOfZoneMs = 0
            }
            holdMs = 0
            lastGestureOk = false
            lastProblem = "Drop your hand, then raise it to the ${zoneName(target)} corner."
            wristSamples.clear()
            return false
        }

        lastProblem = when {
            !aboveShoulder -> "Raise your hand higher, above your shoulder."
            !inZone && target == "L" ->
                if (wristX in 0.38f..0.62f) "That's the centre — move it further LEFT."
                else "Move your hand to your LEFT corner."
            !inZone && target == "R" ->
                if (wristX in 0.38f..0.62f) "That's the centre — move it further RIGHT."
                else "Move your hand to your RIGHT corner."
            !inZone -> "Move your hand to the CENTRE."
            else -> null
        }
        lastGestureOk = inZone && aboveShoulder
        if (!lastGestureOk) {
            holdMs = 0
            wristSamples.clear()
            return false
        }
        wristSamples.add(wristX)
        holdMs += dt
        if (holdMs >= AIM_HOLD_MS && wristSamples.isNotEmpty()) {
            val avg = wristSamples.average().toFloat()
            when (target) {
                "L" -> {
                    aimLMax = min(0.42f, avg + 0.06f)
                    enterGated(Step.AIM_C)
                }
                "C" -> {
                    aimCMin = max(0.30f, avg - 0.08f)
                    aimCMax = min(0.70f, avg + 0.08f)
                    enterGated(Step.AIM_R)
                }
                "R" -> {
                    aimRMin = max(0.58f, avg - 0.06f)
                    enterGated(Step.PRACTICE)
                }
            }
            return true
        }
        return false
    }

    private fun tickPractice(
        kick: ForcePoseEngine.KickEvent?,
        kickFoot: String?,
        footSpeed: Float,
    ): Boolean {
        if (kick == null) return false
        // Only count validated kicks from ForcePoseEngine.
        swingPeaks.add(kick.peakSpeed.coerceAtLeast(footSpeed))
        swingFeet.add(kickFoot ?: kick.foot)
        lastKickReject = null
        if (swingPeaks.size >= PRACTICE_SWINGS) {
            finalizePractice()
            return true
        }
        // Keep listening for the next swing — no READY re-gate between swings.
        waitingConfirm = false
        return false
    }

    private fun finalizePractice() {
        if (swingPeaks.isEmpty()) return
        val sorted = swingPeaks.sorted()
        val median = sorted[sorted.size / 2]
        // Threshold ≈ 55% of median hard release; never below the engine floor.
        if (usesHandThrow) {
            throwMs = max(HandThrowEngine.FLOOR_MS, median * 0.55f)
            // Keep a sane football kick floor if they later switch sports.
            kickMs = ForcePoseEngine.FLOOR_MS
        } else {
            kickMs = max(ForcePoseEngine.FLOOR_MS, median * 0.55f)
            throwMs = HandThrowEngine.DEFAULT_THROW_MS
        }
        dominantFoot = swingFeet.groupingBy { it }.eachCount()
            .maxByOrNull { it.value }?.key ?: "R"
        advance(Step.DONE)
    }

    private fun enterGated(next: Step) {
        advance(next)
        // Thumbs-up / READY once before first practice swing; aim stages still gate each hold.
        waitingConfirm = next != Step.DONE && next != Step.BIOMETRICS
        lastConfirmAskAt = System.currentTimeMillis()
        thumbsHoldMs = 0
    }

    private fun advance(next: Step) {
        step = next
        holdMs = 0
        thumbsHoldMs = 0
        lastTs = 0
        lastGestureOk = false
        lastProblem = null
        wristSamples.clear()
        aimArmed = false
        outOfZoneMs = 0
        if (next == Step.PRACTICE && swingPeaks.isEmpty()) {
            // keep peaks if mid-practice gate
        }
    }

    private fun zoneName(z: String) = when (z) {
        "L" -> "left"; "R" -> "right"; else -> "centre"
    }

    companion object {
        const val TPOSE_HOLD_MS = 2500L
        const val AIM_HOLD_MS = 1500L
        const val THUMBS_HOLD_MS = 700L
        const val PRACTICE_SWINGS = 3

        fun isTpose(lm: List<FloatArray>): Boolean = BodyGuide.isLooseTpose(lm)

        fun torsoLenUnits(lm: List<FloatArray>): Float {
            val sx = (lm[PoseAnalyzer.L_SHO][0] + lm[PoseAnalyzer.R_SHO][0]) / 2f
            val sy = (lm[PoseAnalyzer.L_SHO][1] + lm[PoseAnalyzer.R_SHO][1]) / 2f
            val hx = (lm[PoseAnalyzer.L_HIP][0] + lm[PoseAnalyzer.R_HIP][0]) / 2f
            val hy = (lm[PoseAnalyzer.L_HIP][1] + lm[PoseAnalyzer.R_HIP][1]) / 2f
            return hypot(sx - hx, sy - hy)
        }

        fun armSpanUnits(lm: List<FloatArray>): Float {
            val lx = lm[PoseAnalyzer.L_WRI][0]
            val ly = lm[PoseAnalyzer.L_WRI][1]
            val rx = lm[PoseAnalyzer.R_WRI][0]
            val ry = lm[PoseAnalyzer.R_WRI][1]
            return hypot(lx - rx, ly - ry)
        }
    }
}
