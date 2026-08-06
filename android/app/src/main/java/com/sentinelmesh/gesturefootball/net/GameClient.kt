package com.sentinelmesh.gesturefootball.net

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

/** WebSocket client speaking the same JSON protocol as phone.html.
 *
 * Sends an extended hello with a persisted device_id + capability descriptor
 * (docs/device-protocol.md). Older hosts ignore the extra fields; hosts with a
 * registry return a WELCOME and resume the same session across reconnects.
 * Non-"state" messages (welcome/ack) are already filtered in onMessage.
 */
class GameClient(
    url: String = DEFAULT_URL,
    private val listener: Listener,
    /** Stable device identity; persisted by the caller (MainActivity prefs). */
    private val deviceId: String? = null,
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
        fun onState(state: MatchState)
    }

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
                webSocket.send(hello().toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val o = JSONObject(text)
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

    /** Extended hello: legacy fields + device_id + capability descriptor. */
    private fun hello(): JSONObject {
        val o = JSONObject().put("type", "hello").put("client", "phone")
        if (deviceId != null) {
            o.put("device_id", deviceId)
                .put("device", "phone")
                .put("roles", JSONArray().put("phone"))
                .put("streams", JSONArray()
                    .put(JSONObject().put("name", "aim").put("schema", "zone").put("rate_hz", 5))
                    .put(JSONObject().put("name", "kick").put("schema", "event").put("rate_hz", 0)))
                .put("compute", JSONObject().put("has_npu", true)
                    .put("units", JSONArray().put("cpu").put("gpu").put("npu")))
                .put("proto", 1)
        }
        return o
    }

    fun sendAim(zone: String) {
        ws?.send(JSONObject().put("type", "aim").put("zone", zone).toString())
    }

    /** Per-unit telemetry, 1 Hz (docs/device-protocol.md §4). `metric` is
     * opaque to the host; the phone NPU cell shows the fallback rung. */
    fun sendTelem(unit: String, busyPct: Double, metric: JSONObject, state: String) {
        ws?.send(
            JSONObject()
                .put("type", "telem")
                .put("unit", unit)
                .put("busy_pct", busyPct)
                .put("metric", metric)
                .put("state", state)
                .toString()
        )
    }

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
    ) {
        ws?.send(
            JSONObject()
                .put("type", "kick")
                .put("zone", zone)
                .put("power", power.toDouble())
                .put("force", force)
                .put("dirDeg", dirDeg)
                .put("height", height)
                .put("spin", spin.toDouble())
                .put("strike", strike)
                .put("foot", foot)
                .toString()
        )
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
