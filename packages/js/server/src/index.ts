export {
    Ephaptic,
    EphapticTarget,
    type EphapticOptions,
    type Routes,
    type ExposeOptions,
    type ExceptionMatcher,
    type ExceptionHandlerFn,
    type IdentityLoaderFn,
    type HttpIdentityLoaderFn,
    type AttachOptions,
} from "./ephaptic.js";

export {
    Router,
    type RouteOptions,
    type GenericRequest,
    type GenericResponse,
} from "./router.js";

export {
    ServiceError,
    RatelimitExceededError,
    RESERVED_CODES,
    type WireError,
} from "./errors.js";

export { activeUser, emit, isHttp, isRpc, type HandlerContext } from "./context.js";

export { ConnectionManager } from "./connection-manager.js";
export { parseLimit, type Limit } from "./ratelimit.js";
export { type Transport } from "./transport.js";

import { Ephaptic, type AttachOptions } from "./ephaptic.js";

/**
 * (convenience) mount an existing {@link Ephaptic} instance's WebSocket transport onto a node:http server
 * equivalent to `ephaptic.attach(server, opts)`.
 */
export function attach(
    ephaptic: Ephaptic,
    server: unknown,
    opts: AttachOptions = {},
): unknown {
    return ephaptic.attach(server, opts);
}
