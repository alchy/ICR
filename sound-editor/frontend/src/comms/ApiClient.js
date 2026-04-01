/**
 * src/comms/ApiClient.js
 * ─────────────────────
 * REST client for the ICR Sound Editor backend (FastAPI).
 */

const BASE = "http://localhost:8000";

async function _req(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(BASE + path, opts);
    if (!res.ok) {
        const txt = await res.text();
        throw new Error(`${method} ${path} → ${res.status}: ${txt}`);
    }
    return res.json();
}

const get  = (path)        => _req("GET",    path);
const post = (path, body)  => _req("POST",   path, body);
const put  = (path, body)  => _req("PUT",    path, body);
const del  = (path)        => _req("DELETE", path);

export default {
    // ── Params ──────────────────────────────────────────────────────────────
    getParams:       ()           => get("/params"),
    loadSoundbank:   (path)       => post("/params/load", { path }),
    getNotes:        ()           => get("/params/notes"),

    // ── Layers ──────────────────────────────────────────────────────────────
    getLayers:       ()           => get("/layers"),
    getLayerValues:  (layerId)    => get(`/layers/${layerId}/values`),

    // ── Splines ─────────────────────────────────────────────────────────────
    getSpline:       (layerId)    => get(`/spline/${layerId}`),
    updateConfig:    (layerId, cfg) => put(`/spline/${layerId}/config`, cfg),
    fitSpline:       (layerId)    => post(`/spline/${layerId}/fit`),
    getSplineCurve:  (layerId, n) => post(`/spline/${layerId}/curve?n_points=${n ?? 300}`),
    addAnchor:       (layerId, midi, value, stickiness) =>
        post(`/spline/${layerId}/anchor`, { midi, value, stickiness, is_anchor: true }),
    pullSpline:      (layerId, midi, value, stickiness) =>
        post(`/spline/${layerId}/pull`, { midi, value, stickiness }),
    removePoint:     (layerId, midi) => del(`/spline/${layerId}/point/${midi}`),

    // ── Soundbank ────────────────────────────────────────────────────────────
    previewSoundbank: ()          => get("/soundbank/preview"),
    exportSoundbank:  (path)      => post("/soundbank/export", { path }),

    // ── MIDI / SysEx ─────────────────────────────────────────────────────────
    getMidiPorts:    ()           => get("/midi/ports"),
    connectMidi:     (portName)   => post("/midi/connect", { port_name: portName }),
    disconnectMidi:  ()           => del("/midi/disconnect"),
    midiStatus:      ()           => get("/midi/status"),
    sysexNote:       (midi, vel, paramKey, value) =>
        post("/sysex/note",    { midi, vel, param_key: paramKey, value }),
    sysexPartial:    (midi, vel, k, paramKey, value) =>
        post("/sysex/partial", { midi, vel, k, param_key: paramKey, value }),
    sysexBank:       ()           => post("/sysex/bank"),
    sysexPing:       ()           => post("/sysex/ping"),
};
