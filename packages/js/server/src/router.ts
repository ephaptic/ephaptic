import { createRequire } from "node:module";

import { ServiceError, type WireError } from "./errors.js";
import { checkRateLimit, parseLimit, type Limit } from "./ratelimit.js";
import { contextStorage, type HandlerContext } from "./context.js";
import { getParamNames, coerceScalar } from "./util.js";
import { assertKnownOptions } from "./ephaptic.js";
import type { Ephaptic } from "./ephaptic.js";

const require = createRequire(import.meta.url);

const HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"] as const; // we may have to add QUERY due to [RFC 10008](https://www.rfc-editor.org/rfc/rfc10008.html)
type HttpMethod = (typeof HTTP_METHODS)[number];

/** per-route options. */
export interface RouteOptions {
    
    /**
     * any custom name you wish to expose the RPC function under.
     * defaults to the function name. but if there is no name (e.g. `args => res`)
     * then the name is derived from the HTTP path.
     */
    name?: string;

    /** fixed-window rate limit, e.g. `"5/m"` (applies to both RPC and HTTP). */
    rateLimit?: string;

    /** when this is enabled, anonymous requests will fail with UNAUTHORIZED/401 */
    requiresLogin?: boolean;
}

/**
 * a framework-agnostic request, for {@link Router.routes}.
 * the `express()` adapter builds one of these from an Express request.
 */
export interface GenericRequest {
    method?: HttpMethod;
    params?: Record<string, string>;
    query?: Record<string, unknown>;
    body?: unknown;
    headers?: Record<string, unknown>;
    ip?: string | null;
    
    /** the raw underlying request, which will be given to the HTTP identity loader. */
    raw?: unknown;
}

/** a framework-agnostic response, for {@link Router.routes}. */
export interface GenericResponse {
    status: number;
    headers: Record<string, string>;
    /** JSON-serialisable body (mutually exclusive with `stream`) */
    json?: unknown;
    /** stream of JSON-serialisable items, served as `application/jsonl`. */
    stream?: AsyncIterable<unknown>;
}

interface RouteDef {
    method: HttpMethod;
    /** As declared, in `{param}` form. */
    path: string;
    /** Express-style `:param`, for the bundled adapter only. */
    expressPath: string;
    rpcName: string;
    handler: (...args: any[]) => any;
    paramNames: string[];
    rateLimit?: Limit;
    requiresLogin: boolean;
}

/**
 * This is the Ephaptic Router.
 * 
 * It is mirrored off the FastAPI style. Each handler is exposed as both an RPC method, and a HTTP route.
 * The handler serves both HTTP and RPC requests.
 *
 * The RPC name (what ephaptic clients call) comes from the function's name, or the `name` option,
 * or a name derived from the path (if the function is unnamed); see {@link RouteOptions.name}.
 * 
 * Both functions and anonymous arrows work:
 *
 *     router.get("/sum", function sum(a, b) { return a + b; });    // -> "sum"
 *     router.get("/sum", (a, b) => a + b);                         // -> "sum"
 *     router.get("/users/{id}", id => { return logic(); });        // -> "users"
 *     router.get("/users/{id}/posts", getPosts);                   // -> "getPosts"
 *
 * Two routes that derive/declare the same RPC name will collide, so you may wish to define an explicit name for these.
 * Ex. `createItem` and `deleteItem` for `PUT /items` and `DELETE /items`
 *
 * HTTP request -> handler argument mapping:
 *   - Path params: from `{param}` placeholders in the path
 *   - GET/DELETE: remaining params come from the query string
 *   - POST/PUT/PATCH: a single remaining param receives the whole JSON body;
 *     multiple remaining params are read from the JSON body object by name.
 * 
 * This matches FastAPI and I'm pretty sure it also matches your average DX-oriented HTTP server in JS/TS (though I hold no authority to confirm the latter).
 */
export class Router {
    private _routes: RouteDef[] = [];

    constructor(private ephaptic: Ephaptic) {}

