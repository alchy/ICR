/**
 * src/editor/SplineEditor.js
 * ──────────────────────────
 * Orchestrates multi-velocity spline interaction.
 * Tracks per-layer state (kept, visible) and syncs badges in LayerBrowser.
 */

import api from "../comms/ApiClient.js";
import { VEL_COLORS } from "./VelSelector.js";

export class SplineEditor {
    /**
     * @param {import("../scene/ParameterSpace.js").ParameterSpace} space
     * @param {import("./LayerBrowser.js").LayerBrowser}            browser
     * @param {import("./VelSelector.js").VelSelector}              velSelector
     */
    constructor(space, browser, velSelector) {
        this._space       = space;
        this._browser     = browser;
        this._velSelector = velSelector;
        this._layerId     = null;
        this._layer       = null;
        this._selected    = new Set([0, 1, 2, 3, 4, 5, 6, 7]);
        this._coherence   = 0.0;
        this._stickiness  = 3.0;
        this._config      = {
            stiffness: 1.0, bass_split: 52,
            bass_stiffness: 1.0, treble_stiffness: 1.0, degree: 3,
        };
        // Per-layer state: layerId → { kept: bool, visible: bool }
        this._states = new Map();

        space.onCardClick     = c => this._onCardClick(c);
        space.onCardAltClick  = c => this._onCardAltClick(c);
    }

    // ── Per-layer state ───────────────────────────────────────────────────────

    _getState(layerId) {
        if (!this._states.has(layerId))
            this._states.set(layerId, { kept: false, visible: true });
        return this._states.get(layerId);
    }

    _setState(layerId, patch) {
        const s = this._getState(layerId);
        Object.assign(s, patch);
        this._browser.setLayerState(layerId, s);
    }

    // ── VelSelector callback ──────────────────────────────────────────────────

    onSelectorChange({ selected, coherence, stickiness, keepToggled, applyPressed }) {
        const prevKept   = this._getState(this._layerId ?? "").kept;
        const keepChanged = keepToggled !== prevKept;

        this._selected   = selected;
        this._coherence  = coherence;
        this._stickiness = stickiness;

        if (!this._layerId) return;

        if (applyPressed) {
            this._doApply();
        } else if (keepChanged) {
            keepToggled ? this._doKeep() : this._doUnkeep();
        } else {
            this.fitAndRedraw();
        }
    }

    // ── Layer activation ─────────────────────────────────────────────────────

    async activateLayer(layerId, layer) {
        this._layerId = layerId;
        this._layer   = layer;

        document.getElementById("active-layer-name").textContent =
            `${layer.label}  [${layerId}]`;

        // Restore Keep button to match this layer's kept state
        const state = this._getState(layerId);
        this._velSelector.setKept(state.kept);

        const values = await api.getLayerValues(layerId);
        this._space.loadLayer(values, layer);

        // Restore ghost dots if this layer was kept
        if (state.kept) {
            const vels   = [...this._selected];
            const result = await api.fitAllVelocities(layerId, vels, this._coherence);
            const velFitted = {};
            for (const [v, d] of Object.entries(result)) velFitted[v] = d.fitted;
            this._space.applyKeep(velFitted);
        }

        const splineState = await api.getSpline(layerId);
        this._applyStateToUI(splineState);

        await this.fitAndRedraw();
    }

    // ── Config ───────────────────────────────────────────────────────────────

    readConfigFromUI() {
        this._config.stiffness  = parseFloat(document.getElementById("cfg-stiffness").value);
        this._config.degree     = parseInt(document.getElementById("cfg-degree").value);
        this._config.bass_split = parseInt(document.getElementById("cfg-bass-split").value);
    }

    async applyConfig() {
        if (!this._layerId) return;
        this.readConfigFromUI();
        for (const vel of this._selected)
            await api.updateConfig(`${this._layerId}__vel${vel}`, this._config);
        setStatus("Config updated.");
    }

    // ── Fit ──────────────────────────────────────────────────────────────────

    async fitAndRedraw() {
        if (!this._layerId) return;
        setStatus("Fitting…");
        try {
            const vels   = [...this._selected];
            const result = await api.fitAllVelocities(
                this._layerId, vels, this._coherence,
            );
            this._space.clearSplines();
            for (const [velStr, data] of Object.entries(result)) {
                const vel = parseInt(velStr);
                this._space.updateSpline(
                    vel, data.curve.x, data.curve.y,
                    VEL_COLORS[vel % VEL_COLORS.length],
                );
            }
            setStatus("Fitted.");
        } catch (err) {
            setStatus(`Fit error: ${err.message}`, true);
        }
    }

    // ── Keep / Unkeep / Apply ─────────────────────────────────────────────────

