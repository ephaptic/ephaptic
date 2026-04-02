export class AsyncQueue<T> implements AsyncIterableIterator<T> {
    private queue: T[] = [];
    private resolvers: ((value: IteratorResult<T>) => void)[] = [];
    private isClosed = false;

    push(item: T) {
        if (this.resolvers.length > 0) {
            const resolve = this.resolvers.shift()!;
            resolve({ value: item, done: false });
        } else {
            this.queue.push(item);
        }
    }

    close() {
        this.isClosed = true;
        while (this.resolvers.length > 0) {
            const resolve = this.resolvers.shift()!;
            resolve({ value: undefined, done: true });
        }
    }

    next(): Promise<IteratorResult<T>> {
        if (this.queue.length > 0) {
            return Promise.resolve({ value: this.queue.shift()!, done: false });
        } if (this.isClosed) {
            return Promise.resolve({ value: undefined, done: true });
        } return new Promise(resolve => this.resolvers.push(resolve));
    }

    [Symbol.asyncIterator]() {
        return this;
    }
}