    // Before you yell at me to keep things DRY, this is actually necessary for full types. (DX > CQ :P)

    get(path: string, handler: (...args: any[]) => any, opts?: RouteOptions): this {
        return this._register("GET", path, handler, opts);
    }
    post(path: string, handler: (...args: any[]) => any, opts?: RouteOptions): this {
        return this._register("POST", path, handler, opts);
    }
    put(path: string, handler: (...args: any[]) => any, opts?: RouteOptions): this {
        return this._register("PUT", path, handler, opts);
    }
    delete(path: string, handler: (...args: any[]) => any, opts?: RouteOptions): this {
        return this._register("DELETE", path, handler, opts);
    }
    patch(path: string, handler: (...args: any[]) => any, opts?: RouteOptions): this {
        return this._register("PATCH", path, handler, opts);
    }

    private static readonly _ROUTE_OPTION_KEYS = ["name", "rateLimit", "requiresLogin"] as const;

    private _register(
        method: HttpMethod,
        path: string,
        handler: (...args: any[]) => any,
        opts?: RouteOptions,
    ): this {
        if (opts) assertKnownOptions(opts, Router._ROUTE_OPTION_KEYS, `router.${method.toLowerCase()}`);

        /** I am not going to comment how names are derived for the hundredth time. Just hover over {@link deriveName} in your editor, for god's sake. */
        // yes i made that a JSDoc comment so that i could use @link to stop people from being lazy :P
        const own = handler.name;
        const rpcName = opts?.name || (isUsableIdentifier(own) ? own : "") || deriveName(path);
        if (!rpcName) throw new Error(`Could not derive an RPC name for '${method} ${path}'. Pass a named function or the \`name\` option.`);

        const paramNames = getParamNames(handler);
        const placeholders = [...path.matchAll(/\{([^}]+)\}/g)].map(m => m[1]);
        const missing = placeholders.filter(name => !paramNames.includes(name));
        if (missing.length) {
            throw new Error(
                `Route '${method} ${path}' declares path parameter(s) ${missing.map(m => `'${m}'`).join(", ")} ` +
                `that handler '${handler.name || "(anonymous)"}' does not accept. ` +
                (paramNames.length
                    ? `Its parameters were read as ${paramNames.map(r => `'${r}'`).join(", ")}. ` +
                      `If this build is minified, parameter names are not recoverable: give the handler ` +
                      `parameters matching the placeholders and do not minify the server.`
                    : `No parameter names could be read from it. Destructured, native, and bound functions ` +
                      `cannot receive mapped arguments; use ordinary named parameters.`),
            );
        }

        this.ephaptic.expose(rpcName, handler, {
            rateLimit: opts?.rateLimit,
            requiresLogin: opts?.requiresLogin,
        });

        this._routes.push({
            method,
            path,
            expressPath: toExpressPath(path),
            rpcName,
            handler,
            paramNames,
            rateLimit: opts?.rateLimit ? parseLimit(opts.rateLimit) : undefined,
            requiresLogin: opts?.requiresLogin ?? false,
        });

