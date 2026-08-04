package com.sentinelmesh.gesturefootball.net

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** WebSocket client speaking the same JSON protocol as phone.html. */
class GameClient(
    url: String = DEFAULT_URL,
    private val listener: Listener,
) {
    interface Listener {
        fun onConnected(connected: Boolean)
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
        .retryOnConnectionFailure(true)
        .build()

    @Volatile var url: String = url
        private set
    private var ws: WebSocket? = null
    private val open = AtomicBoolean(false)
    private val allowReconnect = AtomicBoolean(true)

    @Volatile var phase: String = "lobby"
        private set
    @Volatile var kick: Int = 0
        private set

    fun connect() {
        allowReconnect.set(true)
        ws?.cancel()
        val req = Request.Builder().url(this.url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                open.set(true)
                listener.onConnected(true)
                webSocket.send(JSONObject().put("type", "hello").put("client", "phone").toString())
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
                open.set(false)
                listener.onConnected(false)
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                open.set(false)
                listener.onConnected(false)
                scheduleReconnect()
            }
        })
    }

    /** Switch host (e.g. laptop IP) and reconnect. */
    fun reconnect(newUrl: String) {
        url = newUrl
        allowReconnect.set(false)
        ws?.cancel()
        ws = null
        open.set(false)
        Thread {
            try { Thread.sleep(200) } catch (_: InterruptedException) {}
            connect()
        }.start()
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
            return u
        }
    }
}
