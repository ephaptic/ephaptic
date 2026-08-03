import { createRequire } from "node:module";
import { encode, decode } from "@msgpack/msgpack";

import { ServiceError, type WireError } from "./errors.js";
import { ConnectionManager } from "./connection-manager.js";
import { contextStorage } from "./context.js";
import { checkRateLimit, parseLimit, type Limit } from "./ratelimit.js";
import { SendQueue, type Transport } from "./transport.js";
import { getParamNames, hasRestParameter } from "./util.js";
import { Router } from "./router.js";

const require = createRequire(import.meta.url);

/** map of name -> function (used by electron adapter) */
export type Routes = Record<string, (...args: any[]) => any>;

const EXPOSE_OPTION_KEYS = ["name", "rateLimit", "requiresLogin"] as const;

export function assertKnownOptions(opts: object, known: readonly string[], where: string): void {
    const unknown = Object.keys(opts ?? {}).filter(k => !known.includes(k));
    if (unknown.length) {
        throw new Error(
            `Unknown option(s) for ${where}: ${unknown.sort().join(", ")}. ` +
            `Valid options are: ${[...known].sort().join(", ")}.`,
        );
    }
}

/** per-handler options passed to {@link Ephaptic.expose}. */
export interface ExposeOptions {
    /** register the handler under a different name than the function's own. */
    name?: string;
    /** fixed-window rate limit, e.g. `"5/m"`, `"100/hour"`, `"10 per 30s"`. */
    rateLimit?: string;
    /** reject the call with UNAUTHORIZED unless an identity was loaded. */
    requiresLogin?: boolean;
}

/**
 * exception matcher: either an `Error` subclass (matched with `instanceof`)
 * or a predicate `(err) => boolean`.
 */
export type ExceptionMatcher = (new (...args: any[]) => Error) | ((err: unknown) => boolean);

/** registered exception handler: turns a thrown error into a wire error. */
export type ExceptionHandlerFn = (err: unknown) => WireError | ServiceError | Record<string, unknown> | string | void | Promise<WireError | ServiceError | Record<string, unknown> | string | void>;

/** value returned by the identity loader becomes the "current user". */
export type IdentityLoaderFn = (auth: unknown) => unknown | Promise<unknown>;

/** loads current user for an HTTP (Router) request from the raw request. */
export type HttpIdentityLoaderFn = (req: unknown) => unknown | Promise<unknown>;

interface HandlerEntry {
    fn: (...args: any[]) => any;
    hasRest: boolean;
    rateLimit?: Limit;
    requiresLogin: boolean;
    arity: number;
    paramNames: string[];
}

interface RpcFrame {
    type: "rpc";
    id: number;
    name: string;
    args?: unknown[];
    kwargs?: Record<string, unknown>;
}

export interface AttachOptions {
    /** WebSocket path to mount on. Defaults to `/_ephaptic`. */
    path?: string;
}

/** handle to a broadcast target created by {@link Ephaptic.to}. */
export class EphapticTarget {
    constructor(
        private userIds: string[],
        private manager: ConnectionManager,
    ) {}

    /** broadcast an event to the selected users. */
    async emit(name: string, data?: unknown): Promise<void> {
        await this.manager.broadcast(this.userIds, name, [], asEventKwargs(data));
    }
}

/**
 * a live connection. it owns its send queue (unique per connection), tracks in-flight RPCs and
 * open streams, and holds the resolved identity. one per socket.
 */
class Session {
    /** the identity, exactly as the loader returned it */
    identity: unknown = null;
    /** the registry key for the user derived from the identity */
    uid: string | null = null;
    closed = false;

    private initialized = false;
    private readyResolve!: () => void;
    ready: Promise<void>;

    private activeStreams = new Set<{ return?: (value?: any) => any }>();
    private inflight = new Set<Promise<unknown>>();

    constructor(
        private ephaptic: Ephaptic,
        public transport: Transport,
    ) {
        this.ready = new Promise<void>(resolve => { this.readyResolve = resolve; });
    }

    trackStream(stream: { return?: (value?: any) => any }): void {
        this.activeStreams.add(stream);
    }

    untrackStream(stream: { return?: (value?: any) => any }): void {
        this.activeStreams.delete(stream);
    }

    async onMessage(raw: Uint8Array): Promise<void> {
        let data: unknown;
        try {
            data = decode(raw);
        } catch {
            console.warn("[WARN] Malformed mesasge; ignoring.")
            return;
        }

        if (!this.initialized) {
            this.initialized = true;
            const frame = data as { type?: string; auth?: unknown };
            if (frame && frame.type === "init") {
                try {
                    await this.ephaptic._loadIdentity(this, frame.auth);
                } finally {
                    this.readyResolve();
                }
                return;
            }
            // no init frame? connection will be treated as anonymous; initial frame will be processed like others instead of being dropped.
            this.readyResolve();
        }

        this.dispatch(data);
    }