        return this;
    }

    /**
     * A framework-agnostic list of routes for wiring into Fastify/Hono/raw http.
     * Each `handle` takes a {@link GenericRequest} and resolves a {@link GenericResponse}.
     * 
     * This is because I was advised that many ~~JavaScript~~ TypeScript developers will defend their chosen HTTP server
     * like a newborn child; and thus it is better to allow them to keep it by supporting most of them at once.
     */
    routes(): { method: HttpMethod; path: string; handle: (req: GenericRequest) => Promise<GenericResponse> }[] {
        return this._routes.map((route) => ({
            method: route.method,
            path: route.path,
            handle: (req: GenericRequest) => this._handle(route, req),
        }));
    }

    /**
     * Like `routes`, but specifically mapping to an Express `Router`.
     * Mount it with `app.use(router.express())`.
     * 
     * Will, of course, error if you do not have `express` installed.
     */
    express(): any {
        const express = loadExpress();
        const r = express.Router();
        r.use(express.json());

        for (const route of this._routes) {
            r[route.method.toLowerCase()](route.expressPath, async (req: any, res: any) => {
                const response = await this._handle(route, {
                    method: route.method,
                    params: req.params,
                    query: req.query,
                    body: req.body,
                    headers: req.headers, // I am so very tempted to replace this with a `...req`
                    ip: this.ephaptic._clientIp(req),
                    raw: req,
                });

                for (const [key, value] of Object.entries(response.headers)) {
                    res.setHeader(key, value);
                }

                res.status(response.status);

                if (response.stream) {
                    res.setHeader("content-type", "application/jsonl");
                    let aborted = res.destroyed || res.writableEnded;
                    const onAbort = () => { aborted = true; };
                    req.on("close", onAbort);
                    res.on("close", onAbort);

                    const iterator = (response.stream as any)[Symbol.asyncIterator]?.()
                        ?? (response.stream as any);
                    try {
                        while (!aborted) {
                            const next = await iterator.next();
                            if (next.done) break;
                            if (res.destroyed || res.writableEnded) { aborted = true; break; }
                            // `undefined` would serialise to the literal text
                            // "undefined", which is not JSON.
                            const line = JSON.stringify(next.value === undefined ? null : next.value);
                            // matching FastAPI's HTTP generator handling
                            if (!res.write(line + "\n")) {
                                await new Promise<void>(resolve => {
                                    const done = () => { res.off("drain", done); res.off("close", done); resolve(); };
                                    res.once("drain", done);
                                    res.once("close", done);
                                });
                            }
                        }
                    } catch { /* response already started; we can't do anything now, so just continue to end it. */ }
                    finally {
                        req.off("close", onAbort);
                        res.off("close", onAbort);
                        // ask the generator run its `finally { ... }` block.
                        if (aborted) { try { await iterator.return?.(); } catch { /* already finished */ } }
                    }
                    res.end();
                } else {
                    let body: string;
                    try {
                        body = JSON.stringify(response.json ?? null);
                    } catch (err) {
                        const wire = await this.ephaptic.resolveError(err);
                        res.status(500);
                        body = JSON.stringify(wire);
                    }
                    res.setHeader("content-type", "application/json");
                    res.end(body ?? "null");
                }
            });
        }

        return r;
    }

    private _clientIp(req: GenericRequest): string | null {
        const header = this.ephaptic._ipHeaderName();
        if (header && req.headers) {
            const raw = (req.headers as Record<string, unknown>)[header.toLowerCase()];
            const value = Array.isArray(raw) ? raw[0] : raw;
            if (typeof value === "string" && value.length) return value.split(",")[0].trim();
        }
        return req.ip ?? null;
    }

    private async _handle(route: RouteDef, req: GenericRequest): Promise<GenericResponse> {
        try {
            const user = await this.ephaptic._loadHttpIdentity(req.raw ?? req);

            if (route.requiresLogin && user == null) {
                throw new ServiceError("Unauthorized.", { code: "UNAUTHORIZED", statusCode: 401 });
            }

            if (route.rateLimit) {
                // this function throws RateLimitExceeded (a ServiceError) on limit for us, we don't have to throw anything.
                // prefer the uid so an authenticated caller shares one bucket across RPC + HTTP; fall back to IP when anonymous.
                await checkRateLimit(this.ephaptic.manager, route.rpcName, route.rateLimit, {
                    uid: user != null ? this.ephaptic._identityKey(user) : null,
                    ip: this._clientIp(req),
                });
            }

            const args = mapArgs(route, req);

            const required = route.handler.length;
            const supplied = args.findIndex(v => v === undefined);
            if (required > 0 && supplied !== -1 && supplied < required) {
                throw new ServiceError(
                    `Missing required argument '${route.paramNames[supplied] ?? supplied}'.`,
                    { code: "VALIDATION_ERROR", statusCode: 422 },
                );
            }
            const ctx: HandlerContext = { user, scope: "http", emit: httpEmit };

            return await contextStorage.run(ctx, async (): Promise<GenericResponse> => {
                const result = await Promise.resolve(route.handler(...args));

                const tag = Object.prototype.toString.call(result);
                if (tag === "[object AsyncGenerator]" || tag === "[object Generator]") {
                    return {
                        status: 200,
                        headers: { "content-type": "application/jsonl" },
                        stream: bindToContext(ctx, result as AsyncIterable<unknown> | Iterable<unknown>),
                    };
                }

                return { status: 200, headers: {}, json: result ?? null };
            });
        } catch (err) {
            const wire: WireError = await this.ephaptic.resolveError(err);
            const status = err instanceof ServiceError ? err.statusCode : 500;
            const headers: Record<string, string> = {};
            if (
                err instanceof ServiceError &&
                err.data &&
                typeof err.data === "object" &&
                "retry_after" in (err.data as Record<string, unknown>)
            ) {
                headers["Retry-After"] = String((err.data as Record<string, unknown>).retry_after);
            }
            return { status, headers, json: wire };
        }
    }
}

