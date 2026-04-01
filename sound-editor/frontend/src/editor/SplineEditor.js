/**
 * src/editor/SplineEditor.js
 * ──────────────────────────
 * Orchestrates spline interaction: handles card clicks, updates config,
 * calls backend, and updates the 3D scene.
 */

import api from "../comms/ApiClient.js";

export class SplineEditor {
    /**
     * @param {import("../scene/ParameterSpace.js").ParameterSpace} space
     */
    constructor(space) {
        this._space    = space;
        this._layerId  = null;
        this._layer    = null;   // Layer registry entry
        this._config   = {
            stiffness:        1.0,
            bass_split:       52,
            bass_stiffness:   1.0,
            treble_stiffness: 1.0,
            degree:           3,
        };

        space.onCardClick     = c => this._onCardClick(c);
        space.onCardAltClick  = c => this._onCardAltClick(c);
    }

    // ── Layer activation ─────────────────────────────────────────────────────

    async activateLayer(layerId, layer) {
        this._layerId = layerId;
        this._layer   = layer;

        document.getElementById("active-layer-name").textContent =
            `${layer.label}  [${layerId}]`;

        // Load raw values into 3D space
        const values = await api.getLayerValues(layerId);
        this._space.loadLayer(values, layer);

        // Load existing spline state (control points)
        const state = await api.getSpline(layerId);
        this._applyStateToUI(state);

        // Refresh curve
        await this.fitAndRedraw();
    }

    // ── Config ───────────────────────────────────────────────────────────────

    readConfigFromUI() {
        this._config.stiffness   = parseFloat(document.getElementById("cfg-stiffness").value);
        this._config.degree      = parseInt(document.getElementById("cfg-degree").value);
        this._config.bass_split  = parseInt(document.getElementById("cfg-bass-split").value);
    }

    async applyConfig() {
        if (!this._layerId) return;
        this.readConfigFromUI();
        await api.updateConfig(this._layerId, this._config);
        setStatus("Config updated.");
    }

    // ── Fit ──────────────────────────────────────────────────────────────────

    async fitAndRedraw() {
        if (!this._layerId) return;
        setStatus("Fitting spline…");
        try {
            await api.fitSpline(this._layerId);
            const curve = await api.getSplineCurve(this._layerId, 300);
            this._space.updateSpline(
                0,  // vel — for now draw for vel 0; TODO per-vel
                curve.x, curve.y,
                this._layer?.color_hex ?? "#4af",
            );
            setStatus("Spline fitted.");
        } catch (err) {
            setStatus(`Fit error: ${err.message}`, true);
        }
    }

    // ── Card interaction ─────────────────────────────────────────────────────

    async _onCardClick(card) {
        // Pull: drag card value to current position (simple click = pull)
        if (!this._layerId) return;
        await api.pullSpline(this._layerId, card.midi, card.value, 3.0);
        await this.fitAndRedraw();
        this._refreshCPList();
    }

    async _onCardAltClick(card) {
        // Alt+click or right-click = toggle anchor
        if (!this._layerId) return;
        const state   = await api.getSpline(this._layerId);
        const already = state.control_points.find(p => p.midi === card.midi && p.is_anchor);
        if (already) {
            await api.removePoint(this._layerId, card.midi);
            this._space.setCardAnchor(_noteKey(card.midi, card.vel), false);
        } else {
            await api.addAnchor(this._layerId, card.midi, card.value, 8.0);
            this._space.setCardAnchor(_noteKey(card.midi, card.vel), true);
        }
        await this.fitAndRedraw();
        this._refreshCPList();
    }

    // ── Control point list ───────────────────────────────────────────────────

    async _refreshCPList() {
        if (!this._layerId) return;
        const state = await api.getSpline(this._layerId);
        this._applyStateToUI(state);
    }

    _applyStateToUI(state) {
        // Sync config sliders
        if (state.config) {
            const cfg = state.config;
            document.getElementById("cfg-stiffness").value = cfg.stiffness;
            document.getElementById("val-stiffness").textContent = cfg.stiffness.toFixed(2);
            document.getElementById("cfg-degree").value   = cfg.degree;
            document.getElementById("cfg-bass-split").value = cfg.bass_split;
        }

        // Render control point list
        const list = document.getElementById("cp-list");
        list.innerHTML = "";
        for (const cp of (state.control_points ?? [])) {
            const row = document.createElement("div");
            row.className = "cp-row" + (cp.is_anchor ? " anchor" : "");
            row.innerHTML =
                `<span>MIDI ${cp.midi}</span>` +
                `<span>${cp.value.toFixed(4)}</span>` +
                `<span>λ=${cp.stickiness.toFixed(1)}</span>` +
                `<button onclick="app.removeCP(${cp.midi})" ` +
                `style="width:auto;padding:1px 6px;font-size:10px">×</button>`;
            list.appendChild(row);
        }
    }

    async removeCP(midi) {
        if (!this._layerId) return;
        await api.removePoint(this._layerId, midi);
        await this.fitAndRedraw();
        this._refreshCPList();
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _noteKey(midi, vel) {
    return `m${String(midi).padStart(3,"0")}_vel${vel}`;
}

function setStatus(msg, isError = false) {
    const el = document.getElementById("status-msg");
    if (el) {
        el.textContent = msg;
        el.style.color = isError ? "#f66" : "#556";
    }
}
