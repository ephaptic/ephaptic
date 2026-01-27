import { describe, it, expect, vi } from 'vitest';
import { EphapticClientBase } from '../';

vi.stubGlobal('WebSocket', vi.fn(() => ({
    send: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    readyState: WebSocket.OPEN,
    onopen: null,
    onmessage: null,
    onclose: null,
})));

vi.stubGlobal('window', {
    location: {
        protocol: 'http:',
        host: 'localhost',
    },
});

describe('EphapticClientBase', () => {
    it('should be defined', () => {
        expect(EphapticClientBase).toBeDefined();
    });

    describe('_getUrl', () => {
        it('should return default URL if no url is provided', () => {
            const client = new EphapticClientBase();
            expect(client._getUrl()).toBe('ws://localhost/_ephaptic');
        });

        it('should handle relative paths', () => {
            const client = new EphapticClientBase({ url: '/custom_path' });
            expect(client._getUrl()).toBe('ws://localhost/custom_path');
        });

        it('should handle absolute ws urls', () => {
            const client = new EphapticClientBase({ url: 'ws://example.com/test' });
            expect(client._getUrl()).toBe('ws://example.com/test');
        });

        it('should convert http urls to ws urls', () => {
            const client = new EphapticClientBase({ url: 'http://example.com/test' });
            expect(client._getUrl()).toBe('ws://example.com/test');
        });

        it('should convert https urls to wss urls', () => {
            const client = new EphapticClientBase({ url: 'https://example.com/test' });
            expect(client._getUrl()).toBe('wss://example.com/test');
        });
    });

    describe('event emitter', () => {
        it('should register and trigger an event', () => {
            const client = new EphapticClientBase();
            const callback = vi.fn();
            client.on('my_event', callback);
            client._emit('my_event', ['arg1', 'arg2'], { kwarg1: 'value1' });
            expect(callback).toHaveBeenCalledWith('arg1', 'arg2', { kwarg1: 'value1' });
        });

        it('should unregister an event', () => {
            const client = new EphapticClientBase();
            const callback = vi.fn();
            client.on('my_event', callback);
            client.off('my_event', callback);
            client._emit('my_event', ['arg1']);
            expect(callback).not.toHaveBeenCalled();
        });

        it('should register a once event', () => {
            const client = new EphapticClientBase();
            const callback = vi.fn();
            client.once('my_event', callback);
            client._emit('my_event', ['arg1']);
            client._emit('my_event', ['arg1']);
            expect(callback).toHaveBeenCalledTimes(1);
        });
    });

    describe('constructor', () => {
        it('should call connect if in a window context', () => {
            const connectSpy = vi.spyOn(EphapticClientBase.prototype, 'connect');
            new EphapticClientBase();
            expect(connectSpy).toHaveBeenCalled();
            connectSpy.mockRestore();
        });
    });
});
