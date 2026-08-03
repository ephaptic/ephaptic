/**
 * i only understand half of this, but it works, so don't touch it.
 * 
 * or you can touch it but MAKE SURE THE GODDAMN TESTS PASS afterwards
 */
export class AsyncQueue<T> implements AsyncIterableIterator<T> {
    private queue: T[] = [];
    private waiters: { resolve: (v: IteratorResult<T>) => void; reject: (e?: any) => void }[] = [];
    private isClosed = false;
    private error: any = null;
    onAbandon?: () => void;

    push(item: T) {
        if (this.isClosed) return;
        const waiter = this.waiters.shift();
        if (waiter) waiter.resolve({ value: item, done: false });
        else this.queue.push(item);
    }

    close() {
        if (this.isClosed) return;
        this.isClosed = true;
        while (this.waiters.length) {
            this.waiters.shift()!.resolve({ value: undefined, done: true });
        }
    }

    fail(reason: any) {
        if (this.error) return;
        this.error = reason;
        this.isClosed = true;
        if (this.queue.length === 0) {
            while (this.waiters.length) this.waiters.shift()!.reject(reason);
        }
    }

    next(): Promise<IteratorResult<T>> {
        if (this.queue.length > 0) {
            return Promise.resolve({ value: this.queue.shift()!, done: false });
        }
        if (this.error) return Promise.reject(this.error);
        if (this.isClosed) return Promise.resolve({ value: undefined, done: true });
        return new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
    }

    return(): Promise<IteratorResult<T>> {
        this.isClosed = true;
        this.queue.length = 0;
        while (this.waiters.length) {
            this.waiters.shift()!.resolve({ value: undefined, done: true });
        }
        this.onAbandon?.();
        return Promise.resolve({ value: undefined, done: true });
    }

    [Symbol.asyncIterator]() {
        return this;
    }
}