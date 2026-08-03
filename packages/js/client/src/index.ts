import { encode, decode } from "@msgpack/msgpack";
import { AsyncQueue } from "./queue";

declare global {
    interface Window {
        __ephaptic?: {
            invoke(channel: string, ...args: any[]): Promise<any>;
        };
    }
}

interface PydanticErrorDetail {
    loc: (string | number)[];
    msg: string;
    type: string;
    input: any;
}

interface RpcError {
    code: string;
    message: string;
    data?: any;
}

/**
 * error thrown when call fails
 */
export class EphapticError extends Error {
    code: string;
    data?: any;

    constructor(code: string, message: string, data?: any) {
        super(message);
        this.name = "EphapticError";
        this.code = code;
        this.data = data;

        Object.setPrototypeOf(this, EphapticError.prototype);
    }
}

/**
 * pydantic validation error in typescript
 * you can narrow this from typescript using `err.code`.
 */
export interface ValidationError extends RpcError {
    code: 'VALIDATION_ERROR';
    data: PydanticErrorDetail[];
}

export interface RpcResponse {
    id: number,
    result?: any,
    error?: string | RpcError,
    chunk?: any,
    done?: boolean,
    stream?: boolean,
}

export interface ServerEvent {
    type: 'event';
    name: string;
    payload?: {
        args: any[];
        kwargs: Record<string, any>;
    };
}

export interface PendingCall {
    resolve: (value: any) => void;
    reject: (reason?: any) => void;
    timer: ReturnType<typeof setTimeout> | null;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'closed';

function isRpcResponse(data: any): data is RpcResponse {
    return data && typeof data === 'object' && 'id' in data &&
        ('result' in data || 'error' in data || 'chunk' in data || 'done' in data || 'stream' in data);
}

function isServerEvent(data: any): data is ServerEvent {
    return data && typeof data === 'object' && data.type === 'event';
}

function createError(rpcError: string | RpcError) {
    if (typeof rpcError === 'string') return new EphapticError('ERROR', rpcError);
    if (!rpcError || typeof rpcError !== 'object') {
        return new EphapticError('ERROR', 'The server reported an error.');
    }
    return new EphapticError(rpcError.code ?? 'ERROR', rpcError.message ?? '', rpcError.data);
}

export interface EphapticOptions {
    /**
     * The URL path to the backend WebSocket.
     * Defaults to `/_ephaptic` on the current host.
     * Example: "ws://localhost:8000/_ephaptic"
     */
    url?: string;

    /**
     * An auth object you can provide to the server to verify who you are.
     * The server receives this object directly in the identity loader.
     * Example: `auth: { token: window.localStorage.getItem('jwtToken') }`
     * Note: This object must be msgpack serializable.
     */
    auth?: any;

    /**
     * The amount of time (in milliseconds) to wait for a call's initial response before rejecting with code `TIMEOUT`.
     * Default: 30000 (30s); or you can set to `Infinity` to disable the timeout.
     */
    timeout?: number;

    /**
     * Transport you want to use.
     * Either `websocket` or `electron` (uses electron IPC).
     */
    transport?: 'websocket' | 'electron';
}

/**
 * A callback function for events.
 * It receives positional arguments spread out, with the last argument
 * typically being the keyword arguments object.
 */
export type PortalCallback = (...args: any[]) => void; // wow itz still called Portal from the really old era :P

function createQueryProxy(client: any) {
    return new Proxy({}, {
        get(_target, prop: string) {
            return (...args: any[]) => ({
                queryKey: [prop, ...args],
                queryFn: () => client[prop](...args)
            });
        }
    });
}

export class EphapticClientBase extends EventTarget {
    options?: EphapticOptions;
    ws?: WebSocket;
    callId: number = 0;
    pendingCalls: Map<number, PendingCall> = new Map();
    _emitter: Map<string, Set<Function>> = new Map();
    /** callback -> `.once()` wrapper */
    _onceWrappers: Map<string, PortalCallback[]> = new Map();
    _connectionPromise?: Promise<void> | null;
    _pendingStreams: Map<number, AsyncQueue<any>> = new Map();
    retryCount: number = 0;
    _state: ConnectionState = 'disconnected';
    _closedByApp: boolean = false;
    _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    _teardownHandler: ((event?: Event) => void) | null = null;
    // if failed to open initial connection
    _fatal: EphapticError | null = null;

