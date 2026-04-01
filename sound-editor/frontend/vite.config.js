import { defineConfig } from "vite";

export default defineConfig({
    server: {
        port: 5173,
        proxy: {
            // Forward /api/* → backend during dev (optional, used if you prefix with /api)
        },
    },
    build: {
        outDir: "dist",
    },
});
