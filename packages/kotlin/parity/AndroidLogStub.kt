package android.util
object Log {
    fun e(tag: String, msg: String): Int = 0
    fun e(tag: String, msg: String, tr: Throwable?): Int = 0
    fun w(tag: String, msg: String): Int = 0
    fun w(tag: String, msg: String, tr: Throwable?): Int = 0
    fun i(tag: String, msg: String): Int = 0
    fun d(tag: String, msg: String): Int = 0
}
// why is ephaptic kotlin client even called ephaptic-android; like what if a java user wants to use it for
// idk a minecraft mod