    private dispatch(data: unknown): void {
        const frame = data as { type?: string };
        if (frame && frame.type === "rpc") {
            const p = this.ephaptic._handleRpc(this, data as RpcFrame);
            this.inflight.add(p);
            void p.finally(() => this.inflight.delete(p));
        }
    }

    onClose(): void {
        if (this.closed) return;
        this.closed = true;
        if (this.uid != null) this.ephaptic.manager.remove(this.uid, this.transport);
        // stop/return running generators
        for (const stream of this.activeStreams) {
            try {
                stream.return?.();
            } catch { /* ignore */ }
        }
        this.activeStreams.clear();
    }
}

export interface EphapticOptions {
    
    /** Enabling this sends error details (name + stack) to clients for unhandled errors. Try not to enable in production. */
    debug?: boolean;

    /** Redis URL for horizontal scaling of events + rate limits. */
    redisUrl?: string;

    /**
     * Origin allow-list for incoming WebSocket connections. WebSockets aren't
     * subject to the same-origin policy. `null` (default) allows all origins.
     */
    allowedOrigins?: string[] | null;
    
    /**
     * When set, the name of a request header to read the real client IP from
     * for rate limiting (e.g. `"X-Forwarded-For"`, `"CF-Connecting-IP"`).
     * Only enable behind a trusted proxy, otherwise clients can spoof it.
     * For comma-separated headers (X-Forwarded-For) the first entry is used.
     */
    ipHeader?: string;
}

export class Ephaptic {
    readonly manager = new ConnectionManager();
    debug = false;

    private functions = new Map<string, HandlerEntry>();
    private exceptionHandlers: { match: (err: unknown) => boolean; handler: ExceptionHandlerFn; depth: number }[] = [];
    private identityLoaderFn: IdentityLoaderFn | null = null;
    private httpIdentityLoaderFn: HttpIdentityLoaderFn | null = null;
    private allowedOrigins: string[] | null = null;
    private ipHeader: string | null = null;

    constructor(options: EphapticOptions = {}) {
        this.debug = options.debug ?? (
            typeof process !== "undefined" && process.env?.NODE_ENV
                ? process.env.NODE_ENV === "development"
                : false
        );
        this.allowedOrigins = options.allowedOrigins ?? null;
        this.ipHeader = options.ipHeader ?? null;
        if (options.redisUrl) {
            this.manager.initRedis(options.redisUrl).catch((err) => {
                console.error("[ERROR] failed to init Redis:", err);
            });
        }
    }

    // ## Registration

    /** Expose a single function. */
    expose(name: string, fn: (...args: any[]) => any, opts: ExposeOptions = {}): this {
        assertKnownOptions(opts, EXPOSE_OPTION_KEYS, "expose");
        const registerName = opts.name ?? name;
        if (this.functions.has(registerName)) {
            throw new Error(
                `An RPC named '${registerName}' is already registered. Give one of the ` +
                `colliding registrations an explicit name; RPC dispatch does not consider ` +
                `the HTTP method.`,
            );
        }
        this.functions.set(registerName, {
            fn,
            rateLimit: opts.rateLimit ? parseLimit(opts.rateLimit) : undefined,
            requiresLogin: opts.requiresLogin ?? false,
            arity: fn.length, // https://en.wikipedia.org/wiki/Arity
            paramNames: getParamNames(fn),
            hasRest: hasRestParameter(fn),
        });
        return this;
    }

    /** Expose a whole map of handlers at once. */ // its cleaner than manual route objects ig
    exposeAll(routes: Routes): this {
        for (const [name, fn] of Object.entries(routes)) this.expose(name, fn);
        return this;
    }

    /** Register the identity loader. Its return value becomes the current user. */
    identityLoader(fn: IdentityLoaderFn): this {
        this.identityLoaderFn = fn;
        return this;
    }

    /**
     * Register the HTTP identity loader, used for Router routes served over HTTP.
     * The function receives the raw request object, and its return value becomes `activeUser()`.
     */
    httpIdentityLoader(fn: HttpIdentityLoaderFn): this {
        this.httpIdentityLoaderFn = fn;
        return this;
    }

    /** Create a {@link Router} bound to this instance (RPC + optional HTTP). */
    router(): Router {
        return new Router(this);
    }

