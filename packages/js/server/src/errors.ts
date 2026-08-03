/**
 * These are error codes ephaptic reserves for itself.
 * You probably shouldn't use these codes to avoid confusion.
 */
export const RESERVED_CODES = new Set<string>([
    "INTERNAL",
    "VALIDATION_ERROR",
    "RETURN_VALIDATION_ERROR",
    "RATELIMIT",
    "NOT_FOUND",
]);

/** the `{ code, message, data }` objects representing errors */
export interface WireError {
    code: string;
    message: string;
    data: unknown;
}

/**
 * Base class for typed, structured errors.
 *
 * You may subclass it to define your own application errors, overriding the static defaults:
 *
 *     class NotFound extends ServiceError {
 *         static code = "NOT_FOUND";
 *         static message = "The requested resource was not found.";
 *         static statusCode = 404;
 *     }
 *
 * You can then throw the error either bare (`new NotFound()`) or with overrides (e.g. `new NotFound("No such object.", { data: { id } })`)
 */
export class ServiceError extends Error {
    // the default settings for a ServiceError, you can override these in your subclasses
    // ^ why am i saying "you" its a normal comment not a docstring comment
    static code = "ERROR";
    static message = "An error occurred.";
    static statusCode = 400;

    code: string;
    data: unknown;
    statusCode: number;

    constructor(
        message?: string,
        options: { code?: string; data?: unknown; statusCode?: number } = {},
    ) {
        const cls = new.target as typeof ServiceError;
        super(message !== undefined ? message : cls.message);
        this.name = cls.name;
        this.code = options.code ?? cls.code;
        this.message = message !== undefined ? message : cls.message;
        this.statusCode = options.statusCode ?? cls.statusCode;
        this.data = options.data ?? null;

        Object.setPrototypeOf(this, new.target.prototype);
    }

    /** call this to serialize me into a {@link WireError}. (that can be sent across the wire!!) */
    toWire(): WireError {
        return { code: this.code, message: this.message, data: this.data ?? null };
    }
}

/**
 * INTERNAL ERROR: RateLimitExceeded. This is called by the library when the rate limit set is exceeded; no need to raise it yourself ;)
 */
export class RatelimitExceededError extends ServiceError {
    static code = "RATELIMIT";
    static message = "Rate limit exceeded.";
    static statusCode = 429;

    retryAfter: number;

    constructor(message: string, retryAfter: number) {
        super(message, { data: { retry_after: retryAfter } });
        this.retryAfter = retryAfter;
    }
}