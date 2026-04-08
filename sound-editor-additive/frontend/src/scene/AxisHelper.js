/**
 * src/scene/AxisHelper.js
 * ───────────────────────
 * Draws axes, grid and text labels in the 3D parameter space.
 *
 *   X → MIDI note  (21–108),  world −5 … +5
 *   Y → value      (normalised), world 0 … 8
 *   Z → velocity   (0–7),     world 0 … 8.4
 */

import * as THREE from "three";
import { xFromMidi } from "./ParameterSpace.js";

// ── Text sprite helper ────────────────────────────────────────────────────────

function makeLabel(text, { fontSize = 22, color = "#aabbcc", bg = null } = {}) {
    const canvas  = document.createElement("canvas");
    const ctx     = canvas.getContext("2d");
    ctx.font      = `${fontSize}px monospace`;
    const w       = Math.ceil(ctx.measureText(text).width) + 12;
    canvas.width  = w;
    canvas.height = fontSize + 8;
    ctx.font      = `${fontSize}px monospace`;
    if (bg) { ctx.fillStyle = bg; ctx.fillRect(0, 0, canvas.width, canvas.height); }
    ctx.fillStyle = color;
    ctx.fillText(text, 6, fontSize);

    const tex  = new THREE.CanvasTexture(canvas);
    const mat  = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
    const spr  = new THREE.Sprite(mat);
    const scale = 0.012;
    spr.scale.set(canvas.width * scale, canvas.height * scale, 1);
    spr.renderOrder = 999;
    return spr;
}

// ── Line helper ───────────────────────────────────────────────────────────────

function line(p1, p2, color) {
    const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...p1),
        new THREE.Vector3(...p2),
    ]);
    return new THREE.Line(geo, new THREE.LineBasicMaterial({ color }));
}

function dashedLine(p1, p2, color, dash = 0.2, gap = 0.15) {
    const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...p1),
        new THREE.Vector3(...p2),
    ]);
    const mat = new THREE.LineDashedMaterial({ color, dashSize: dash, gapSize: gap });
    const l   = new THREE.Line(geo, mat);
    l.computeLineDistances();
    return l;
}

// ── AxisHelper ────────────────────────────────────────────────────────────────

export class AxisHelper {
    /**
     * @param {THREE.Scene} scene
     * @param {Function}    yFromVal   (value) → world Y  (provided by ParameterSpace)
     * @param {Object}      layer      current Layer (min_val, max_val, label)
     */
    constructor(scene) {
        this._scene   = scene;
        this._objects = [];
    }

    /**
     * Build/rebuild all axis geometry for the given layer.
     */
    build(yFromVal, layer) {
        this.dispose();

        const minVal = layer?.min_val ?? 0;
        const maxVal = layer?.max_val ?? 1;
        const yLabel = layer?.label   ?? "value";

        this._buildFloorGrid();
        this._buildMidiAxis();
        this._buildVelAxis();
        this._buildValueAxis(yFromVal, minVal, maxVal, yLabel);
    }

    dispose() {
        for (const obj of this._objects) {
            this._scene.remove(obj);
            obj.geometry?.dispose();
            obj.material?.dispose?.();
        }
        this._objects = [];
    }

    _add(obj) { this._scene.add(obj); this._objects.push(obj); }

    // ── Floor grid ────────────────────────────────────────────────────────────

    _buildFloorGrid() {
        const Y = -0.08;

        // Horizontal lines at each velocity Z
        for (let vel = 0; vel <= 7; vel++) {
            const z = vel * 1.2;
            this._add(dashedLine([-5.2, Y, z], [5.2, Y, z], 0x1a3a4a));
        }

        // Vertical lines at C notes
        for (const midi of [24, 36, 48, 60, 72, 84, 96]) {
            const x = xFromMidi(midi);
            this._add(dashedLine([x, Y, -0.3], [x, Y, 8.7], 0x1a3a4a));
        }
    }

