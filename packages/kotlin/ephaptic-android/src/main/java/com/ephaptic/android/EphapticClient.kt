package com.ephaptic.android

import android.util.Log
import com.daveanthonythomas.moshipack.MoshiPack
import com.squareup.moshi.Moshi
import com.squareup.moshi.MsgpackWriter
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.ephaptic.android.internal.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.*
import okio.Buffer
import okio.ByteString
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random
import kotlin.reflect.javaType
import kotlin.reflect.typeOf

/** a server-sent event, delivered over [EphapticClient.events]. */
data class ServerEvent(val name: String, val args: List<Any?>, val kwargs: Map<String, Any?>)

/** connection state, per client spec 5.2. */
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING, CLOSED }

/** a frame belonging to an open stream. public, because [EphapticClient.stream] is inline. */
sealed class StreamFrame {
    object Open: StreamFrame()
    data class Chunk(val value: Any?): StreamFrame()
    data class Error(val code: String, val message: String, val data: Any?): StreamFrame()
    object Done: StreamFrame()
}

/**
 * the error every failure surfaces as. `code`, `message`, and `data` are each
 * retrievable independently, without parsing another
 */
class EphapticException(
    val code: String,
    override val message: String,
    val data: Any? = null,
): Exception("$code: $message")

class EphapticClient(
    private val url: String,
    private val auth: Any? = null,
    private val client: OkHttpClient = OkHttpClient(),
    private val timeoutMs: Long = 30_000L, // 30 seconds
) {

    init {
        require(timeoutMs in 1..600_000) {
            "timeoutMs must be between 1 and 600000; the interval cannot be disabled."
        }
    }

    private val scope = CoroutineScope(
        Dispatchers.IO + SupervisorJob() + CoroutineExceptionHandler { _, e ->
            Log.e("ephaptic", "A background coroutine failed", e)
        }
    )
    @PublishedApi
    internal val moshi: Moshi = Moshi.Builder().addLast(KotlinJsonAdapterFactory()).build()
    private val moshiPack = MoshiPack()

    @Volatile private var webSocket: WebSocket? = null
    private val connectionMutex = Mutex() // stops concurrent connects

    @Volatile private var connectionAck = CompletableDeferred<Unit>()
    @Volatile private var reconnectJob: Job? = null
    @Volatile private var reconnectPending = false

    @PublishedApi
    @Volatile internal var remainingBudgetMs: Long = timeoutMs

    private val _state = MutableStateFlow(ConnectionState.DISCONNECTED)
    
    /** observable connection state */
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val eventQueue = Channel<ServerEvent>(Channel.UNLIMITED)
    private val _events = MutableSharedFlow<ServerEvent>(
        extraBufferCapacity = 256,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    /** observe server-sent events. Filter by [ServerEvent.name] for a specific event. */
    val events: SharedFlow<ServerEvent> = _events.asSharedFlow()

    private val listeners = ConcurrentHashMap<String, MutableSet<EventListener>>()

    private val pendingCalls = ConcurrentHashMap<Int, CompletableDeferred<Any?>>()
    @PublishedApi
    internal val pendingStreams = ConcurrentHashMap<Int, Channel<StreamFrame>>()
    private val callIdCounter = AtomicInteger(0)

    @Volatile private var retryCount = 0
    @Volatile private var isManuallyClosed = false

    private val generation = AtomicInteger(0)

    init {
        scope.launch {
            for (event in eventQueue) {
                deliver(event)
                _events.tryEmit(event)
            }
        }
    }

    // ## Calls

    @OptIn(ExperimentalStdlibApi::class)
    suspend inline fun <reified T> request(method: String, vararg args: Any?): T {
        val rawResult = sendRawRpc(method, args.toList())
        val adapter = moshi.adapter<T>(typeOf<T>().javaType)
        @Suppress("UNCHECKED_CAST")
        if (rawResult == null) return null as T
        return try {
            adapter.fromJsonValue(rawResult) as T
        } catch (e: EphapticException) {
            throw e
        } catch (e: Exception) {
            throw EphapticException("PROTOCOL_ERROR", "Could not decode the result of '$method': ${e.message}")
        }
    }

    @PublishedApi
    internal suspend fun sendRawRpc(name: String, args: List<Any?>): Any? {
        var id: Int? = null
        return try {
            withTimeout(timeoutMs) {
                ensureConnected()
                val callId = callIdCounter.incrementAndGet()
                id = callId
                val deferred = CompletableDeferred<Any?>()
                pendingCalls[callId] = deferred
                sendFrame(RpcRequestFrame(id = callId, name = name, args = args), name)
                deferred.await()
            }
        } catch (e: TimeoutCancellationException) {
            id?.let { pendingCalls.remove(it) }
            throw EphapticException("TIMEOUT", "$name timed out; exceeded ${timeoutMs}ms.")
        } catch (e: Exception) {
            id?.let { pendingCalls.remove(it) }
            throw e
        }
    }

    /**
     * call a streaming (generator) RPC. each yielded item is emitted on the
     * returned [Flow]; a mid-stream server error is thrown into the collector.
     */
    @OptIn(ExperimentalStdlibApi::class)
    inline fun <reified T> stream(method: String, vararg args: Any?): Flow<T> = flow {
        val adapter = moshi.adapter<T>(typeOf<T>().javaType)
        val id = openStream(method, args.toList())
        val channel = pendingStreams[id]
            ?: throw EphapticException("DISCONNECTED", "The connection closed before '$method' could start.")

        try {
            awaitStreamOpen(id, channel, method)
            for (item in channel) {
                when (item) {
                    is StreamFrame.Open -> {}
                    is StreamFrame.Chunk -> {
                        @Suppress("UNCHECKED_CAST")
                        val value: T = if (item.value == null) null as T else try {
                            adapter.fromJsonValue(item.value) as T
                        } catch (e: Exception) {
                            throw EphapticException("PROTOCOL_ERROR", "Could not decode a chunk of '$method': ${e.message}")
                        }
                        emit(value)
                    }
                    is StreamFrame.Error -> throw EphapticException(item.code, item.message, item.data)
                    is StreamFrame.Done -> return@flow
                }
            }
        } finally {
            closeStream(id)
        }
    }

    /** @suppress Internal; public only so [stream] may be inline. */
    @PublishedApi
    internal suspend fun openStream(method: String, args: List<Any?>): Int {
        val startedAt = System.nanoTime()
        try {
            withTimeout(timeoutMs) { ensureConnected() }
        } catch (e: TimeoutCancellationException) {
            throw EphapticException("TIMEOUT", "$method timed out; exceeded ${timeoutMs}ms.")
        }
        remainingBudgetMs = (timeoutMs - (System.nanoTime() - startedAt) / 1_000_000).coerceAtLeast(1)
        val id = callIdCounter.incrementAndGet()

        pendingStreams[id] = Channel(Channel.UNLIMITED)
        try {
            sendFrame(RpcRequestFrame(id = id, name = method, args = args), method)
        } catch (e: Exception) {
            closeStream(id)
            throw e
        }
        return id
    }

    /** @suppress Internal; public only so [stream] may be inline. */
    @PublishedApi
    internal suspend fun awaitStreamOpen(id: Int, channel: Channel<StreamFrame>, method: String) {
        val first = try {
            withTimeout(remainingBudgetMs) { channel.receive() }
        } catch (e: TimeoutCancellationException) {
            closeStream(id)
            throw EphapticException("TIMEOUT", "$method timed out; exceeded ${timeoutMs}ms.")
        }
        when (first) {
            is StreamFrame.Open -> {}
            is StreamFrame.Error -> throw EphapticException(first.code, first.message, first.data)
            is StreamFrame.Done -> throw EphapticException("PROTOCOL_ERROR", "Stream '$method' completed before it opened.")
            is StreamFrame.Chunk -> throw EphapticException("PROTOCOL_ERROR", "Stream '$method' produced a chunk before it opened.")
        }
    }

    /** @suppress Internal; public only so [stream] may be inline. */
    @PublishedApi
    internal fun closeStream(id: Int) {
        pendingStreams.remove(id)?.close()
    }

    private fun <T> pack(value: T, type: java.lang.reflect.Type): ByteString {
        val buffer = Buffer()
        moshi.adapter<T>(type).serializeNulls().toJson(MsgpackWriter(buffer), value)
        return buffer.readByteString()
    }

    private fun sendFrame(frame: RpcRequestFrame, method: String) {
        val bytes = try {
            pack(frame, RpcRequestFrame::class.java)
        } catch (e: Exception) {
            throw EphapticException("ENCODE_ERROR", "Could not encode the arguments of '$method': ${e.message}")
        }
        val socket = webSocket ?: throw EphapticException("DISCONNECTED", "Not connected.")
        if (!socket.send(bytes)) {
            throw EphapticException("DISCONNECTED", "The connection closed before '$method' could be sent.")
        }
    }

    // ## Events

    /** A registration returned by [on] or [once]. */
    inner class EventListener internal constructor(
        internal val name: String,
        internal val handler: suspend (ServerEvent) -> Unit,
        internal val single: Boolean,
    ) {
        private val mailbox = Channel<ServerEvent>(Channel.UNLIMITED)

        private val pump = scope.launch {
            for (event in mailbox) {
                runCatching { handler(event) }
                if (single) break
            }
            mailbox.close()
        }

        internal fun post(event: ServerEvent) {
            mailbox.trySend(event)
        }

        /** Deregister. Takes effect immediately. */
        fun cancel() {
            listeners[name]?.remove(this)
            mailbox.close()
            pump.cancel()
        }
    }

    fun on(name: String, handler: suspend (ServerEvent) -> Unit): EventListener =
        register(EventListener(name, handler, single = false))

    fun once(name: String, handler: suspend (ServerEvent) -> Unit): EventListener =
        register(EventListener(name, handler, single = true))

    private fun register(listener: EventListener): EventListener {
        listeners.computeIfAbsent(listener.name) { ConcurrentHashMap.newKeySet() }.add(listener)
        return listener
    }

    private fun deliver(event: ServerEvent) {
        for (listener in listeners[event.name]?.toList() ?: emptyList()) {
            if (listener.single) {
                listeners[listener.name]?.remove(listener)
            }
            listener.post(event)
        }
    }

    // ## Connection

    @PublishedApi
    internal suspend fun ensureConnected() {
        if (isManuallyClosed) throw EphapticException("DISCONNECTED", "The client has been closed.")
        if (connectionAck.isCompleted && webSocket != null) return
        if (webSocket == null && !reconnectPending && reconnectJob?.isActive != true) connectInternal()
        try {
            connectionAck.await()
        } catch (e: EphapticException) {
            throw e
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            throw EphapticException("CONNECT_FAILED", "Could not connect to $url: ${e.message}")
        }
    }

    fun connect() {
        isManuallyClosed = false
        scope.launch { connectInternal() }
    }

    private suspend fun connectInternal() {
        connectionMutex.withLock {
            if (webSocket != null || isManuallyClosed) return

            Log.d("ephaptic", "connecting to $url...")
            _state.value = if (retryCount > 0) ConnectionState.RECONNECTING else ConnectionState.CONNECTING

            if (connectionAck.isCompleted) connectionAck = CompletableDeferred()

            val request = try {
                Request.Builder().url(url).build()
            } catch (e: IllegalArgumentException) {
                val error = EphapticException("CONNECT_FAILED", "'$url' is not a usable address: ${e.message}")
                isManuallyClosed = true // an unusable address will not become usable
                _state.value = ConnectionState.CLOSED
                if (!connectionAck.isCompleted) connectionAck.completeExceptionally(error)
                failPending(error)
                return
            }
            webSocket = client.newWebSocket(request, Connection(generation.incrementAndGet()))
        }
    }

    private inner class Connection(private val gen: Int): WebSocketListener() {

        private val settled = java.util.concurrent.atomic.AtomicBoolean(false)

        private fun isCurrent() = generation.get() == gen

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrent() || isManuallyClosed) {
                webSocket.close(1000, "Superseded")
                return
            }

            Log.d("ephaptic", "connected to $url")
            this@EphapticClient.webSocket = webSocket
            retryCount = 0
            callIdCounter.set(0)

            try {
                webSocket.send(pack(InitFrame(auth = auth), InitFrame::class.java))
            } catch (e: Exception) {
                Log.e("ephaptic", "Could not send the init frame", e)
                settled.set(true)
                handleDisconnect(
                    EphapticException("CONNECT_FAILED", "Could not send the init frame: ${e.message}"),
                    failedToConnect = true,
                )
                return
            }

            _state.value = ConnectionState.CONNECTED
            connectionAck.complete(Unit)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (isCurrent()) handleMessage(bytes)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrent() || !settled.compareAndSet(false, true)) return
            Log.e("ephaptic", "Connection failed (${t.message})")
            handleDisconnect(
                EphapticException("CONNECT_FAILED", "The connection failed: ${t.message}"),
                failedToConnect = true,
            )
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrent() || !settled.compareAndSet(false, true)) return
            Log.w("ephaptic", "websocket closing ($code: $reason)")
            runCatching { webSocket.close(1000, null) }
            handleDisconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrent() || !settled.compareAndSet(false, true)) return
            Log.w("ephaptic", "websocket closed ($code: $reason)")
            handleDisconnect()
        }
    }

    private fun handleMessage(bytes: ByteString) {
        val frame: Map<String, Any?> = try {
            moshiPack.unpack(bytes.toByteArray())
        } catch (e: Exception) {
            Log.e("ephaptic", "Discarding an undecodable frame", e)
            return
        }

        try {
            if (frame["type"] == "event") {
                @Suppress("UNCHECKED_CAST")
                val payload = frame["payload"] as? Map<String, Any?>
                @Suppress("UNCHECKED_CAST")
                eventQueue.trySend(ServerEvent(
                    name = frame["name"] as? String ?: "",
                    args = payload?.get("args") as? List<Any?> ?: emptyList(),
                    kwargs = payload?.get("kwargs") as? Map<String, Any?> ?: emptyMap(),
                ))
                return
            }

            val id = (frame["id"] as? Number)?.toInt() ?: return

            val streamChannel = pendingStreams[id]
            if (streamChannel != null) {
                when {
                    frame.containsKey("stream") -> streamChannel.trySend(StreamFrame.Open)
                    frame.containsKey("error") -> streamChannel.trySend(parseError(frame["error"]).toStreamFrame())
                    frame.containsKey("done") -> streamChannel.trySend(StreamFrame.Done)
                    frame.containsKey("chunk") -> streamChannel.trySend(StreamFrame.Chunk(frame["chunk"]))
                }
                return
            }

            val deferred = pendingCalls.remove(id)
            if (deferred == null) {
                return
            }
            when {
                frame.containsKey("error") -> deferred.completeExceptionally(parseError(frame["error"]))
                frame.containsKey("stream") -> deferred.completeExceptionally(EphapticException(
                    "PROTOCOL_ERROR", "Call $id returned a stream; use stream() rather than request()."))
                frame.containsKey("result") -> deferred.complete(frame["result"])
                else -> deferred.completeExceptionally(EphapticException(
                    "PROTOCOL_ERROR", "The server sent a frame carrying no result, error, or stream."))
            }
        } catch (e: Exception) {
            Log.e("ephaptic", "Discarding a structurally invalid frame", e)
            val id = (frame["id"] as? Number)?.toInt()
            if (id != null) {
                val protocolError = EphapticException(
                    "PROTOCOL_ERROR", "The server sent a frame this client could not interpret.")
                pendingCalls.remove(id)?.completeExceptionally(protocolError)
                pendingStreams.remove(id)?.let {
                    it.trySend(StreamFrame.Error(protocolError.code, protocolError.message, null))
                    it.close()
                }
            }
        }
    }

    private fun parseError(raw: Any?): EphapticException = when (raw) {
        is String -> EphapticException("ERROR", raw)
        is Map<*, *> -> EphapticException(
            raw["code"] as? String ?: "ERROR",
            raw["message"] as? String ?: "",
            raw["data"],
        )
        else -> EphapticException("ERROR", "The server reported an error.")
    }

    private fun EphapticException.toStreamFrame() = StreamFrame.Error(code, message, data)

    private fun failPending(error: EphapticException) {
        for (id in pendingCalls.keys.toList()) {
            pendingCalls.remove(id)?.completeExceptionally(error)
        }
        for (id in pendingStreams.keys.toList()) {
            pendingStreams.remove(id)?.let {
                it.trySend(StreamFrame.Error(error.code, error.message, error.data))
                it.close()
            }
        }
    }

    private fun handleDisconnect(cause: EphapticException? = null, failedToConnect: Boolean = false) {
        webSocket = null

        if (!isManuallyClosed) reconnectPending = true

        failPending(EphapticException("DISCONNECTED", "Connection closed before a response was received."))

        if (!connectionAck.isCompleted) {
            if (failedToConnect) {
                connectionAck.completeExceptionally(
                    cause ?: EphapticException("CONNECT_FAILED", "Could not connect to $url.")
                )
            } else {
                connectionAck.complete(Unit)
            }
        }

        if (isManuallyClosed) {
            reconnectPending = false
            _state.value = ConnectionState.CLOSED
            return
        }

        _state.value = ConnectionState.RECONNECTING
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            try {
            connectionMutex.withLock {
                if (connectionAck.isCompleted) connectionAck = CompletableDeferred()
            }
            val delayMs = calculateBackoff()
            Log.d("ephaptic", "reconnecting in ${delayMs}ms...")
            delay(delayMs)
            if (isManuallyClosed) {
                _state.value = ConnectionState.CLOSED
                return@launch
            }
            retryCount++
            connectInternal()
            } finally {
                reconnectPending = false
            }
        }
    }

    private fun calculateBackoff(): Long {
        val baseDelay = 1000.0
        val maxDelay = 30000.0
        val exponential = min(maxDelay, baseDelay * 2.0.pow(retryCount))
        return (exponential + Random.nextDouble(0.0, 1000.0)).toLong()
    }

    fun disconnect() {
        isManuallyClosed = true
        reconnectPending = false
        generation.incrementAndGet()
        retryCount = 0
        reconnectJob?.cancel()
        reconnectJob = null
        webSocket?.close(1000, "Client disconnected")
        webSocket = null
        failPending(EphapticException("DISCONNECTED", "The client disconnected."))
        if (!connectionAck.isCompleted) connectionAck.complete(Unit)
        _state.value = ConnectionState.CLOSED
    }

    fun close() {
        disconnect()
        eventQueue.close()
        scope.cancel()
    }
}