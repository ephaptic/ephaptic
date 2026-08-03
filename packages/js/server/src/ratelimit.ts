import { RatelimitExceededError } from "./errors.js";
import type { ConnectionManager } from "./connection-manager.js";

/** `[maxRequests, windowSeconds]`. */
export type Limit = [number, number];

const UNIT_SECONDS: Record<string, number> = {
    s: 1,
    m: 60,
    h: 3600,
    d: 86400,
};

/**
 * parse a rate-limit string like `"5/m"`, `"100/hour"`, or `"10 per 30s"` into a {@link Limit}
 */
export function parseLimit(limit: string): Limit {
    const normalised = limit.replace(/ per /g, "/");
    const parts = normalised.split("/");
    if (parts.length !== 2) throw new Error(`Invalid rate limit: ${limit}`);

    const count = parseInt(parts[0].trim(), 10);
    if (Number.isNaN(count)) throw new Error(`Invalid rate limit count: ${parts[0]}`);

    const match = /^(\d+)?\s*([smhd])/.exec(parts[1].trim().toLowerCase());
    if (!match) throw new Error(`Invalid rate limit period: ${parts[1]}`);

    const multiplier = match[1] ? parseInt(match[1], 10) : 1;
    const unit = UNIT_SECONDS[match[2]];

    return [count, multiplier * unit];
}

// ---- [INFO] Only used when Redis isn't configured ----------
const localCache = new Map<string, [number, number]>(); // [hits, expireAt]
let lastCacheCleanup = Date.now() / 1000;

/**
 * fixed-window rate limiter - uses Redis if configured else in-memory cache.
 * throws {@link RatelimitExceededError} when limit exceeded
 */
export async function checkRateLimit(
    manager: ConnectionManager,
    funcName: string,
    limit: Limit,
    identity: { uid?: string | null; ip?: string | null },
): Promise<void> {
    const [maxReqs, window] = limit;
    const identifier = identity.uid ? `u:${identity.uid}` : `ip:${identity.ip ?? "anon"}`;
    const now = Date.now() / 1000;
    const currentWindow = Math.floor(now / window);
    const reset = (currentWindow + 1) * window;
    const key = `ephaptic:rl:${funcName}:${identifier}:${currentWindow}`;

    let hits: number;

    const redis = manager.redis;
    if (redis) {
        const pipeline = redis.pipeline?.() ?? redis.multi?.();
        if (pipeline) {
            pipeline.incr(key);
            pipeline.expire(key, window + 1);
            const results = (await pipeline.exec()) as any;
            // ioredis: [[err, val], ...]; node-redis: [val, ...].
            const first = Array.isArray(results?.[0]) ? results[0][1] : results?.[0];
            hits = Number(first);
        } else if (redis.incr) {
            hits = await redis.incr(key);
            await redis.expire?.(key, window + 1);
        } else {
            hits = 1;
        }
    } else {
        if (now - lastCacheCleanup > 60) {
            for (const [k, v] of localCache) {
                if (v[1] < now) localCache.delete(k);
            }
            lastCacheCleanup = now;
        }

        let entry = localCache.get(key);
        if (!entry) {
            entry = [0, reset];
            localCache.set(key, entry);
        }
        entry[0] += 1;
        hits = entry[0];
    }

    if (hits > maxReqs) {
        const retryAfter = Math.max(1, Math.floor(reset - now));
        throw new RatelimitExceededError(
            `Rate limit exceeded. Try again in ${retryAfter} seconds.`,
            retryAfter,
        );
    }
}