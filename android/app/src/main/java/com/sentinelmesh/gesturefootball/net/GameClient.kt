package com.sentinelmesh.gesturefootball.net

import com.sentinelmesh.gesturefootball.pose.KickKinematicState
import com.sentinelmesh.gesturefootball.pose.ShotTrajectory
import com.sentinelmesh.gesturefootball.profile.PlayerProfile
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** WebSocket client speaking the same JSON protocol as phone.html. */
class GameClient(
    url: String = DEFAULT_URL,
    private val listener: Listener,
) {
    sealed class ConnectStatus {
        data class Connecting(val url: String) : ConnectStatus()
        data class Connected(val url: String) : ConnectStatus()
        data class Failed(val url: String, val message: String) : ConnectStatus()
        data class Disconnected(val url: String) : ConnectStatus()
    }

    interface Listener {
        fun onConnected(connected: Boolean)
        fun onConnectStatus(status: ConnectStatus) {}
        fun onEdgePose(frame: EdgePoseFrame) {}
        fun onState(state: MatchState)
    }

    data class EdgePoseFrame(
        val seq: Long,
        val captureNs: Long,
        val width: Int,
        val height: Int,
        val rotation: Int,
        val mirrored: Boolean,
        val landmarks: List<FloatArray>,
        val visibility: FloatArray,
        val inferenceMs: Long,
        val fps: Float,
        val flowMotion: EdgeFlowMotion? = null,
    )

    data class EdgeFlowFoot(
        val vxNorm: Float,
        val vyNorm: Float,
        val peakVxNorm: Float,
        val peakVyNorm: Float,
        val dxNorm: Float,
        val dyNorm: Float,
        val confidence: Float,
        val samples: Int,
    )

    data class EdgeFlowMotion(
        val timestampNs: Long,
        val fps: Float,
        val left: EdgeFlowFoot?,
        val right: EdgeFlowFoot?,
    )

    data class MatchState(
        val phase: String,
        val kick: Int,
        val kicksTotal: Int,
        val score: Int,
        val line: String,
        val timerMs: Int,
        val lastForce: Int?,
        val lastResult: String?,
        val raw: JSONObject,
    )

    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .retryOnConnectionFailure(true)
        .build()

    @Volatile var url: String = url
        private set
    private var ws: WebSocket? = null
    private val open = AtomicBoolean(false)
    private val allowReconnect = AtomicBoolean(true)
    /** Report Failed once for the next failure after a user tap on HOST. */
    private val userConnectAttempt = AtomicBoolean(false)
    /** Ignore onFailure/onClosed from cancel() while swapping sockets after HOST. */
    private val suppressCloseEvents = AtomicBoolean(false)

    @Volatile var phase: String = "lobby"
        private set
    @Volatile var kick: Int = 0
        private set

    fun connect() {
        allowReconnect.set(true)
        ws?.cancel()
        // Connecting is emitted from reconnect() (user HOST tap) so auto-reconnect
        // does not overwrite a Failed hint.
        val req = try {
            Request.Builder().url(this.url).build()
        } catch (e: IllegalArgumentException) {
            listener.onConnected(false)
            listener.onConnectStatus(
                ConnectStatus.Failed(url, "bad host URL — enter laptop IP:8080")
            )
            return
        }
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                open.set(true)
                userConnectAttempt.set(false)
                listener.onConnected(true)
                listener.onConnectStatus(ConnectStatus.Connected(url))
                webSocket.send(JSONObject().put("type", "hello").put("client", "phone").toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val o = JSONObject(text)
                    if (o.optString("type") == "edge_pose") {
                        parseEdgePose(o)?.let(listener::onEdgePose)
                        return
                    }
                    if (o.optString("type") != "state") return
                    phase = o.optString("phase", "lobby")
                    kick = o.optInt("kick", 0)
                    val last = o.optJSONObject("last")
                    listener.onState(
                        MatchState(
                            phase = phase,
                            kick = kick,
                            kicksTotal = o.optInt("kicksTotal", 5),
                            score = o.optInt("score", 0),
                            line = o.optString("line", ""),
                            timerMs = o.optInt("timerMs", 0),
                            lastForce = last?.optInt("force"),
                            lastResult = last?.optString("result"),
                            raw = o,
                        )
                    )
                } catch (_: Exception) {
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (suppressCloseEvents.get()) {
                    open.set(false)
                    return
                }
                open.set(false)
                listener.onConnected(false)
                if (userConnectAttempt.getAndSet(false)) {
                    listener.onConnectStatus(ConnectStatus.Disconnected(url))
                }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (suppressCloseEvents.get()) {
                    open.set(false)
                    return
                }
                open.set(false)
                listener.onConnected(false)
                if (userConnectAttempt.getAndSet(false)) {
                    listener.onConnectStatus(
                        ConnectStatus.Failed(url, humanizeFailure(t, response))
                    )
                }
                scheduleReconnect()
            }
        })
    }

    /**
     * User tapped HOST — reconnect and surface the next success/failure clearly.
     * @return false if URL invalid (Failed already emitted)
     */
    fun reconnect(newUrl: String): Boolean {
        val trimmed = newUrl.trim()
        if (trimmed.isEmpty() || trimmed == "ws://" || trimmed == "wss://") {
            listener.onConnectStatus(
                ConnectStatus.Failed(
                    DEFAULT_URL,
                    "Enter laptop IP, e.g. 172.20.10.2:8080",
                )
            )
            return false
        }
        // Reject host-less garbage like "ws:///ws"
        val hostPart = trimmed.removePrefix("ws://").removePrefix("wss://")
            .substringBefore('/').substringBefore(':')
        if (hostPart.isBlank()) {
            listener.onConnectStatus(
                ConnectStatus.Failed(
                    trimmed,
                    "Enter laptop IP, e.g. 172.20.10.2:8080",
                )
            )
            return false
        }
        url = trimmed
        userConnectAttempt.set(true)
        allowReconnect.set(false)
        suppressCloseEvents.set(true)
        ws?.cancel()
        ws = null
        open.set(false)
        listener.onConnectStatus(ConnectStatus.Connecting(url))
        Thread {
            try { Thread.sleep(200) } catch (_: InterruptedException) {}
            suppressCloseEvents.set(false)
            connect()
        }.start()
        return true
    }

    private fun scheduleReconnect() {
        if (!allowReconnect.get()) return
        Thread {
            try { Thread.sleep(1500) } catch (_: InterruptedException) {}
            if (allowReconnect.get() && !open.get()) connect()
        }.start()
    }

    fun sendAim(zone: String) {
        ws?.send(JSONObject().put("type", "aim").put("zone", zone).toString())
    }

    /** HTTP endpoint paired with the current match-host WebSocket URL. */
    fun edgeFrameUrl(): String = url
        .replaceFirst("ws://", "http://")
        .replaceFirst("wss://", "https://")
        .removeSuffix("/ws") + "/edge/frame.jpg"

    /** Ask the server to start the match (works while it's in the lobby). */
    fun sendStart() {
        ws?.send(JSONObject().put("type", "start").toString())
    }

    fun sendKick(
        zone: String,
        power: Float,
        force: Int,
        dirDeg: Int,
        height: String = "L",
        spin: Float = 0f,
        strike: String = "drive",
        foot: String = "R",
        kinematics: KickKinematicState? = null,
        trajectory: ShotTrajectory? = null,
    ) {
        val packet = JSONObject()
            .put("type", "kick")
            .put("zone", zone)
            .put("power", power.toDouble())
            .put("force", force)
            .put("dirDeg", dirDeg)
            .put("height", height)
            .put("spin", spin.toDouble())
            .put("strike", strike)
            .put("foot", foot)
        kinematics?.let { state ->
            packet.put(
                "kickState",
                JSONObject()
                    .put("schema", "sentinel.kick.state.v1")
                    .put("source", state.source)
                    .put("peakFootSpeedMps", state.peakFootSpeedMps.toDouble())
                    .put("lateralVelocityMps", state.lateralVelocityMps.toDouble())
                    .put("upwardVelocityMps", state.upwardVelocityMps.toDouble())
                    .put("pathDisplacementM", state.pathDisplacementM.toDouble())
                    .put("liftM", state.liftM.toDouble())
                    .put("swingDurationMs", state.swingDurationMs)
                    .put("confidence", state.confidence.toDouble()),
            )
        }
        trajectory?.let { shot ->
            val points = JSONArray()
            shot.points.forEach { point ->
                points.put(
                    JSONArray()
                        .put(point.timeS.toDouble())
                        .put(point.xM.toDouble())
                        .put(point.yM.toDouble())
                        .put(point.zM.toDouble()),
                )
            }
            packet.put(
                "trajectory",
                JSONObject()
                    .put("schema", "sentinel.trajectory.v1")
                    .put("model", shot.model)
                    .put("confidence", shot.confidence.toDouble())
                    .put("launchVelocity", JSONArray()
                        .put(shot.launchVxMps.toDouble())
                        .put(shot.launchVyMps.toDouble())
                        .put(shot.launchVzMps.toDouble()))
                    .put("launchSpeedMps", shot.launchSpeedMps.toDouble())
                    .put("flightTimeS", shot.flightTimeS.toDouble())
                    .put("goalX", shot.goalXM.toDouble())
                    .put("goalZ", shot.goalZM.toDouble())
                    .put("apexM", shot.apexM.toDouble())
                    .put("points", points),
            )
        }
        ws?.send(packet.toString())
    }

    fun sendSkeleton(kickNo: Int, frames: List<Pair<Int, List<FloatArray>>>) {
        val arr = JSONArray()
        for ((t, pts) in frames) {
            val p = JSONArray()
            for (xyz in pts) {
                p.put(JSONArray().put(xyz[0].toDouble()).put(xyz[1].toDouble()).put(xyz[2].toDouble()))
            }
            arr.put(JSONObject().put("t", t).put("p", p))
        }
        ws?.send(JSONObject().put("type", "skel").put("kick", kickNo).put("frames", arr).toString())
    }

    /** Calibration dimensions used by the laptop's display-only human rig. */
    fun sendBodyProfile(profile: PlayerProfile?) {
        val value = profile ?: return
        if (!open.get()) return
        ws?.send(
            JSONObject()
                .put("type", "body_profile")
                .put("schema", "sentinel.body.profile.v1")
                .put("heightCm", value.heightCm.toDouble())
                .put("weightKg", value.weightKg.toDouble())
                .put("torsoM", value.torsoM.toDouble())
                .toString()
        )
    }

    /**
     * Source-neutral live pose. The same seam is used for phone NPU/GPU/CPU
     * landmarks and UNO Q landmarks, so retargeting never selects a device.
     */
    fun sendPoseState(timestampMs: Long, points: List<FloatArray>, source: String) {
        if (!open.get() || points.size != 33) return
        val encoded = JSONArray()
        points.forEach { point ->
            if (point.size < 3) return
            encoded.put(
                JSONArray()
                    .put(point[0].toDouble())
                    .put(point[1].toDouble())
                    .put(point[2].toDouble())
            )
        }
        if (encoded.length() != 33) return
        ws?.send(
            JSONObject()
                .put("type", "pose_state")
                .put("schema", "sentinel.pose.state.v1")
                .put("timestampMs", timestampMs)
                .put("source", source.take(48))
                .put("points", encoded)
                .toString()
        )
    }

    /**
     * Self-reported silicon duty cycle for the laptop TelemetryStore / TV HUD.
     * Same shape as tv.html: unit ∈ cpu|gpu|npu, opaque metric dict, optional temp_c.
     */
    fun sendTelem(
        unit: String,
        source: String,
        busyPct: Double,
        metric: Map<String, Any?> = emptyMap(),
        state: String = "",
        tempC: Double? = null,
    ) {
        val sock = ws ?: return
        if (!open.get()) return
        val m = JSONObject()
        for ((k, v) in metric) {
            when (v) {
                null -> { /* skip */ }
                is Number -> m.put(k, v)
                is Boolean -> m.put(k, v)
                else -> m.put(k, v.toString())
            }
        }
        val o = JSONObject()
            .put("type", "telem")
            .put("unit", unit)
            .put("source", source)
            .put("busy_pct", busyPct)
            .put("metric", m)
            .put("state", state)
        if (tempC != null) o.put("temp_c", tempC)
        sock.send(o.toString())
    }

    fun isConnected(): Boolean = open.get()

    fun close() {
        allowReconnect.set(false)
        userConnectAttempt.set(false)
        ws?.close(1000, "bye")
        ws = null
    }

    companion object {
        const val DEFAULT_URL = "ws://127.0.0.1:8080/ws"
        const val PREFS = "gf_net"
        const val PREF_URL = "host_url"

        private fun parseEdgePose(o: JSONObject): EdgePoseFrame? {
            val points = o.optJSONArray("landmarks") ?: return null
            if (points.length() != 0 && points.length() != 33) return null
            val landmarks = ArrayList<FloatArray>(points.length())
            val visibility = FloatArray(points.length())
            for (i in 0 until points.length()) {
                val p = points.optJSONArray(i) ?: return null
                if (p.length() < 4) return null
                landmarks += floatArrayOf(
                    p.optDouble(0).toFloat(),
                    p.optDouble(1).toFloat(),
                    p.optDouble(2).toFloat(),
                )
                visibility[i] = p.optDouble(3, 1.0).toFloat().coerceIn(0f, 1f)
            }
            val frame = o.optJSONObject("frame") ?: JSONObject()
            val diagnostics = o.optJSONObject("diagnostics") ?: JSONObject()
            val motion = parseEdgeFlow(o.optJSONObject("motion"))
            return EdgePoseFrame(
                seq = o.optLong("seq", 0L),
                captureNs = o.optLong("t_capture_ns", 0L),
                width = frame.optInt("width", 1).coerceAtLeast(1),
                height = frame.optInt("height", 1).coerceAtLeast(1),
                rotation = frame.optInt("rotation", 0),
                mirrored = frame.optBoolean("mirrored", true),
                landmarks = landmarks,
                visibility = visibility,
                inferenceMs = diagnostics.optDouble("inference_ms", 0.0).toLong(),
                fps = diagnostics.optDouble("fps", 0.0).toFloat(),
                flowMotion = motion,
            )
        }

        private fun parseEdgeFlow(o: JSONObject?): EdgeFlowMotion? {
            if (o == null) return null
            fun foot(name: String): EdgeFlowFoot? {
                val value = o.optJSONObject(name) ?: return null
                return EdgeFlowFoot(
                    vxNorm = value.optDouble("vx", 0.0).toFloat(),
                    vyNorm = value.optDouble("vy", 0.0).toFloat(),
                    peakVxNorm = value.optDouble("peak_vx", 0.0).toFloat(),
                    peakVyNorm = value.optDouble("peak_vy", 0.0).toFloat(),
                    dxNorm = value.optDouble("dx", 0.0).toFloat(),
                    dyNorm = value.optDouble("dy", 0.0).toFloat(),
                    confidence = value.optDouble("confidence", 0.0).toFloat().coerceIn(0f, 1f),
                    samples = value.optInt("samples", 0).coerceAtLeast(0),
                )
            }
            return EdgeFlowMotion(
                timestampNs = o.optLong("t_ns", 0L).coerceAtLeast(0L),
                fps = o.optDouble("fps", 0.0).toFloat().coerceAtLeast(0f),
                left = foot("left"),
                right = foot("right"),
            )
        }

        fun normalizeUrl(raw: String): String {
            var u = raw.trim()
            if (u.isEmpty()) return DEFAULT_URL
            if (!u.startsWith("ws://") && !u.startsWith("wss://")) {
                u = "ws://$u"
            }
            if (!u.contains("/ws")) {
                u = u.trimEnd('/') + "/ws"
            }
            return if (isValidWsUrl(u)) u else DEFAULT_URL
        }

        /** Reject mangled prefs like ws://192.168.1.65:8080172.20.10.2:8080/ws */
        fun isValidWsUrl(u: String): Boolean {
            return try {
                val http = u
                    .replaceFirst("ws://", "http://")
                    .replaceFirst("wss://", "https://")
                    .toHttpUrlOrNull()
                    ?: return false
                val host = http.host
                val port = http.port
                host.isNotBlank() && port in 1..65535 &&
                    // Single host:port before path — no glued second IP.
                    Regex("""^wss?://[^/:]+:\d+/ws/?$""").matches(u)
            } catch (_: Exception) {
                false
            }
        }

        fun humanizeFailure(t: Throwable, response: Response?): String {
            val msg = (t.message ?: "").lowercase()
            return when {
                t is UnknownHostException || "unable to resolve" in msg || "unknown host" in msg ->
                    "bad IP / DNS"
                t is SocketTimeoutException || "timeout" in msg || "timed out" in msg ->
                    "timeout — same Wi‑Fi?"
                t is ConnectException || "failed to connect" in msg || "connection refused" in msg ||
                    "econnrefused" in msg ->
                    "server not running or wrong port"
                response != null -> "HTTP ${response.code}"
                t.message.isNullOrBlank() -> "connection failed"
                else -> t.message!!.take(60)
            }
        }
    }
}