    // ── MIDI (X) axis ─────────────────────────────────────────────────────────

    _buildMidiAxis() {
        const Y  = -0.08;
        const Z  = 9.2;   // in front of all velocity layers

        // Main axis line
        this._add(line([-5.3, Y, Z], [5.3, Y, Z], 0x2a6a8a));

        // Label "MIDI"
        const lbl = makeLabel("MIDI", { color: "#4af", fontSize: 20 });
        lbl.position.set(5.7, Y + 0.1, Z);
        this._add(lbl);

        // Tick marks + labels at C notes
        const noteNames = { 24:"C1", 36:"C2", 48:"C3", 60:"C4", 72:"C5", 84:"C6", 96:"C7", 108:"C8" };
        for (const [midiStr, name] of Object.entries(noteNames)) {
            const midi = parseInt(midiStr);
            const x    = xFromMidi(midi);

            // Tick
            this._add(line([x, Y - 0.08, Z], [x, Y + 0.08, Z], 0x3a8aaa));

            // Vertical guide line up
            this._add(dashedLine([x, Y, Z], [x, 0.0, Z - 0.01], 0x112233, 0.1, 0.2));

            // Label below axis
            const sp = makeLabel(name, { color: "#88bbcc", fontSize: 18 });
            sp.position.set(x, Y - 0.25, Z);
            this._add(sp);
        }
    }

    // ── Velocity (Z) axis ─────────────────────────────────────────────────────

    _buildVelAxis() {
        const Y = -0.08;
        const X = -5.5;

        // Main axis line
        this._add(line([X, Y, -0.3], [X, Y, 8.7], 0x2a5a4a));

        // Label
        const lbl = makeLabel("vel", { color: "#4fa", fontSize: 20 });
        lbl.position.set(X - 0.05, Y + 0.1, 9.0);
        this._add(lbl);

        // Ticks + labels
        for (let vel = 0; vel <= 7; vel++) {
            const z = vel * 1.2;
            this._add(line([X - 0.08, Y, z], [X + 0.08, Y, z], 0x3aaa6a));

            const sp = makeLabel(`${vel}`, { color: "#77ccaa", fontSize: 18 });
            sp.position.set(X - 0.28, Y + 0.05, z);
            this._add(sp);
        }
    }

    // ── Value (Y) axis ────────────────────────────────────────────────────────

    _buildValueAxis(yFromVal, minVal, maxVal, yLabel) {
        const X = -5.5;
        const Z = -0.3;

        const yBot = yFromVal(minVal);
        const yTop = yFromVal(maxVal);

        // Main axis line
        this._add(line([X, yBot - 0.1, Z], [X, yTop + 0.3, Z], 0x6a4a8a));

        // Label
        const lbl = makeLabel(yLabel, { color: "#c8f", fontSize: 18 });
        lbl.position.set(X - 0.1, yTop + 0.5, Z);
        this._add(lbl);

        // 5 evenly spaced ticks
        const steps = 4;
        for (let i = 0; i <= steps; i++) {
            const t    = i / steps;
            const val  = minVal + t * (maxVal - minVal);
            const y    = yFromVal(val);
            const text = _formatVal(val);

            this._add(line([X - 0.08, y, Z], [X + 0.08, y, Z], 0xaa77cc));

            // Horizontal guide plane line (across MIDI axis)
            this._add(dashedLine([X, y, Z], [5.3, y, Z], 0x221133, 0.15, 0.25));

            const sp = makeLabel(text, { color: "#aa88dd", fontSize: 17 });
            sp.position.set(X - 0.5, y, Z);
            this._add(sp);
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _formatVal(v) {
    if (Math.abs(v) >= 1000) return v.toFixed(0);
    if (Math.abs(v) >= 10)   return v.toFixed(1);
    if (Math.abs(v) >= 0.1)  return v.toFixed(3);
    return v.toExponential(1);
}