    // tanstack qery
    declare queries: any;

    constructor(options: EphapticOptions = {}) {
        super();
        this.options = options;

        const transport = resolveTransport(options);
        if (typeof window !== "undefined" && transport === 'websocket') this.connect();
    }

    get state(): ConnectionState {
        return this._state;
    }

    _setState(next: ConnectionState) {
        if (this._state === next) return;
        this._state = next;
        this.dispatchEvent(new CustomEvent('statechange', { detail: { state: next } }));
    }

    _getUrl() {
        let url = this.options?.url;

        if (url && /^https?:\/\//i.test(url)) url = url.replace(/^http/i, m => m === 'HTTP' ? 'WS' : 'ws');

        if (url && /^wss?:\/\//i.test(url)) return url;

        if (typeof window === "undefined" || !window.location) return ''; // o noez :(

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;

        if (url) {
            const path = url.startsWith('/') ? url : '/' + url;
            return `${protocol}//${host}${path}`;
        }

        return `${protocol}//${host}/_ephaptic`;
    }

    _sendInit() {
        const payload: Record<string, any> = { type: 'init' };
        if (this.options && 'auth' in this.options && this.options.auth !== undefined) {
            payload.auth = this.options.auth;
        }
        this.ws?.send(encode(payload));
    }

    connect(): void {
        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) return;

        this._closedByApp = false;
        if (this._reconnectTimer !== null) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }

        const url = this._getUrl();
        if (!url) {
            this._fatal = new EphapticError(
                'CONNECT_FAILED',
                'No server address given, and no `window.location` to form a default address to connect to. Please specify where you want to connect.',
            );
            this._failAllPending(this._fatal);
            this._setState('disconnected');
            this.dispatchEvent(new CustomEvent('disconnected'));
            return;
        }

        this._fatal = null;
        this._setState(this.retryCount > 0 ? 'reconnecting' : 'connecting');

        if (this.ws) detachSocket(this.ws);

        try {
            this.ws = new WebSocket(url);
        } catch (err) {
            this.ws = undefined;
            this._failAllPending(new EphapticError('CONNECT_FAILED', `Could not open a connection to ${url}.`));
            this._setState('disconnected');
            this.dispatchEvent(new CustomEvent('disconnected'));
            this._scheduleReconnect();
            return;
        }
        this.ws.binaryType = "arraybuffer";
        this._installTeardownHandler();

        this._connectionPromise = new Promise(resolve => {
            if (this.ws?.readyState === WebSocket.OPEN) {
                this._sendInit();
                resolve();
            } else {
                const finish = () => {
                    this.ws?.removeEventListener('open', finish);
                    this.ws?.removeEventListener('close', finish);
                    this.ws?.removeEventListener('error', finish);
                    if (this.ws?.readyState === WebSocket.OPEN) this._sendInit();
                    resolve();
                }
                this.ws?.addEventListener('open', finish);
                this.ws?.addEventListener('close', finish);
                this.ws?.addEventListener('error', finish);
            }
        });

        this.ws.onopen = () => {
            this.retryCount = 0;
            this.callId = 0;
            this._setState('connected');
            this.dispatchEvent(new CustomEvent('connected'));
        }

        this.ws.onmessage = event => {
            let data: unknown;
            try {
                data = decode(event.data);
            } catch {
                console.warn('[ephaptic] discarding an undecodable frame');
                return;
            }

            try {
                this._dispatchFrame(data);
            } catch (err) {
                console.warn('[ephaptic] discarding a structurally invalid frame', err);
            }
        }

        this.ws.onclose = () => {
            this._connectionPromise = null;

            this._failAllPending(new EphapticError('DISCONNECTED', 'Connection closed before a response was received.'));

            this.dispatchEvent(new CustomEvent('disconnected'));

            if (this._closedByApp) {
                this._setState('closed');
                return;
            }

            this._scheduleReconnect();
        }
    }

