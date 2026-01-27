/// <reference types="vitest" />
import { defineConfig } from 'vite';
import { resolve } from 'path';
import dts from 'vite-plugin-dts';
import { configDefaults } from 'vitest/config';

export default defineConfig({
    build: {
        lib: {
            entry: resolve(__dirname, 'src/index.ts'),
            name: 'ephaptic',
            fileName: 'ephaptic',
            formats: ['es', 'cjs', 'umd'],
        },

        sourcemap: true,
    },
    plugins: [
        dts({
            insertTypesEntry: true,
        }),
    ],
});