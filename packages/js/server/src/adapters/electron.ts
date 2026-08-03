import { ipcMain } from "electron";
import type { Routes } from "../ephaptic.js";
import { Ephaptic } from "../ephaptic.js";

/**
 * Expose your handlers over Electron's IPC.
 * 
 * You can pass an {@link Ephaptic} instance if you want to have thrown errors
 * resolved like they are over WS; a typed error would keep its `code`/`message`/`data`,
 * while uncaught exceptions become either a generic `INTERNAL` or contain error information
 * if debug mode is enabled.
 * 
 * Without an instance passed, errors are masked but no exception handlers are
 * used.
 */
export function exposeIPC<T extends Routes>(routes: T, ephaptic?: Ephaptic): T {
    const resolver = ephaptic ?? new Ephaptic();

    for (const [name, fn] of Object.entries(routes)) {
        ipcMain.handle(`ephaptic:${name}`, async (_event, ...args) => {
            try {
                return await fn(...args);
            } catch (err) {
                const wire = await resolver.resolveError(err);
                const wrapped = new Error(wire.message);
                (wrapped as Error & { code?: string; data?: unknown }).code = wire.code;
                (wrapped as Error & { code?: string; data?: unknown }).data = wire.data;
                throw wrapped;
            }
        });
    }

    return routes;
}