    _dispatchFrame(data: unknown) {
        if (isRpcResponse(data)) {
            if ('stream' in data && data.stream) {
                const id = data.id;
                const handlers = this.pendingCalls.get(id);
                if (!handlers) return;
                const queue = new AsyncQueue<any>();
                queue.onAbandon = () => { this._pendingStreams.delete(id); };
                this._pendingStreams.set(id, queue);

                if (handlers.timer !== null) clearTimeout(handlers.timer);
                handlers.resolve(queue);
                this.pendingCalls.delete(id);
            } else if ('chunk' in data) {
                const streamHandler = this._pendingStreams.get(data.id);
                if (!streamHandler) return;
                streamHandler.push(data.chunk);
            } else if ('done' in data && data.done === true) {
                const streamHandler = this._pendingStreams.get(data.id);
                if (!streamHandler) return;
                streamHandler.close();
                this._pendingStreams.delete(data.id);
            } else if ('error' in data && this._pendingStreams.has(data.id)) {
                const streamHandler = this._pendingStreams.get(data.id)!;
                streamHandler.fail(createError(data.error!));
                this._pendingStreams.delete(data.id);
            } else if (this.pendingCalls.has(data.id)) {
                const handlers = this.pendingCalls.get(data.id);
                if (handlers) {
                    const { resolve, reject, timer } = handlers;
                    if (timer !== null) clearTimeout(timer);
                    if ('error' in data) reject(createError(data.error!));
                    else if ('result' in data) resolve(data.result);
                    else reject(new EphapticError(
                        'PROTOCOL_ERROR',
                        "The server is confused. I don't know why.",
                    ));
                    this.pendingCalls.delete(data.id);
                }
            }
        } else if (isServerEvent(data)) {
            const { args = [], kwargs = {} } = data.payload || {};
            this.dispatchEvent(new CustomEvent(`event:${data.name}`, { detail: { args, kwargs } }));
            this._emit(data.name, args, kwargs)
        }
    }

    _failAllPending(err: EphapticError) {
        for (const handlers of this.pendingCalls.values()) {
            if (handlers.timer !== null) clearTimeout(handlers.timer);
            handlers.reject(err);
        }
        this.pendingCalls.clear();
        for (const stream of this._pendingStreams.values()) stream.fail(err);
        this._pendingStreams.clear();
    }

