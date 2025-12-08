import { encode, decode } from "@msgpack/msgpack";

interface RpcResponse {
    id: number,
    result?: any,
    error?: string,
}

interface ServerEvent {
    type: 'event';
    name: string;
    payload?: {
        args: any[];
        kwargs: Record<string, any>;
    };
}

function isRpcResponse(data: any): data is RpcResponse {
    return data && typeof data === 'object' && 'id' in data && ('result' in data || 'error' in data);
}

function isServerEvent(data: any): data is ServerEvent {
    return data && typeof data === 'object' && data.type === 'event';
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
}

/**
 * A callback function for events.
 * It receives positional arguments spread out, with the last argument 
 * typically being the keyword arguments object.
 */
export type PortalCallback = (...args: any[]) => void;

class EphapticClientBase extends EventTarget {
    options?: EphapticOptions;
    ws?: WebSocket;
    callId: Number = 0;
    pendingCalls: Map<number, { resolve: (value: any) => void, reject: (reason?: any) => void }> = new Map();
    _emitter: Map<string, Set<Function>> = new Map();
    _connectionPromise?: Promise<void> | null;

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

        this.ws.onopen = () => { this.dispatchEvent(new CustomEvent('connected')); }

        this.ws.onmessage = event => {
            const data = decode(event.data);

            if (isRpcResponse(data)) {
                if (this.pendingCalls.has(data.id)) {
                    const handlers = this.pendingCalls.get(data.id);
                    if (handlers) {
                        const { resolve, reject } = handlers;
                        if (data.error) reject(new Error(data.error));
                        else resolve(data.result);
                        this.pendingCalls.delete(data.id);
                    }
                }
            } else if (isServerEvent(data)) {
                const { args = [], kwargs = {} } = data.payload || {};
                this.dispatchEvent(new CustomEvent(data.name, { detail: { args, kwargs } }));
                this._emit(data.name, args, kwargs)
            }
        }

        this.ws.onclose = () => {
            this._connectionPromise = null;
            setTimeout(() => this.connect(), 3000);
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
}

/**
 * Connect to an Ephaptic server.
 * @param options Configuration options.
 */
export function connect(options?: EphapticOptions) {
    const clientInstance = new EphapticClientBase(options);

    const clientProxy = new Proxy(clientInstance, {
        get(target: any, prop: string) {
            if (prop in target) return target[prop];

            return async(...args: any[]) => {
                if (!target.ws) target.connect();
                if (target._connectionPromise) await target._connectionPromise;
                return new Promise((resolve, reject) => {
                    const id = ++target.callId;
                    target.pendingCalls.set(id, { resolve, reject });
                    target.ws.send(encode({ type: 'rpc', id, name: prop, args }));
                })
            }
        }
    });

    return clientProxy;
}