    /**
     * Register an exception handler. `matcherOrClass` is either a subclass of `Error` or a predicate `err => boolean` arrow function.
     */
    exceptionHandler(matcherOrClass: ExceptionMatcher, fn: ExceptionHandlerFn): this {
        const isClass = isErrorClass(matcherOrClass);
        const match = isClass
            ? (err: unknown) => err instanceof (matcherOrClass as new (...args: any[]) => Error)
            : (matcherOrClass as (err: unknown) => boolean);
        let depth = -1;
        if (isClass) {
            depth = 0;
            let proto = (matcherOrClass as any).prototype;
            while (proto && proto !== Error.prototype) {
                proto = Object.getPrototypeOf(proto);
                depth++;
            }
        }
        this.exceptionHandlers.push({ match, handler: fn, depth });
        this.exceptionHandlers.sort((a, b) => b.depth - a.depth);
        return this;
    }

    // ## Events

    /** Target one or more users for a broadcast: `to(a, b).emit(name, data)`. */
    to(...ids: (unknown | unknown[])[]): EphapticTarget {
        const targets: unknown[] = [];
        for (const id of ids) {
            if (Array.isArray(id)) targets.push(...id);
            else targets.push(id);
        }
        return new EphapticTarget(targets.map(identityKey), this.manager);
    }

    // ## Mounting

    /**
     * Mount the WebSocket transport onto an existing Node HTTP server.
     * Requires the optional `ws` peer dependency.
     */
    attach(httpServer: unknown, opts: AttachOptions = {}): unknown {
        const { WebSocketServer } = loadWs();
        const path = opts.path ?? "/_ephaptic";
        const wss = new WebSocketServer({ server: httpServer, path });
        this.wireServer(wss);
        return wss;
    }

    /**
     * Create a standalone HTTP server, mount the WebSocket transport, and start
     * listening. Returns the underlying HTTP server.
     */
    listen(port: number, opts: AttachOptions = {}): unknown {
        const http = require("node:http");
        const server = http.createServer();
        this.attach(server, opts);
        server.listen(port);
        return server;
    }

    private wireServer(wss: any): void {
        wss.on("connection", (ws: any, req: any) => {
            if (this.allowedOrigins) {
                const origin: string | undefined = req?.headers?.origin;
                if (!origin || !this.allowedOrigins.includes(origin)) {
                    ws.close(1008); // policy violation
                    return;
                }
            }

            let remoteAddr: string | undefined = req?.socket?.remoteAddress ?? undefined;
            if (this.ipHeader) {
                const raw = req?.headers?.[this.ipHeader.toLowerCase()];
                const value = Array.isArray(raw) ? raw[0] : raw;
                if (typeof value === "string" && value.length) {
                    remoteAddr = value.split(',')[0].trim();
                }
            }

            const transport = new WebSocketTransport(ws, remoteAddr);
            const session = new Session(this, transport);

            ws.on("message", (data: any, isBinary: boolean) => {
                void session.onMessage(normalizeIncoming(data, isBinary));
            });
            ws.on("close", () => session.onClose());
            ws.on("error", () => session.onClose());
        });
    }

    // ## Identity

    async _loadIdentity(session: Session, auth: unknown): Promise<void> {
        if (!this.identityLoaderFn) return;
        try {
            const identity = await this.identityLoaderFn(auth);
            if (identity != null) {
                let key: string;
                try {
                    key = identityKey(identity);
                } catch (err) {
                    console.error(
                        "[ERROR] the identity loader returned a value that cannot be used as a registry key; " +
                        "treating the connection as anonymous. Return a string, a number, a bigint, or a boolean.", // please don't return a boolean
                        err,
                    );
                    return;
                }
                session.identity = identity;
                session.uid = key;
                if (!session.closed) this.manager.add(session.uid, session.transport);
            }
        } catch (err) {
            console.error("[ERROR] identity loader threw:", err);
        }
    }

    /** @internal */
    async _loadHttpIdentity(req: unknown): Promise<unknown> {
        if (!this.httpIdentityLoaderFn) return null;
        try {
            const identity = (await this.httpIdentityLoaderFn(req)) ?? null;
            if (identity === null) return null;
            try {
                identityKey(identity);
            } catch (err) {
                console.error(
                    "[ERROR] the HTTP identity loader returned a value that cannot be used as a registry key; " +
                    "treating the request as anonymous. Return a string, a number, a bigint, or a boolean.", // if you return a boolean you are masochist
                    err,
                );
                return null;
            }
            return identity;
        } catch (err) {
            console.error("[ERROR] HTTP identity loader threw:", err);
            return null;
        }
    }