    _scheduleReconnect() {
        if (this._closedByApp) return;

        // min(30000, 1000 * 2^attempt) + RandInt(0, 1000)
        const delay = Math.min(30000, 1000 * Math.pow(2, this.retryCount)) + Math.random() * 1000;
        this.retryCount++;
        this._setState('reconnecting');

        console.warn(`[ephaptic] connection lost. reconnecting in ${Math.round(delay)}ms...`);

        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (this._closedByApp) return;
            this.connect();
        }, delay);
        (this._reconnectTimer as any)?.unref?.();
    }

    _installTeardownHandler() {
        if (this._teardownHandler || typeof window === 'undefined' || !window.addEventListener) return;
        this._teardownHandler = (event?: Event) => {
            if ((event as PageTransitionEvent | undefined)?.persisted) return;
            this.disconnect();
        };
        window.addEventListener('pagehide', this._teardownHandler);
    }

    /**
     * Close transport, stop the automatic recconector, and release resources owned by the client.
     * You can then reconnect by calling `connect()` again.
     */
    disconnect(): void {
        this._closedByApp = true;

        if (this._reconnectTimer !== null) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }

        if (this._teardownHandler && typeof window !== 'undefined' && window.removeEventListener) {
            window.removeEventListener('pagehide', this._teardownHandler);
            this._teardownHandler = null;
        }

        const socket = this.ws;
        this.ws = undefined;
        this._connectionPromise = null;

        if (socket) detachSocket(socket);

        this._failAllPending(new EphapticError('DISCONNECTED', 'The client disconnected.'));

        this.dispatchEvent(new CustomEvent('disconnected'));

        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            try { socket.close(1000, 'Client disconnected'); } catch { /* already closing */ }
        }

        this._setState('closed');
    }

    /**
     * (you probably don't need this)
     * Invoke a server function by its name.
     * You probably would only use this if your function's name collides with the client's functions/properties,
     * e.g. `connect` or (if you're a masochist) `__proto__`.
     */
    call<T = any>(name: string, ...args: any[]): Promise<T> {
        return this._invoke(name, args) as Promise<T>;
    }

    async _invoke(name: string, args: any[]): Promise<any> {
        const transport = resolveTransport(this.options ?? {});

        if (transport === 'electron') {
            if (typeof window === 'undefined' || !window.__ephaptic) {
                throw new EphapticError('TRANSPORT_UNAVAILABLE', 'ephaptic: Electron IPC not found on window.');
            }
            try {
                return await window.__ephaptic.invoke(name, ...args);
            } catch (err: any) {
                if (err instanceof EphapticError) throw err;
                throw new EphapticError(err?.code ?? 'INTERNAL', err?.message ?? String(err), err?.data);
            }
        }

        if (this._closedByApp) {
            throw new EphapticError('DISCONNECTED', 'The client has been disconnected.');
        }
        if (this._fatal) throw this._fatal;

        const configured = this.options?.timeout;
        const timeoutDuration = typeof configured === 'number' && Number.isFinite(configured) && configured > 0
            ? configured
            : 30000;
        const deadline = Date.now() + timeoutDuration;

        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.connect();
            if (this._fatal) throw this._fatal;
            await new Promise<void>((resolve, reject) => {
                const onSuccess = () => { cleanup(); resolve(); };
                const onError = () => {
                    cleanup();
                    reject(new EphapticError('CONNECT_FAILED', 'Failed to establish a connection.'));
                };
                const onTimeout = () => {
                    cleanup();
                    reject(new EphapticError('TIMEOUT', `${name} timed out while connecting; exceeded ${timeoutDuration}ms.`));
                };
                const connectTimer = setTimeout(onTimeout, Math.max(1, deadline - Date.now()));
                const cleanup = () => {
                    clearTimeout(connectTimer);
                    this.removeEventListener('connected', onSuccess);
                    this.removeEventListener('disconnected', onError);
                };
                this.addEventListener('connected', onSuccess, { once: true });
                this.addEventListener('disconnected', onError);
            });
        }

        if (this._connectionPromise) await this._connectionPromise;

        let frame: Uint8Array;
        try {
            frame = encode({ type: 'rpc', id: this.callId + 1, name, args });
        } catch (err: any) {
            throw new EphapticError('ENCODE_ERROR', `Could not encode the arguments of '${name}': ${err?.message ?? err}`);
        }

        return new Promise((resolve, reject) => {
            const id = ++this.callId;

            const timer = setTimeout(() => {
                if (this.pendingCalls.has(id)) {
                    this.pendingCalls.delete(id);
                    reject(new EphapticError('TIMEOUT', `${name} timed out; exceeded ${timeoutDuration}ms.`));
                }
            }, Math.max(1, deadline - Date.now()));

            this.pendingCalls.set(id, { resolve, reject, timer });

            try {
                // @ts-ignore it fucking works, don't touch it
                this.ws!.send(frame);
            } catch (err: any) {
                if (timer !== null) clearTimeout(timer);
                this.pendingCalls.delete(id);
                reject(new EphapticError('DISCONNECTED', `The connection closed before '${name}' could be sent.`));
            }
        });
    }

    _emit(name: string, args: any[] = [], kwargs = {}) {
        const callbacks = this._emitter.get(name);
        if (!callbacks) return;
        for (const cb of Array.from(callbacks)) {
            try {
                const result = cb(...args, kwargs);
                if (result && typeof (result as any).catch === 'function') {
                    (result as Promise<unknown>).catch(e => console.error(e));
                }
            } catch (e) { console.error(e); }
        }
    }

    /**
     * Register a callback for a server-sent event.
     * @param event The name of the event emitted from the server.
     * @param callback The function to run when data is received.
     */
    on(event: string, callback: PortalCallback) {
        if (!this._emitter.has(event)) this._emitter.set(event, new Set());
        this._emitter.get(event)?.add(callback);
    }

    /**
     * Remove a specific callback for an event.
     * @param event The name of the event.
     * @param callback The function to remove.
     */
    off(event: string, callback: PortalCallback) {
        const s = this._emitter.get(event);
        if (!s) return;
        s.delete(callback);
        const key = onceKey(event, callback);
        for (const wrapper of this._onceWrappers.get(key) ?? []) s.delete(wrapper);
        this._onceWrappers.delete(key);
        if (!s.size) this._emitter.delete(event);
    }

    /**
     * Register a callback that runs only once.
     * @param event The name of the event.
     * @param callback The function to run.
     */
    once(event: string, callback: PortalCallback) {
        const wrapper = (...args: any[]) => {
            this._removeOnce(event, callback, wrapper);
            callback(...args);
        };
        const key = onceKey(event, callback);
        const existing = this._onceWrappers.get(key);
        if (existing) existing.push(wrapper);
        else this._onceWrappers.set(key, [wrapper]);
        this.on(event, wrapper);
    }

    private _removeOnce(event: string, callback: PortalCallback, wrapper: PortalCallback) {
        const s = this._emitter.get(event);
        if (s) {
            s.delete(wrapper);
            if (!s.size) this._emitter.delete(event);
        }
        const key = onceKey(event, callback);
        const list = this._onceWrappers.get(key);
        if (!list) return;
        const at = list.indexOf(wrapper);
        if (at !== -1) list.splice(at, 1);
        if (!list.length) this._onceWrappers.delete(key);
    }

    // [methodName: string]
    // removed because otherwise mistyping a RPC function wouldn't show an error when using typed client
    // and hopefully everyone is using the typed client rather than using typescript AND no typed client
}