// ## Helpers

/** `{param}` --> `:param`. */
function isUsableIdentifier(name: string | undefined): boolean {
    return !!name && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name);
}

function toExpressPath(path: string): string {
    return path.replace(/\{([^}]+)\}/g, (_, name) => ":" + name);
}

/**
 * derive an RPC name from a HTTP path (this will be called if no name is provided);
 * 
 * drop the leading slash, drop any `{param}`/`:param` sections, and join the rest with `_` (yes, snake case, I know)
 *  
 * e.g. `/users/{id}/posts` -> `users_posts`, `/sum` -> `sum`.
 */
function deriveName(path: string): string {
    return path
        .split("/")
        .map((s) => s.trim())
        .filter((s) => s && !/^\{.*\}$/.test(s) && !/^:/.test(s))
        .map((s) => s.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, ""))
        .filter(Boolean)
        .join("_");
}

function httpEmit() {
    throw new Error("You can't call `emit` from within a HTTP context."); // the infrastructure simply ISN'T in place!
    // and no don't chat to me about SSE 🫩
}

/**
 * map a HTTP request onto the handler's arguments, based on the conventions that we have already documented @ {@link Router} (FastAPI-style, and for good reason)
 */
function mapArgs(route: RouteDef, req: GenericRequest): unknown[] {
    const { params = {}, query = {}, body } = req;
    const hasBody = route.method === "POST" || route.method === "PUT" || route.method === "PATCH";

    const values: Record<string, unknown> = {};
    const restNames = route.paramNames.filter(name => !(name in params));

    for (const name of route.paramNames) if (name in params) values[name] = coerceScalar(params[name]);

    if (!hasBody) {
        for (const name of restNames) if (name in query) values[name] = coerceScalar((query as Record<string, unknown>)[name]);
    } else if (restNames.length === 1) {
        values[restNames[0]] = body;
    } else {
        for (const name of restNames) if (body && typeof body === "object" && name in (body as Record<string, unknown>)) values[name] = (body as Record<string, unknown>)[name];
    }
    // LGTM (looks garbage to me) but who cares

    return route.paramNames.map(name => values[name]);
}

function bindToContext(
    ctx: HandlerContext,
    source: AsyncIterable<unknown> | Iterable<unknown>,
): AsyncIterable<unknown> {
    const iterator = (source as any)[Symbol.asyncIterator]?.() ?? (source as any)[Symbol.iterator]();

    return {
        [Symbol.asyncIterator]() {
            return {
                next: () => contextStorage.run(ctx, () => Promise.resolve(iterator.next())),
                return: (value?: unknown) =>
                    contextStorage.run(ctx, () =>
                        Promise.resolve(iterator.return ? iterator.return(value) : { value: undefined, done: true }),
                    ),
            };
        },
    };
}

function loadExpress(): any {
    try   { return require("express"); }
    catch { throw new Error("The 'express' package is required for Router.express(). Install it with `npm install express`, or use router.routes() with another framework."); }
}