    async _doKeep() {
        if (!this._layerId) return;
        setStatus("Keeping…");
        try {
            await api.keepLayer(this._layerId, [...this._selected], this._coherence);

            const result = await api.fitAllVelocities(
                this._layerId, [...this._selected], this._coherence,
            );
            const velFitted = {};
            for (const [v, d] of Object.entries(result)) velFitted[v] = d.fitted;
            this._space.applyKeep(velFitted);

            this._space.clearSplines();
            for (const [velStr, data] of Object.entries(result)) {
                const vel = parseInt(velStr);
                this._space.updateSpline(
                    vel, data.curve.x, data.curve.y,
                    VEL_COLORS[vel % VEL_COLORS.length],
                );
            }

            this._setState(this._layerId, { kept: true });
            setStatus("Kept ✓");
        } catch (err) {
            setStatus(`Keep error: ${err.message}`, true);
        }
    }

    async _doUnkeep() {
        if (!this._layerId) return;
        await api.unkeepLayer(this._layerId, [...this._selected]);
        this._space.clearKeep();
        this._setState(this._layerId, { kept: false });
        setStatus("Keep removed.");
        await this.fitAndRedraw();
    }

    async _doApply() {
        if (!this._layerId) return;
        setStatus("Applying…");
        try {
            await api.applyLayer(this._layerId, [...this._selected], this._coherence);

            this._space.clearKeep();
            this._setState(this._layerId, { kept: false });
            this._velSelector.setKept(false);

            const values = await api.getLayerValues(this._layerId);
            this._space.loadLayer(values, this._layer);

            await this.fitAndRedraw();
            setStatus("Applied ✓");
        } catch (err) {
            setStatus(`Apply error: ${err.message}`, true);
        }
    }

    // ── Visible toggle (from LayerBrowser badge) ──────────────────────────────

    onToggleVisible(layerId, visible) {
        this._setState(layerId, { visible });
        // If it's the active layer, hide/show splines
        if (layerId === this._layerId) {
            this._space.setSplineVisibility(visible);
        }
    }

    // ── Card interaction ──────────────────────────────────────────────────────

    async _onCardClick(card) {
        if (!this._layerId) return;
        for (const vel of this._selected)
            await api.pullSpline(`${this._layerId}__vel${vel}`,
                card.midi, card.value, this._stickiness);
        await this.fitAndRedraw();
        this._refreshCPList();
    }

    async _onCardAltClick(card) {
        if (!this._layerId) return;
        for (const vel of this._selected) {
            const sid   = `${this._layerId}__vel${vel}`;
            const state = await api.getSpline(sid);
            const already = state.control_points.find(
                p => p.midi === card.midi && p.is_anchor
            );
            if (already) await api.removePoint(sid, card.midi);
            else         await api.addAnchor(sid, card.midi, card.value, 8.0);
        }
        await this.fitAndRedraw();
        this._refreshCPList();
    }

    // ── Control point list ────────────────────────────────────────────────────

    async _refreshCPList() {
        if (!this._layerId || this._selected.size !== 1) return;
        const [vel] = [...this._selected];
        const state = await api.getSpline(`${this._layerId}__vel${vel}`);
        this._applyStateToUI(state);
    }

    _applyStateToUI(state) {
        if (state.config) {
            const cfg = state.config;
            document.getElementById("cfg-stiffness").value = cfg.stiffness;
            document.getElementById("val-stiffness").textContent = cfg.stiffness.toFixed(2);
            document.getElementById("cfg-degree").value    = cfg.degree;
            document.getElementById("cfg-bass-split").value = cfg.bass_split;
        }
        const list = document.getElementById("cp-list");
        list.innerHTML = "";
        for (const cp of (state.control_points ?? [])) {
            const row = document.createElement("div");
            row.className = "cp-row" + (cp.is_anchor ? " anchor" : "");
            row.innerHTML =
                `<span>MIDI ${cp.midi}</span>` +
                `<span>${cp.value.toFixed(4)}</span>` +
                `<span>λ=${cp.stickiness.toFixed(1)}</span>` +
                `<button onclick="app.removeCP(${cp.midi})"` +
                ` style="width:auto;padding:1px 6px;font-size:10px">×</button>`;
            list.appendChild(row);
        }
    }

    async removeCP(midi) {
        if (!this._layerId) return;
        for (const vel of this._selected)
            await api.removePoint(`${this._layerId}__vel${vel}`, midi);
        await this.fitAndRedraw();
        this._refreshCPList();
    }
}

function setStatus(msg, isError = false) {
    const el = document.getElementById("status-msg");
    if (el) { el.textContent = msg; el.style.color = isError ? "#f66" : "#556"; }
}