function detachSocket(socket: WebSocket): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
}

const onceKeys = new WeakMap<Function, string>();
let onceKeySeq = 0;

function onceKey(event: string, callback: Function): string {
    let id = onceKeys.get(callback);
    if (id === undefined) {
        id = String(++onceKeySeq);
        onceKeys.set(callback, id);
    }
    return `${event}\u0000${id}`;
}

function resolveTransport(options: EphapticOptions): 'websocket' | 'electron' {
    if (options.transport) return options.transport;
    const websocketAvailable = typeof WebSocket !== 'undefined';
    const electronAvailable = typeof window !== 'undefined' && '__ephaptic' in window;
    if (!websocketAvailable && electronAvailable) return 'electron';
    return 'websocket';
}

/**
 * Connect to an Ephaptic server.
 * @param options Configuration options.
 */
export function connect(options?: EphapticOptions): EphapticClientBase {
    const clientInstance = new EphapticClientBase(options);

    const clientProxy: EphapticClientBase = new Proxy(clientInstance, {
        get(target: any, prop: string | symbol) {
            if (typeof prop === 'symbol') return (target as any)[prop];

            if (prop === 'queries') {
                if (!target._queriesProxy) target._queriesProxy = createQueryProxy(clientProxy);
                return target._queriesProxy;
            }

            if (prop === 'then') return undefined; // dont make it thenable or else some fool will try `await connect()` and be unable to debug why client is sending `.then()` RPC :/
            if (prop === 'toJSON' || prop === 'inspect' || prop === 'constructor') return undefined;
            if (prop in target) {
                const value = target[prop];
                return typeof value === 'function' ? value.bind(target) : value;
            }

            return (...args: any[]) => target._invoke(prop, args);
        }
    });

    return clientProxy;
}