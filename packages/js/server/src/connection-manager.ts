import { encode, decode } from "@msgpack/msgpack";
import type { Transport } from "./transport.js";

export const CHANNEL_NAME = "ephaptic:broadcast";

/**
 * The event frame which would be sent to a client.
 * It does not carry the target user list to prevent information leakage.
 */
interface EventFrame {
    type: "event";
    name: string;
    payload: { args: unknown[]; kwargs: Record<string, unknown> };
}

/**
 * The basic Redis functions we need exposed on any Redis client. `ioredis` + node:`redis` expose these
 * so we can lazy-import either of them (depending on whichever is installed).
 */
interface RedisLike {
    publish(channel: string, message: Uint8Array | string): Promise<unknown>;
    duplicate?: () => RedisLike;
    subscribe?: (channel: string, ...args: unknown[]) => unknown;
    on?: (event: string, cb: (...args: unknown[]) => void) => void;
    incr?: (key: string) => Promise<number>;
    expire?: (key: string, seconds: number) => Promise<unknown>;
    pipeline?: () => { incr: (k: string) => unknown; expire: (k: string, s: number) => unknown; exec: () => Promise<unknown> };
    multi?: () => { incr: (k: string) => unknown; expire: (k: string, s: number) => unknown; exec: () => Promise<unknown> };
}

/**
 * `userId -> Set<Transport>` so that events are sent to every socket that a user owns.
 */
export class ConnectionManager {
    private active = new Map<string, Set<Transport>>();
    redis: RedisLike | null = null;
    private subscriber: RedisLike | null = null;

    private readonly originId: string = `${process.pid}-${Math.random().toString(36).slice(2)}`;

    /**
     * lazily import a Redis client (`ioredis` preferred, then `redis`) and connect
     * kept off the module's static imports so Redis stays optional.
     */
    async initRedis(url: string): Promise<void> {
        const client = await createRedisClient(url);
        await this.startRedis(client);
        this.redis = client;
    }

    add(userId: string, transport: Transport): void {
        let set = this.active.get(userId);
        if (!set) {
            set = new Set();
            this.active.set(userId, set);
        }
        set.add(transport);
    }

    remove(userId: string, transport: Transport): void {
        const set = this.active.get(userId);
        if (!set) return;
        set.delete(transport);
        if (set.size === 0) this.active.delete(userId);
    }

    async broadcast(
        userIds: string[],
        eventName: string,
        args: unknown[],
        kwargs: Record<string, unknown>,
    ): Promise<void> {
        // do not pass in the target user list to prevent information leakage
        const frame: EventFrame = {
            type: "event",
            name: eventName,
            payload: { args, kwargs },
        };
        const message = encode(frame);

        this.sendTo(userIds, message);

        if (this.redis) {
            // wrap the client message in an envelope for routing
            // the envelope will only be in the server(s), never reaching a client, so that target users remain hidden
            const envelope = encode({ target_users: userIds, message, origin: this.originId });
            try {
                await this.redis.publish(CHANNEL_NAME, Buffer.from(envelope));
            } catch (err) {
                console.error("[ephaptic] failed to publish a broadcast:", err);
            }
        }
    }

    private sendTo(userIds: string[], message: Uint8Array): void {
        for (const userId of userIds) {
            const set = this.active.get(userId);
            if (!set) continue;
            for (const transport of Array.from(set)) {
                // fire and ~~forget~~ move on to the next. do not allow a slow/dead socket to introduce latency
                transport.send(message).catch(() => {});
            }
        }
    }

    private async startRedis(client: RedisLike): Promise<void> {
        if (typeof client.duplicate !== "function") return;
        const sub = client.duplicate();
        this.subscriber = sub;

        const onMessage = (raw: unknown) => {
            try {
                const bytes =
                    raw instanceof Uint8Array ? raw :
                    typeof raw === "string" ? Buffer.from(raw, "binary") :
                    null;
                if (!bytes) return;
                const envelope = decode(bytes) as { target_users?: string[]; message: Uint8Array; origin?: string };
                if (envelope.origin === this.originId) return; // already delivered locally
                this.sendTo(envelope.target_users ?? [], envelope.message);
            } catch {
                console.warn("[ephaptic] discarding a malformed broadcast envelope");
            }
        };

        try {
            if (typeof (sub as any).connect === "function" && (sub as any).isOpen === false) {
                await (sub as any).connect();
            }
        } catch { /* already connected */ }

        if (typeof sub.on !== "function" || typeof (sub as any).subscribe !== "function") return;

        const nodeRedis = typeof (sub as any).sSubscribe === "function"
            || typeof (sub as any).pSubscribe === "function"
            || "isOpen" in (sub as any);

        if (nodeRedis) {
            // node-redis: subscribe(channel, listener, bufferMode)
            await (sub as any).subscribe(CHANNEL_NAME, (message: unknown) => onMessage(message), true);
        } else {
            // ioredis: binary payloads arrive on `messageBuffer`
            // no need to listen `.on("message")` becauase that would just make it listen twice
            sub.on("messageBuffer", (_channel: unknown, message: unknown) => onMessage(message));
            await Promise.resolve((sub as any).subscribe(CHANNEL_NAME));
        }
    }
}

// exists so TypeScript doesn't try to statically resolve these optional
// packages at build time (they are, after all, optional, and therefore not always installed on the build machine)
const dynamicImport = (name: string): Promise<any> =>
    import(/* @vite-ignore */ name);

async function createRedisClient(url: string): Promise<RedisLike> {
    try {
        const mod: any = await dynamicImport("ioredis");
        const IORedis = mod.default ?? mod.Redis ?? mod;
        return new IORedis(url) as RedisLike;
    } catch { /* try the node `redis` module */ }

    try {
        const mod: any = await dynamicImport("redis");
        const client = mod.createClient({ url });
        await client.connect();
        return client as RedisLike;
    } catch {
        throw new Error("Redis support requires 'ioredis' or 'redis' to be installed.");
    }
}