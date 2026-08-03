package com.ephaptic.android.internal

internal data class InitFrame(
    val type: String = "init",
    val auth: Any? = null,
)

@PublishedApi
internal data class RpcRequestFrame(
    val type: String = "rpc",
    val id: Int,
    val name: String,
    val args: List<Any?>
)

/* [NOTE] Response frames are decoded as `Map<String, Any?>` rather than into a
 * data class. A data class cannot distinguish an absent field from one present
 * and null, which is required, and it also can't accept a error as a plain string.
 */