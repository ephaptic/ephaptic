/**
 * minimal transport abstraction to avoid dependence & coupling on a particular WS implementation
 * represents anything that can send a binary message + report a remote address.
 *
 * writes are serialised per transport (see {@link SendQueue}):
 * with each RPC handled concurrently, multiple coroutines (e.g. a stream and a regular reply)
 * may try to write to the same connection at once, and interleaved writes would corrupt the msgpack wire.
 */
export interface Transport {

    /** remote address, if known. */
    readonly remoteAddr?: string;

    /** send one binary payload. resolves as soon as the frame is flushed. */
    send(data: Uint8Array): Promise<void>;

    /** whether the underlying connection is [still] open. */
    readonly isOpen: boolean;
}

/**
 * serialize sends so that concurrently-produced frames (e.g. a reply racing a stream chunk) never interleave on the wire.
 * each `send` chains onto the previous one.
 */
export class SendQueue {
    private tail: Promise<void> = Promise.resolve();

    enqueue(write: () => Promise<void>): Promise<void> {
        const run = this.tail.then(write);
        this.tail = run.then(
            () => undefined,
            () => undefined,
        );
        return run;
    }
}