    _ipHeaderName(): string | null {
        return this.ipHeader;
    }

    _identityKey(identity: unknown): string {
        return identityKey(identity);
    }

    _clientIp(req: any): string | null {
        if (this.ipHeader) {
            const raw = req?.headers?.[this.ipHeader.toLowerCase()];
            const value = Array.isArray(raw) ? raw[0] : raw;
            if (typeof value === "string" && value.length) return value.split(",")[0].trim();
        }
        return req?.socket?.remoteAddress ?? req?.ip ?? null;
    }

    /// ## RPC

    async _handleRpc(session: Session, data: RpcFrame): Promise<void> {
        await session.ready;

        const callId = data.id;
        const funcName = data.name;
        const args = Array.isArray(data.args) ? data.args : [];
        const kwargs = data.kwargs && typeof data.kwargs === "object" ? data.kwargs : {};

        try {
            const entry = this.functions.get(funcName);
            if (!entry) {
                await this.sendError(session.transport, callId, {
                    code: "NOT_FOUND",
                    message: `Function '${funcName}' not found.`,
                    data: null,
                });
                return;
            }

            if (entry.requiresLogin && session.uid == null) {
                await this.sendError(session.transport, callId, {
                    code: "UNAUTHORIZED",
                    message: "Unauthorized.",
                    data: null,
                });
                return;
            }

            if (entry.rateLimit) {
                await checkRateLimit(this.manager, funcName, entry.rateLimit, {
                    uid: session.uid,
                    ip: session.transport.remoteAddr ?? null,
                }); // this function throws the RatelimitExceededError (it's a ServiceError) if limit is reached
            }

            const callArgs = this.bindArgs(entry, args, kwargs);
            if (callArgs === null) {
                await this.sendError(session.transport, callId, {
                    code: "VALIDATION_ERROR",
                    message: `Function '${funcName}' expects at least ${entry.arity} argument(s), got ${args.length}.`,
                    data: null,
                });
                return;
            }

            const emitToCaller = (name: string, payload?: unknown) => {
                const frame = encode({
                    type: "event",
                    name,
                    payload: { args: [], kwargs: asEventKwargs(payload) },
                });
                session.transport.send(frame).catch(() => {});
            };

            // run the entire call, including stream iteration, in the ALS scope.
            // generator handler's body only executes when it's iterated (in runStream)
            // so we can't leave iteration outside this scope; else `activeUser()` would lose context mid-stream
            await contextStorage.run({ user: session.identity, emit: emitToCaller, scope: "rpc" }, async () => {
                const result = await Promise.resolve(entry.fn(...callArgs));

                const tag = Object.prototype.toString.call(result);
                const isAsyncGen = tag === "[object AsyncGenerator]";
                const isSyncGen = tag === "[object Generator]";

                if (isAsyncGen || isSyncGen) {
                    await this.runStream(session, callId, result as any, isAsyncGen);
                    return;
                }

                await this.send(session.transport, { id: callId, result });
            });
        } catch (err) {
            if (isConnectionClosed(err)) return;
            await this.sendResolvedError(session.transport, callId, err);
        }
    }

    private async runStream(
        session: Session,
        callId: number,
        gen: AsyncGenerator | Generator,
        isAsyncGen: boolean,
    ): Promise<void> {

        session.trackStream(gen as any);

        try {
            await this.send(session.transport, { id: callId, stream: true });

            if (isAsyncGen) {
                for await (const chunk of gen as AsyncGenerator) {
                    if (session.closed || !session.transport.isOpen) break;
                    await this.send(session.transport, { id: callId, chunk });
                }
            } else {
                for (const chunk of gen as Generator) {
                    if (session.closed || !session.transport.isOpen) break;
                    await this.send(session.transport, { id: callId, chunk });
                }
            }

            await this.send(session.transport, { id: callId, done: true });
        } catch (err) {
            try { (gen as any).return?.(); } catch { /* who cares */ }
            if (isConnectionClosed(err) || session.closed) return;
            await this.sendResolvedError(session.transport, callId, err);
        } finally {
            session.untrackStream(gen as any);
        }
    }

    /**
     * build the args list. returns `null` when required args are missing.
     * JS client sends only args, but python client may send `kwargs` which we attempt to merge by param name
     */
    private bindArgs(
        entry: HandlerEntry,
        args: unknown[],
        kwargs: Record<string, unknown>,
    ): unknown[] | null {
        const callArgs = [...args];
        const kwargKeys = Object.keys(kwargs);
        if (kwargKeys.length && entry.paramNames.length && !entry.hasRest) {
            for (let i = 0; i < entry.paramNames.length; i++) {
                const name = entry.paramNames[i];
                if (callArgs[i] === undefined && name in kwargs) {
                    callArgs[i] = kwargs[name];
                }
            }
        }
        if (callArgs.length < entry.arity) return null;
        return callArgs;
    }

