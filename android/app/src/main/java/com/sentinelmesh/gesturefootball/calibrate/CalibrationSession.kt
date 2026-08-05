package com.sentinelmesh.gesturefootball.calibrate

import com.sentinelmesh.gesturefootball.forcepose.ForcePoseEngine
import com.sentinelmesh.gesturefootball.pose.BodyGuide
import com.sentinelmesh.gesturefootball.pose.PoseAnalyzer
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * On-device calibration state machine.
 * Steps: biometrics → T-pose → aim L/C/R → practice swing → profile.
 *
 * Hold steps auto-advance once the progress bar fills
 * (T-pose [TPOSE_HOLD_MS], aim corners [AIM_HOLD_MS]).
 */
class CalibrationSession {

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
        /** True while a hold timer is actively filling the bar. */
        val holding: Boolean = false,
        /** Spoken instruction for this state — empty means stay quiet. */
        val voice: String = "",
    )

    var step: Step = Step.BIOMETRICS
        private set

    var heightCm: Float = 175f
    var weightKg: Float = 75f

    private var torsoM: Float = PlayerProfile.torsoMetresFromHeight(175f)
    private var kickMs: Float = PlayerProfile.DEFAULT_KICK_MS
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
    private val wristSamples = ArrayList<Float>(48)
    private var practicePeakSpeed = 0f
    private var practicePeakFoot = "R"
    private var practicePeakForce = 0f
    private var swingArmed = false

    fun ui(): Ui = when (step) {
        Step.BIOMETRICS -> Ui(
            Step.BIOMETRICS,
            "YOUR BODY",
            "Enter height & weight — stays on this phone only.",
            0f,
            showBiometrics = true,
            voice = "Let's calibrate. Type your height and weight on the phone, " +
                "or just say skip to use defaults.",
        )
        Step.TPOSE -> holdUi(
            step = Step.TPOSE,
            needMs = TPOSE_HOLD_MS,
            readyHint = "Stand still, arms out like a T. Hold ${secs(TPOSE_HOLD_MS)}s",
            holdingTitle = "HOLD T-POSE",
            readyVoice = "Got you. Arms straight out like a T, and hold still.",
        )
        Step.AIM_L -> holdUi(
            step = Step.AIM_L,
            needMs = AIM_HOLD_MS,
            readyHint = "Raise your hand to YOUR left corner",
            holdingTitle = "AIM LEFT",
            readyVoice = "T pose locked. Now point your hand to your left corner and hold.",
        )
        Step.AIM_C -> holdUi(
            step = Step.AIM_C,
            needMs = AIM_HOLD_MS,
            readyHint = "Move your hand to the centre",
            holdingTitle = "AIM CENTRE",
            readyVoice = "Left locked. Move your hand to the center and hold.",
        )
        Step.AIM_R -> holdUi(
            step = Step.AIM_R,
            needMs = AIM_HOLD_MS,
            readyHint = "Raise your hand to YOUR right corner",
            holdingTitle = "AIM RIGHT",
            readyVoice = "Center locked. Now your right corner, and hold.",
        )
        Step.PRACTICE -> Ui(
            Step.PRACTICE,
            "PRACTICE SWING",
            "On KICK — swing hard once. Sets your threshold.",
            if (practicePeakSpeed > 0f) 1f else 0f,
            showBiometrics = false,
            canFinishSwing = practicePeakSpeed > 1.5f,
            voice = if (practicePeakSpeed > 1.5f)
                "Swing captured. Say done to save your profile, or swing again."
            else
                "All corners locked. Now take one hard practice swing.",
        )
        Step.DONE -> Ui(
            Step.DONE,
            "PROFILE SAVED",
            "Private biomechanics ready. ${"%.0f".format(weightKg)} kg · " +
                "torso ${"%.2f".format(torsoM)} m · kick ≥ ${"%.1f".format(kickMs)} m/s · $dominantFoot",
            1f,
            showBiometrics = false,
            voice = "Calibration complete.",
        )
    }

    private fun holdUi(
        step: Step,
        needMs: Long,
        readyHint: String,
        holdingTitle: String,
        readyVoice: String,
    ): Ui {
        val leftMs = (needMs - holdMs).coerceAtLeast(0L)
        val leftSec = leftMs / 1000f
        return when {
            !lastHasPose -> Ui(
                step,
                "FIND YOU",
                "Step into the outline — head to feet inside STAND HERE",
                progress = 0f, showBiometrics = false, holding = false,
                voice = "Step back so I can see your whole body, head to feet.",
            )
            !lastBodyOk -> Ui(
                step,
                "FIT THE FRAME",
                "Seen you — step back until the outline turns green",
                progress = 0f, showBiometrics = false, holding = false,
                voice = "I can see you. Step back until the outline turns green.",
            )
            !lastGestureOk -> Ui(
                step,
                "PERSON DETECTED",
                readyHint,
                progress = 0f, showBiometrics = false, holding = false,
                voice = readyVoice,
            )
            else -> Ui(
                step,
                holdingTitle,
                "Hold still… ${"%.1f".format(leftSec)}s left",
                progress = (holdMs / needMs.toFloat()).coerceIn(0f, 1f),
                showBiometrics = false,
                holding = true,
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
        advance(Step.TPOSE)
    }

    fun skipAimDefaults() {
        // Keep defaults; jump to practice if needed mid-flow.
        if (step == Step.AIM_L || step == Step.AIM_C || step == Step.AIM_R) {
            advance(Step.PRACTICE)
        }
    }

    /** Skip T-pose hold (still keeps height-based torso estimate). */
    fun skipTpose() {
        if (step == Step.TPOSE) advance(Step.AIM_L)
    }

    fun confirmPractice() {
        if (step != Step.PRACTICE || practicePeakSpeed < 1.5f) return
        // Threshold ≈ 55% of your hard swing so fidgets don't fire.
        kickMs = max(1.6f, practicePeakSpeed * 0.55f)
        dominantFoot = practicePeakFoot
        advance(Step.DONE)
    }

    fun buildProfile(): PlayerProfile = PlayerProfile(
        heightCm = heightCm,
        weightKg = weightKg,
        torsoM = torsoM,
        kickMs = kickMs,
        dominantFoot = dominantFoot,
        aimLMax = aimLMax,
        aimCMin = aimCMin,
        aimCMax = aimCMax,
        aimRMin = aimRMin,
    )

    /**
     * Feed pose each frame while calibrating (not BIOMETRICS / DONE).
     * @return true if step advanced this frame
     */
    fun onPose(
        nowMs: Long,
        bodyOk: Boolean,
        landmarks: List<FloatArray>?,
        wristXMirrored: Float?,
        liveForce: Float,
        kick: ForcePoseEngine.KickEvent?,
        kickFoot: String?,
        footSpeed: Float,
    ): Boolean {
        lastHasPose = landmarks != null
        lastBodyOk = lastHasPose && bodyOk
        if (!lastBodyOk) {
            holdMs = 0
            lastGestureOk = false
            lastTs = nowMs
            return false
        }
        val dt = if (lastTs == 0L) 0L else (nowMs - lastTs).coerceIn(0L, 80L)
        lastTs = nowMs

        return when (step) {
            Step.TPOSE -> tickTpose(dt, landmarks!!)
            Step.AIM_L -> tickAim(dt, wristXMirrored, target = "L")
            Step.AIM_C -> tickAim(dt, wristXMirrored, target = "C")
            Step.AIM_R -> tickAim(dt, wristXMirrored, target = "R")
            Step.PRACTICE -> {
                lastGestureOk = true
                tickPractice(liveForce, kick, kickFoot, footSpeed)
            }
            else -> false
        }
    }

    private fun tickTpose(dt: Long, landmarks: List<FloatArray>): Boolean {
        // Prefer loose guide T-pose; fall back to stricter classic check.
        lastGestureOk = BodyGuide.isLooseTpose(landmarks) || isTpose(landmarks)
        if (!lastGestureOk) {
            holdMs = 0
            return false
        }
        // Refine torso metres: stature × frac, nudged by arm-span ≈ height check.
        val heightM = heightCm / 100f
        val base = heightM * PlayerProfile.TORSO_FRAC
        val span = armSpanUnits(landmarks)
        val torso = torsoLenUnits(landmarks)
        if (span > 0.05f && torso > 0.05f) {
            // arm span in image ≈ height; torso_m = height_m * (torso/span)
            val fromSpan = heightM * (torso / span)
            torsoM = (0.65f * base + 0.35f * fromSpan).coerceIn(0.35f, 0.75f)
        } else {
            torsoM = base
        }
        holdMs += dt
        if (holdMs >= TPOSE_HOLD_MS) {
            advance(Step.AIM_L)
            return true
        }
        return false
    }

    private fun tickAim(dt: Long, wristX: Float?, target: String): Boolean {
        if (wristX == null) {
            holdMs = 0
            lastGestureOk = false
            wristSamples.clear()
            return false
        }
        val inZone = when (target) {
            "L" -> wristX < 0.38f
            "R" -> wristX > 0.62f
            else -> wristX in 0.38f..0.62f
        }
        lastGestureOk = inZone
        if (!inZone) {
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
                    advance(Step.AIM_C)
                }
                "C" -> {
                    aimCMin = max(0.30f, avg - 0.08f)
                    aimCMax = min(0.70f, avg + 0.08f)
                    advance(Step.AIM_R)
                }
                "R" -> {
                    aimRMin = max(0.58f, avg - 0.06f)
                    advance(Step.PRACTICE)
                }
            }
            return true
        }
        return false
    }

    private fun tickPractice(
        liveForce: Float,
        kick: ForcePoseEngine.KickEvent?,
        kickFoot: String?,
        footSpeed: Float,
    ): Boolean {
        if (!swingArmed) {
            swingArmed = true
            practicePeakSpeed = 0f
            practicePeakForce = 0f
        }
        if (footSpeed > practicePeakSpeed) {
            practicePeakSpeed = footSpeed
            if (kickFoot != null) practicePeakFoot = kickFoot
        }
        practicePeakForce = max(practicePeakForce, liveForce)
        if (kick != null) {
            practicePeakSpeed = max(practicePeakSpeed, 3.5f)
            if (kickFoot != null) practicePeakFoot = kickFoot
            confirmPractice()
            return true
        }
        return false
    }

    private fun advance(next: Step) {
        step = next
        holdMs = 0
        lastTs = 0
        lastGestureOk = false
        wristSamples.clear()
        if (next == Step.PRACTICE) {
            swingArmed = false
            practicePeakSpeed = 0f
        }
    }

    companion object {
        const val TPOSE_HOLD_MS = 2500L
        const val AIM_HOLD_MS = 1500L

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
