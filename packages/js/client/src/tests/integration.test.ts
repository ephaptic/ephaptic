import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { connect } from '../';

const SERVER_URL = `ws://127.0.0.1:${process.env.TEST_PORT || 8000}/_ephaptic`;

describe('ephaptic (connected to actual server)', () => {
    let client: any;

    beforeEach(async () => {
        client = connect({ url: SERVER_URL, auth: "user123" });

        await new Promise<void>((resolve, reject) => {
            const connectTimeout = setTimeout(() => {
                reject(new Error("Client failed to connect within 10 seconds. Is the Python server running?"));
            }, 10000);

            client.addEventListener('connected', () => {
                clearTimeout(connectTimeout);
                resolve();
            }, { once: true });

            client.addEventListener('disconnected', (event: CustomEvent) => {
                clearTimeout(connectTimeout);
                reject(new Error(`Client disconnected before connecting: ${event.detail?.reason}`));
            }, { once: true });
        });
    });

    afterEach(() => {
        if (client.ws && client.ws.readyState === WebSocket.OPEN) {
            client.ws.close();
        }
    });

    it('should be able to make an RPC call and receive a response', async () => {
        const result = await client.echo("hi");
        expect(result).toBe("hi");
    });

    it('should handle RPC call errors', async () => {
        await expect(client.add("a", "b")).rejects.toThrow();
    });

    it('should receive broadcast events from the server', async () => {
        const eventPromise = new Promise<any>(resolve => {
            client.on('MyEvent', (payload: { message: string }) => {
                resolve(payload.message);
            });
        });

        await client.emit_event("Integration test event");

        const receivedMessage = await eventPromise;
        expect(receivedMessage).toBe("Integration test event");
    });

    it('should receive typed broadcast events from the server', async () => {
        const eventPromise = new Promise<any>(resolve => {
            client.on('MyTypedEvent', (event: any) => {
                resolve(event);
            });
        });

        await client.emit_typed_event(42);

        const receivedEvent = await eventPromise;
        expect(receivedEvent).toEqual({ value: 42 });
    });

    it('should handle rate limiting errors', async () => {
        console.log('1');
        await client.spam_me();

        console.log('2');
        await expect(client.spam_me()).rejects.toThrow(/Rate Limit exceeded/);
    });

    it('should access routes via RPC', async () => {
        const result = await client.r_echo("js-test");
        expect(result.is_rpc).toBe(true);
        expect(result.is_http).toBe(false);
        expect(result.active_user).toBe('user123');
        expect(result.message).toBe('js-test')
    }, 15000);
});