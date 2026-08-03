package harness

import com.ephaptic.android.EphapticClient
import com.ephaptic.android.EphapticException
import com.ephaptic.android.ConnectionState
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.toList

var passed = 0
var failed = 0

fun check(label: String, ok: Boolean, detail: String = "") {
    if (ok) {
        passed++
        println("  ok   $label")
    } else {
        failed++
        println("  FAIL $label${if (detail.isNotEmpty()) "  <- $detail" else ""}")
    }
}

suspend fun <T> expectError(label: String, expectedCode: String, block: suspend () -> T): EphapticException? {
    return try {
        block()
        check(label, false, "no error raised")
        null
    } catch (e: EphapticException) {
        check(label, e.code == expectedCode, "code=${e.code} message=${e.message}")
        e
    }
}

fun main(args: Array<String>) = runBlocking {
    val port = args.getOrNull(0) ?: "7900"
    val url = "ws://127.0.0.1:$port/_ephaptic"

    val client = EphapticClient(url = url, auth = mapOf("token" to "user-1"))

    // ## Calls
    check("add(2,3) == 5", client.request<Int>("add", 2, 3) == 5)
    check("echo round-trips a string", client.request<String>("echo", "hi") == "hi")
    check("identity crosses the init frame", client.request<String>("whoami") == "user-1")
    check("a null result is a value, not an error", client.request<String?>("returns_null") == null)

    val list = client.request<List<Int>>("echo", listOf(1, 2, 3))
    check("a generic result decodes to its element type", list == listOf(1, 2, 3), list.toString())
    check("a generic result's elements are the right type",
        list.isNotEmpty() && list[0]::class == Int::class,
        if (list.isEmpty()) "empty" else list[0]::class.toString())

    val map = client.request<Map<String, Any?>>("echo", mapOf("a" to 1, "b" to null))
    check("a null member survives the round trip",
        map.containsKey("b") && map["b"] == null, map.toString())
    check("a null member is not confused with an absent one",
        map.keys == setOf("a", "b"), map.keys.toString())

    val nested = client.request<Map<String, Any?>>("echo", mapOf("outer" to mapOf("inner" to null)))
    @Suppress("UNCHECKED_CAST")
    val inner = nested["outer"] as? Map<String, Any?>
    check("a nested null member survives the round trip",
        inner != null && inner.containsKey("inner") && inner["inner"] == null, nested.toString())

    check("an explicit null argument is delivered as null",
        client.request<String?>("echo", null) == null)

    // ## Errors
    val typed = expectError("a typed error crosses with its code", "INSUFFICIENT_FUNDS") {
        client.request<Any?>("boom")
    }
    @Suppress("UNCHECKED_CAST")
    val data = typed?.data as? Map<String, Any?>
    check("a typed error carries its data", data?.get("available") == 3.0, data.toString())

    val masked = expectError("an untyped error becomes INTERNAL", "INTERNAL") {
        client.request<Any?>("kaboom")
    }
    check("an untyped error discloses no detail",
        masked != null && !masked.message.contains("sk-proj"), masked?.message ?: "")

    check("requiresLogin admits an authenticated caller",
        client.request<String>("secret") == "sk-proj-1234")

    val anon = EphapticClient(url = url)
    expectError("requiresLogin refuses an anonymous caller", "UNAUTHORIZED") {
        anon.request<Any?>("secret")
    }
    anon.close()

    // ## Streaming
    val countdown = client.stream<Int>("countdown", 4).toList()
    check("a stream yields every chunk in order", countdown == listOf(4, 3, 2, 1), countdown.toString())

    val falsy = client.stream<Int?>("falsy_stream").toList()
    check("a falsy chunk is not treated as completion", falsy == listOf(0, null, 1), falsy.toString())

    val received = mutableListOf<Int>()
    var streamError: EphapticException? = null
    try {
        client.stream<Int>("badstream").collect { received.add(it) }
    } catch (e: EphapticException) {
        streamError = e
    }
    check("a mid-stream error reaches the collector",
        streamError?.code == "INSUFFICIENT_FUNDS", streamError?.code ?: "none")
    check("chunks before a mid-stream error are kept", received == listOf(1), received.toString())

    // ## Events
    val seen = CompletableDeferred<Map<String, Any?>>()
    val listener = client.on("pong") { event -> seen.complete(event.kwargs) }
    check("emit returns", client.request<String>("ping") == "sent")
    val payload = withTimeoutOrNull(10_000) { seen.await() }
    check("an event crosses with its payload", payload?.get("ok") == true, payload.toString())
    listener.cancel()

    var afterCancel = 0
    val temp = client.on("pong") { afterCancel++ }
    temp.cancel()
    client.request<String>("ping")
    delay(1000)
    check("a cancelled registration receives nothing", afterCancel == 0, afterCancel.toString())

    var onceCount = 0
    client.once("pong") { onceCount++ }
    client.request<String>("ping")
    delay(500)
    client.request<String>("ping")
    delay(1000)
    check("once fires exactly once", onceCount == 1, onceCount.toString())

    // ## Concurrency
    val concurrent = (1..200).map { n -> async { client.request<Int>("add", n, n) } }.awaitAll()
    check("200 concurrent calls each receive their own result",
        concurrent == (1..200).map { it * 2 }, "mismatch")

    val racy = EphapticClient(url = url, auth = mapOf("token" to "user-2"), timeoutMs = 20_000)
    val inFlight = (1..50).map { n ->
        async {
            try { racy.request<Int>("add", n, n); "ok" }
            catch (e: EphapticException) { e.code }
        }
    }
    delay(150)
    racy.disconnect()
    val outcomes = inFlight.awaitAll().toSet()
    check("a disconnect settles every call in flight",
        outcomes.all { it == "ok" || it == "DISCONNECTED" }, outcomes.toString())

    racy.connect()
    check("a disconnected client reconnects and serves calls",
        racy.request<Int>("add", 7, 7) == 14)
    racy.close()

    val churn = EphapticClient(url = url, auth = mapOf("token" to "user-3"), timeoutMs = 10_000)
    var lateConnect = 0
    repeat(40) {
        churn.connect()
        churn.disconnect()
        delay(15)
        if (churn.state.value != ConnectionState.CLOSED) lateConnect++
    }
    delay(1000)
    check("a socket opening after disconnect does not report itself connected",
        lateConnect == 0 && churn.state.value == ConnectionState.CLOSED,
        "late=$lateConnect final=${churn.state.value}")
    churn.close()

    val closed = EphapticClient(url = "ws://127.0.0.1:$port/wrong-path", timeoutMs = 6000)
    val closeBegan = System.currentTimeMillis()
    expectError("a server-initiated close is observed, not hung on", "DISCONNECTED") {
        closed.request<Any?>("add", 1, 1)
    }
    check("a server-initiated close is observed promptly, not on the interval",
        System.currentTimeMillis() - closeBegan < 5000,
        "${System.currentTimeMillis() - closeBegan}ms of a 6000ms interval")
    check("a server-initiated close does not leave the state CONNECTED",
        closed.state.value != ConnectionState.CONNECTED, closed.state.value.toString())
    closed.close()

    val order = mutableListOf<Int>()
    val orderListener = client.on("pong") { order.add(order.size) }
    repeat(60) { client.request<String>("ping") }
    delay(2500)
    orderListener.cancel()
    check("a burst of events is delivered in order",
        order.size >= 55 && order == order.sorted(), "n=${order.size}")

    val slow = EphapticClient(url = "ws://10.255.255.1:9/_ephaptic", timeoutMs = 2000) // <-- who's IP is this? which port is that? WHO THE FUCK KNOWS
    val began = System.currentTimeMillis()
    try { slow.request<Any?>("add", 1, 1) } catch (e: EphapticException) { }
    val took = System.currentTimeMillis() - began
    check("the interval is not applied twice", took < 3500, "took ${took}ms for a 2000ms interval")
    slow.close()

    val malformed = EphapticClient(url = "127.0.0.1:8000/_ephaptic", timeoutMs = 3000)
    expectError("a schemeless address reports CONNECT_FAILED", "CONNECT_FAILED") {
        malformed.request<Any?>("add", 1, 1)
    }
    malformed.close()

    // ## State & Lifecycle
    check("state is CONNECTED while in use", client.state.value == ConnectionState.CONNECTED,
        client.state.value.toString())

    client.disconnect()
    check("state is CLOSED after disconnect", client.state.value == ConnectionState.CLOSED,
        client.state.value.toString())

    expectError("a call after disconnect fails DISCONNECTED", "DISCONNECTED") {
        client.request<Any?>("add", 1, 1)
    }

    val bad = EphapticClient(url = "ws://127.0.0.1:1/_ephaptic", timeoutMs = 3000)
    expectError("a refused connection reports CONNECT_FAILED", "CONNECT_FAILED") {
        bad.request<Any?>("add", 1, 1)
    }
    bad.close()

    client.close()

    println()
    println("$passed/${passed + failed} kotlin parity checks passed")
    if (failed > 0) throw RuntimeException("$failed check(s) failed")
}