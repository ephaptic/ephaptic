import { ipcMain } from "electron";
import type { Routes } from "../ephaptic.js";

export function exposeIPC<T extends Routes>(routes: T): T {
    for (const [name, fn] of Object.entries(routes)) {
        ipcMain.handle(`ephaptic:${name}`, async (event, ...args) => {
            return await fn(...args);
        });
    }

    return routes;
}