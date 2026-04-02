import { encode, decode } from "@msgpack/msgpack";
import { AsyncQueue } from "./queue";

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

class EphapticError extends Error {
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

interface ValidationError extends RpcError {
    code: 'VALIDATION_ERROR';
    data: PydanticErrorDetail[];
} // Why did we even define this?
// Could we do this?
//     if (error.code === 'VALIDATION_ERROR') {
// and TypeScript would pick it up with Pydantic error details? I assume not.
// We will have to fix this when we figure out error handling (ephaptic.py:394)

interface RpcResponse {
    id: number,
    result?: any,
    error?: string | RpcError,
    chunk?: any,
    done?: boolean,
    stream?: boolean,
}

interface ServerEvent {
    type: 'event';
    name: string;
    payload?: {
        args: any[];
        kwargs: Record<string, any>;
    };
}

interface PendingCall {
    resolve: (value: any) => void;
    reject: (reason?: any) => void;
    timer: ReturnType<typeof setTimeout>;
}

function isRpcResponse(data: any): data is RpcResponse {
    return data && typeof data === 'object' && 'id' in data &&
        ('result' in data || 'error' in data || 'chunk' in data || 'done' in data || 'stream' in data);
}

function isServerEvent(data: any): data is ServerEvent {
    return data && typeof data === 'object' && data.type === 'event';
}

function createError(rpcError: string | RpcError) {
    if (typeof rpcError === 'string') return new Error(rpcError);
    return new EphapticError(rpcError.code, rpcError.message, rpcError.data)
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
     * Timeout (ms) to wait before rejecting with a TimeoutError.
     * Default: 30000ms.
     */
    timeout?: number;
}

/**
 * A callback function for events.
 * It receives positional arguments spread out, with the last argument 
 * typically being the keyword arguments object.
 */
export type PortalCallback = (...args: any[]) => void;

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
    _connectionPromise?: Promise<void> | null;
    _pendingStreams: Map<number, AsyncQueue<any>> = new Map();
    retryCount: number = 0;

    constructor(options: EphapticOptions = {}) {
        super();
        this.options = options;

        if (typeof window !== "undefined") this.connect();
    }

