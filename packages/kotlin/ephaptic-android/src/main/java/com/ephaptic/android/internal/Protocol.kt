package com.ephaptic.android.internal

internal data class InitFrame(
    val type: String = "init",
    val auth: Any? = null,
)

internal data class RpcRequestFrame(
    val type: String = "rpc",
    val id: Int,
    val name: String,
    val args: List<Any>
)

internal data class RpcResponseFrame(
    val id: Int? = null,
    val result: Any? = null,
    val error: RpcError? = null,

    val type: String? = null,
    val name: String? = null,
    val payload: EventPayload? = null
)

internal data class RpcError(
    val code: String,
    val message: String,
    val data: Any? = null,
)

internal data class EventPayload(
    val args: List<Any> = emptyList(),
    val kwargs: Map<String, Any> = emptyMap()
)