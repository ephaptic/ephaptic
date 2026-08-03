import { AsyncLocalStorage } from "node:async_hooks";

/**
 * context available to a handler while it runs. propagated with
 * {@link AsyncLocalStorage} so handlers can reach the current user / caller
 * without threading an explicit argument through every call.
 */
export interface HandlerContext {
    /** identity returned by the identity loader (or `null` if anonymous). */
    user: unknown;
    /** emit a server event back to the socket that made the current call. */
    emit: (name: string, data?: unknown) => void;
    /** protocol the current handler is serving */
    scope: "rpc" | "http";
}

export const contextStorage = new AsyncLocalStorage<HandlerContext>();

/** `true` when the current handler is serving an HTTP (Router) request. */
export function isHttp(): boolean {
    return contextStorage.getStore()?.scope === "http";
}

/** `true` when the current handler is serving an RPC (WebSocket) call. */
export function isRpc(): boolean {
    return contextStorage.getStore()?.scope === "rpc";
}

/**
 * current caller's identity, as returned by the registered identity loader
 * or `null` if anonymous
 */
export function activeUser<T = unknown>(): T | null {
    const ctx = contextStorage.getStore();
    return (ctx ? (ctx.user as T) : null) ?? null;
}

/**
 * emit a server event to the socket that made the current call.
 *
 * only valid inside an RPC handler; throws otherwise. to broadcast to specific
 * users from a background task, use `ephaptic.to(...).emit(name, data)`.
 */
export function emit(name: string, data?: unknown): void {
    const ctx = contextStorage.getStore();
    if (!ctx) {
        throw new Error(
            `emit(${JSON.stringify(name)}) called outside of an RPC context. ` +
            `Use ephaptic.to(...).emit(...) to broadcast from background tasks.`,
        );
    }
    ctx.emit(name, data);
}