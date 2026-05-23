import { contextBridge, ipcRenderer } from 'electron';

export function exposeEphaptic() {
    contextBridge.exposeInMainWorld('__ephaptic', {
        invoke: (name: string, ...args: any[]) => {
            return ipcRenderer.invoke(`ephaptic:${name}`, ...args);
        }
    });
}