    _getUrl() {
        let url = this.options?.url;

        if (url && /^(http|https):\/\//.test(url)) url = url.replace(/^http/, 'ws');
        
        if (url && /^(ws|wss|http|https):\/\//.test(url)) return url;

        if (typeof window === "undefined") return '';

        if (url) {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;

            const path = url.startsWith('/') ? url : '/' + url;

            return `${protocol}//${host}${path}`;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/_ephaptic`;
    }

    _sendInit() {
        const payload: Record<string, any> = { type: 'init' };
        if (this.options?.auth) {
            payload.auth = this.options.auth;
        }
        this.ws?.send(encode(payload));
    }

    connect(): void {
        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) return;

        this.ws = new WebSocket(this._getUrl());
        this.ws.binaryType = "arraybuffer";

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
            this.dispatchEvent(new CustomEvent('connected'));
        }

        this.ws.onmessage = event => {
            const data = decode(event.data);

            if (isRpcResponse(data)) {
                if (data.stream) {
                    const queue = new AsyncQueue<any>();
                    this._pendingStreams.set(data.id, queue);

                    const handlers = this.pendingCalls.get(data.id);
                    if (handlers) {
                        clearTimeout(handlers.timer);
                        handlers.resolve(queue);
                        this.pendingCalls.delete(data.id);
                    }
                } else if ('chunk' in data) {
                    const streamHandler = this._pendingStreams.get(data.id);
                    if (!streamHandler) return console.warn(`Server sent chunk data for nonexistent stream ID: ${data.id}. Ignoring.`);
                    streamHandler.push(data.chunk);
                } else if ('done' in data && data.done === true) {
                    const streamHandler = this._pendingStreams.get(data.id);
                    if (!streamHandler) return console.warn(`Server sent chunk completion for nonexistent stream ID: ${data.id}. Ignoring.`);
                    streamHandler.close();
                    this._pendingStreams.delete(data.id);
                } else if (this.pendingCalls.has(data.id)) {
                    const handlers = this.pendingCalls.get(data.id);
                    if (handlers) {
                        const { resolve, reject, timer } = handlers;
                        clearTimeout(timer);
                        if (data.error) reject(createError(data.error));
                        else resolve(data.result);
                        this.pendingCalls.delete(data.id);
                    }
                } else {
                    console.warn(`Server sent rpc response for nonexistent call ID: ${data.id}. Ignoring.`);
                }
            } else if (isServerEvent(data)) {
                const { args = [], kwargs = {} } = data.payload || {};
                this.dispatchEvent(new CustomEvent(data.name, { detail: { args, kwargs } }));
                this._emit(data.name, args, kwargs)
            }
        }

        this.ws.onclose = () => {
            this._connectionPromise = null;

            this.dispatchEvent(new CustomEvent('disconnected'));

            const baseDelay = 1000;
            const maxDelay = 30000;
            // min(max, base * 2^retries)
            let delay = Math.min(maxDelay, baseDelay * Math.pow(2, this.retryCount)) + Math.random() * 1000;

            console.warn(`[ephaptic] connection lost. reconnecting in ${Math.round(delay)}ms...`);

            this.retryCount++;

            setTimeout(() => this.connect(), delay);
        }
    }

    _emit(name: string, args: any[] = [], kwargs = {}) {
        if (this._emitter.has(name)) {
            const callbacks = this._emitter.get(name);
            if (callbacks) {
                for (const cb of Array.from(callbacks)) {
                    try { cb(...args, kwargs); } catch(e) { console.error(e); }
                }
            }
        }
    }

    /**
     * Register a callback for a server-sent event.
     * @param event The name of the event emitted from Python.
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
        if (!this._emitter.has(event)) return;
        const s = this._emitter.get(event);
        s?.delete(callback);
        if (!s?.size) this._emitter.delete(event);
    }

    /**
     * Register a callback that runs only once.
     * @param event The name of the event.
     * @param callback The function to run.
     */
    once(event: string, callback: PortalCallback) {
        const wrapper = (...args: any[]) => { this.off(event, wrapper); callback(...args); }
        this.on(event, wrapper);
    }

    /**
     * Dynamic RPC methods.
     * Any property not listed above is treated as an RPC call to the server.
     * 
     * Usage: await portal.my_function(arg1, arg2);
     */
    [methodName: string]: ((...args: any[]) => Promise<any>) | any;
    // We probably have to remove this for TypeScript users to stop them from mistyping function names and TypeScript accepting it.
    // Since this is only used for those who are using TypeScript but not using the generated schema.
    // TODO: Do something about this.
}

/**
 * Connect to an Ephaptic server.
 * @param options Configuration options.
 */
export function connect(options?: EphapticOptions) {
    const clientInstance = new EphapticClientBase(options);

    const clientProxy = new Proxy(clientInstance, {
        get(target: any, prop: string) {
            if (prop === 'queries') {
                if (!target._queriesProxy) target._queriesProxy = createQueryProxy(clientProxy);
                return target._queriesProxy;
            }
            if (prop in target) return target[prop];

            return async(...args: any[]) => {
                if (!target.ws || target.ws.readyState !== WebSocket.OPEN) {
                    target.connect();
                    await new Promise<void>((resolve, reject) => {
                        const onSuccess = () => { cleanup(); resolve(); };
                        const onError = () => { cleanup(); reject(new Error("Failed to establish connection.")); };

                        const cleanup = () => {
                            target.removeEventListener('connected', onSuccess);
                            target.removeEventListener('disconnected', onError);
                        };

                        target.addEventListener('connected', onSuccess, { once: true });
                        target.addEventListener('disconnected', onError);
                    });
                }

                if (target._connectionPromise) await target._connectionPromise;
                return new Promise((resolve, reject) => {
                    const id = ++target.callId;
                    const timeoutDuration = target.options?.timeout || 30000;

                    const timer = setTimeout(() => {
                        if (target.pendingCalls.has(id)) {
                            target.pendingCalls.delete(id);
                            // if (target._pendingStreams.has(id)) {
                            //     target._pendingStreams.get(id).close();
                            //     target._pendingStreams.delete(id);
                            // }
                            // I think it's best to not time out streams, they should be allowed to be long-running.
                            reject(new Error(`${prop} timed out; exceeded ${timeoutDuration}ms.`));
                        }
                    }, timeoutDuration);
                    target.pendingCalls.set(id, { resolve, reject, timer });
                    try {
                        target.ws.send(encode({ type: 'rpc', id, name: prop, args }));
                    } catch (err) {
                        clearTimeout(timer);
                        target.pendingCalls.delete(id);
                        reject(err);
                    }
                })
            }
        }
    });

    return clientProxy;
}