    // ## Error resolution

    /** @internal */ // <-- use this decorator because fx doesn't start with `_` so it might not be obvious
    async resolveError(err: unknown): Promise<WireError> {
        if (!(err instanceof ServiceError)) {
            console.error("[ERROR] unhandled handler error:", err);
        }

        if (err instanceof ServiceError) return err.toWire();

        for (const { match, handler } of this.exceptionHandlers) {
            let matched = false;
            try { matched = match(err); } catch { matched = false; }
            if (!matched) continue;
            try {
                const result = await handler(err);
                return normalizeHandlerResult(result);
            } catch (handlerErr) {
                console.error("[ERROR] an exception handler itself threw:", handlerErr);
                break;
            }
        }

        const e = err as Error;
        if (this.debug) {
            return {
                code: "INTERNAL",
                message: `${e?.name ?? "Error"}: ${e?.message ?? String(err)}`,
                data: { stack: e?.stack ?? null },
            };
        }
        return { code: "INTERNAL", message: "Internal server error.", data: null };
    }

    private async sendResolvedError(transport: Transport, callId: number, err: unknown): Promise<void> {
        const wire = await this.resolveError(err);
        await this.sendError(transport, callId, wire);
    }

    private async sendError(transport: Transport, callId: number, wire: WireError): Promise<void> {
        await this.send(transport, { id: callId, error: wire });
    }

    private async send(transport: Transport, frame: Record<string, unknown>): Promise<void> {
        try {
            await transport.send(encode(frame));
        } catch { /* connection closed; nothing to do, so we */ return; }
    }
}

// ## Transport implementation


class WebSocketTransport implements Transport {
    private queue = new SendQueue();

    constructor(
        private ws: any,
        readonly remoteAddr?: string,
    ) {}

    get isOpen(): boolean {
        return this.ws.readyState === 1; // ws.OPEN
    }

    send(data: Uint8Array): Promise<void> {
        return this.queue.enqueue(
            () => new Promise<void>((resolve, reject) => {
                this.ws.send(data, (err?: Error) => {
                    if (err) reject(err);
                    else resolve();
                });
            }),
        );
    }
}

// ## Helpers

function loadWs(): any {
    try { return require("ws"); }
    catch { throw new Error("The 'ws' package is required to use the WebSocket transport. Install it with `npm install ws`."); }
}

function normalizeIncoming(data: any, _isBinary: boolean): Uint8Array {
    if (data instanceof ArrayBuffer) return new Uint8Array(data);
    if (Array.isArray(data)) return Buffer.concat(data);
    return data as Uint8Array; // Buffer
}

function isErrorClass(m: ExceptionMatcher): m is new (...args: any[]) => Error {
    return typeof m === "function" && (m === (Error as any) || m.prototype instanceof Error);
}

function isConnectionClosed(err: unknown): boolean {
    const code = (err as { code?: string })?.code;
    return code === "ERR_STREAM_WRITE_AFTER_END" || code === "EPIPE";
}

function normalizeHandlerResult(result: unknown): WireError {
    if (result instanceof ServiceError) return result.toWire();
    if (typeof result === "string") return { code: "ERROR", message: result, data: null };
    if (result && typeof result === "object" && "code" in (result as any)) {
        const r = result as Record<string, unknown>;
        return {
            code: String(r.code),
            message: typeof r.message === "string" ? r.message : '',
            data: r.data ?? null,
        };
    }
    return { code: "INTERNAL", message: "Internal server error.", data: null };
}

export function asEventKwargs(payload: unknown): Record<string, unknown> {
    if (payload == null) return {};
    if (typeof payload === "object" && !Array.isArray(payload)) return payload as Record<string, unknown>;
    return { value: payload };
}

function identityKey(identity: unknown): string {
    switch (typeof identity) {
        case "string": return "s:" + identity;
        case "number": return Number.isFinite(identity) ? "n:" + identity : "n:" + String(identity);
        case "bigint": return "n:" + identity.toString();
        case "boolean": return "b:" + (identity ? "1" : "0");
    }
    throw new ServiceError(
        `An identity of type ${identity === null ? "null" : typeof identity} cannot be reduced to a key. ` +
        `Return a string, a number, or a boolean from the identity loader.`,
        { code: "INTERNAL", statusCode: 500 },
    );
}