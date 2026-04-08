/**
 * src/main.js
 * ───────────
 * Entry point. Wires together ParameterSpace, SplineEditor, LayerBrowser,
 * and the UI panel controls.
 */

import api            from "./comms/ApiClient.js";
import { ParameterSpace } from "./scene/ParameterSpace.js";
import { SplineEditor }   from "./editor/SplineEditor.js";
import { LayerBrowser }   from "./editor/LayerBrowser.js";
import { VelSelector }    from "./editor/VelSelector.js";
import { NoteInspector }  from "./editor/NoteInspector.js";
import { NoteCompare }    from "./editor/NoteCompare.js";

// ── Boot ──────────────────────────────────────────────────────────────────────

const container   = document.getElementById("canvas-container");
const space       = new ParameterSpace(container);
const velBarEl    = document.getElementById("vel-bar-container");
const velSelector = new VelSelector(velBarEl, params => editor.onSelectorChange(params));
const browser     = new LayerBrowser(
    async (layerId, layer) => editor.activateLayer(layerId, layer),
    (layerId, on)          => editor.onToggleDots(layerId, on),
    (layerId, on)          => editor.onToggleSplines(layerId, on),
);
const editor      = new SplineEditor(space, browser, velSelector);
const inspector   = new NoteInspector(document.getElementById("note-inspector-container"));
const noteCompare = new NoteCompare(document.getElementById("note-compare-container"));

// Expose to inline HTML handlers
window.app = {
    refreshBanks,
    loadSelectedBank,
    setDimension: d => browser.setDimension(d),
    filterGroup:  g => browser.filterGroup(g),
    filterK:      k => browser.filterK(k),
    applySplineConfig: () => editor.applyConfig(),
    fitSpline:         () => editor.fitAndRedraw(),
    removeCP:          m => editor.removeCP(m),
    setAnchorsFromInput: () => editor.setAnchorsFromInput(),
    clearAnchors:        () => editor.clearAnchors(),
    connectMidi,
    sysexBank,
    exportSoundbank,
};

// ── Stiffness slider live label ───────────────────────────────────────────────

document.getElementById("cfg-stiffness").addEventListener("input", e => {
    document.getElementById("val-stiffness").textContent =
        parseFloat(e.target.value).toFixed(2);
});

// ── Soundbank discovery + loading ────────────────────────────────────────────

async function refreshBanks() {
    try {
        const { files, dir } = await api.listSoundbanks();
        const sel = document.getElementById("sel-bank");
        const prev = sel.value;
        sel.innerHTML = '<option value="">— select soundbank —</option>' +
            files.map(f => `<option value="${f}">${f}</option>`).join("");
        if (prev && files.includes(prev)) sel.value = prev;
        setStatus(`Banks dir: ${dir}  (${files.length} files)`);
    } catch (err) {
        setStatus(`Cannot reach backend: ${err.message}`, true);
    }
}

async function loadSelectedBank() {
    const filename = document.getElementById("sel-bank").value;
    if (!filename) return setStatus("Select a soundbank first.", true);
    try {
        setStatus(`Loading ${filename}…`);
        const r = await api.loadSoundbankByName(filename);
        setStatus(`Loaded ${filename}  (${r.loaded} notes)`);
        await browser.load(api);
        setStatus("Ready. Click a layer to edit.");
    } catch (err) {
        setStatus(`Load failed: ${err.message}`, true);
    }
}

// ── MIDI ──────────────────────────────────────────────────────────────────────

async function refreshMidiPorts() {
    try {
        const { ports } = await api.getMidiPorts();
        const sel = document.getElementById("sel-midi-port");
        sel.innerHTML = ports.map(p => `<option>${p}</option>`).join("") ||
                        '<option disabled>No ports found</option>';
    } catch (_) {}
}

async function connectMidi() {
    const port = document.getElementById("sel-midi-port").value;
    if (!port) return;
    try {
        await api.connectMidi(port);
        document.getElementById("midi-status").textContent = `Connected: ${port}`;
        document.getElementById("midi-status").style.color = "#4f4";
        setStatus(`MIDI connected: ${port}`);
    } catch (err) {
        setStatus(`MIDI error: ${err.message}`, true);
    }
}

async function sysexBank() {
    try {
        setStatus("Sending soundbank via SysEx…");
        const r = await api.sysexBank();
        setStatus(`Sent ${r.bytes} bytes via SysEx.`);
    } catch (err) {
        setStatus(`SysEx error: ${err.message}`, true);
    }
}

// ── Export ────────────────────────────────────────────────────────────────────

async function exportSoundbank() {
    const path = document.getElementById("export-path").value.trim();
    if (!path) return setStatus("Enter export path.", true);
    try {
        await api.exportSoundbank(path);
        setStatus(`Saved: ${path}`);
    } catch (err) {
        setStatus(`Export error: ${err.message}`, true);
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function setStatus(msg, isError = false) {
    const el = document.getElementById("status-msg");
    el.textContent = msg;
    el.style.color = isError ? "#f66" : "#556";
}

// ── Startup ───────────────────────────────────────────────────────────────────

refreshMidiPorts();
refreshBanks();
