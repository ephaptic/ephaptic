package com.ephaptic.android

import android.util.Log
import com.daveanthonythomas.moshipack.MoshiPack
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.ephaptic.android.internal.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.*
import okio.ByteString
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

class EphapticClient(
    private val url: String,
    private val auth: Any? = null,
    private val client: OkHttpClient = OkHttpClient(),
    private val timeoutMs: Long = 30_000L, // 30 seconds
): WebSocketListener() {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    @PublishedApi
    internal val moshi = Moshi.Builder().addLast(KotlinJsonAdapterFactory()).build()
    private val moshiPack = MoshiPack()


    private var webSocket: WebSocket? = null
    private val connectionMutex = Mutex() // to stop it from connecting multiple times

    private var connectionAck = CompletableDeferred<Unit>()

    private val _events = MutableSharedFlow<RpcResponseFrame>()
    private val pendingCalls = mutableMapOf<Int, CompletableDeferred<Any>>()
    private var callIdCounter = 0
    private var retryCount = 0
    private var isManuallyClosed = false

    suspend inline fun <reified T> request(method: String, vararg args: Any?): T {
        val rawResult = sendRawRpc(method, args.toList())
        val adapter = moshi.adapter(T::class.java)
        return adapter.fromJsonValue(rawResult) ?: throw EphapticException("PARSE_ERROR", "Received null for non-nullable type ${T::class.simpleName}")
    }

    @PublishedApi
    internal suspend fun sendRawRpc(name: String, args: List<Any?>): Any? {
        ensureConnected()

        return withTimeout(timeoutMs) {
            val id = ++callIdCounter
            val deferred = CompletableDeferred<Any>()
            pendingCalls[id] = deferred

            try {
                val frame = RpcRequestFrame(id = id, name = name, args = args)
                val bytes = moshiPack.pack(frame).readByteString()
                webSocket?.send(bytes) ?: throw EphapticException("NETWORK_ERROR", "Socket is null")

                deferred.await()
            } catch (e: Exception) {
                pendingCalls.remove(id)
                throw e
            }
        }
    }

    fun connect() {
        isManuallyClosed = false
        scope.launch { connectInternal() }
    }

    private suspend fun connectInternal() {
        connectionMutex.withLock {
            if (webSocket != null) return

            Log.d("ephaptic", "connecting to ${url}...")

            if (connectionAck.isCompleted) connectionAck = CompletableDeferred()

            val request = Request.Builder().url(url).build()
            webSocket = client.newWebSocket(request, this@EphapticClient)
        }
    }

    private suspend fun ensureConnected() {
        if (!connectionAck.isCompleted) {
            if (webSocket == null) connect()
            Log.d("ephaptic", "waiting for connection...")
            connectionAck.await()
        }
    }

    override fun onOpen(webSocket: WebSocket, response: Response) {
        Log.d("ephaptic", "connected to ${url}")
        retryCount = 0

        val init = InitFrame(auth = auth)
        val bytes = moshiPack.pack(init).readByteString()
        webSocket.send(bytes)

        connectionAck.complete(Unit)
        Log.d("ephaptic", "ready !!")
    }

    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
        try {
            val frame = moshiPack.unpack<RpcResponseFrame>(bytes.toByteArray())

            if (frame.type == "event") {
                scope.launch { _events.emit(frame) }
            } else if (frame.id != null) {
                val deferred = pendingCalls.remove(frame.id)
                if (frame.error != null) deferred?.completeExceptionally(EphapticException(frame.error.code, frame.error.message))
                else deferred?.complete(frame.result ?: Unit)
            }
        } catch (e: Exception) {
            Log.e("ephaptic", "Failed to deserialize msgpack message", e)
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        Log.e("ephaptic", "Connection Failed (${t.message})")
        handleDisconnect()
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        Log.w("ephaptic", "websocket closed (${code}: ${reason})")
        handleDisconnect()
    }

    private fun handleDisconnect() {
        scope.launch {
            connectionMutex.withLock {
                webSocket = null
                if (connectionAck.isCompleted) connectionAck = CompletableDeferred()
            }

            if (!isManuallyClosed) {
                val delayMs = calculateBackoff()
                Log.d("ephaptic", "reconnecting in ${delayMs}ms...")
                delay(delayMs)
                retryCount++
                connectInternal()
            }
        }
    }

    private fun calculateBackoff(): Long {
        val baseDelay = 1000.0 // 1s
        val maxDelay = 30000.0 // 30s
        val exponential = baseDelay * 2.0.pow(retryCount)
        val jitter = Random.nextDouble(0.0, 1000.0)
        return min(maxDelay, exponential + jitter).toLong()
    }

    fun disconnect() {
        isManuallyClosed = true
        webSocket?.close(1000, "Logout")
        webSocket = null
    }
}

class EphapticException(val code: String, message: String): Exception("${code}: ${message}")