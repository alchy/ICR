/**
 * src/editor/SplineEditor.js
 * ──────────────────────────
 * Orchestrates multi-velocity spline interaction + layer visibility ghosts.
 *
 * Visibility rules:
 *   Active layer  + dotsOn    → full-color CardMesh spheres
 *   Active layer  + splinesOn → full-color SplineMesh tubes
 *   Inactive layer + dotsOn   → gray ghost Points cloud
 *   Inactive layer + splinesOn→ gray ghost Line per velocity
 *   Active layer = green highlight in browser, last clicked
 */

import api from "../comms/ApiClient.js";
import { VEL_COLORS } from "./VelSelector.js";

export class SplineEditor {
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

        // Per-layer cache for ghost rendering
        this._layerValues = new Map();   // layerId → {noteKey: value}
        this._layerCurves = new Map();   // layerId → { vel: {x,y} }
        this._layerMeta   = new Map();   // layerId → layer object

        space.onCardClick     = c => this._onCardClick(c);
        space.onCardAltClick  = c => this._onCardAltClick(c);
    }

    // ── VelSelector callback ──────────────────────────────────────────────────

    onSelectorChange({ selected, coherence, stickiness, keepToggled, applyPressed, fillPressed }) {
        const prevKept    = this._browser.getLayerState(this._layerId ?? "").kept;
        const keepChanged = keepToggled !== prevKept;
        this._selected    = selected;
        this._coherence   = coherence;
        this._stickiness  = stickiness;

        if (!this._layerId) return;

        if (fillPressed)        this._doFillMissing();
        else if (applyPressed)  this._doApply();
        else if (keepChanged)   keepToggled ? this._doKeep() : this._doUnkeep();
        else                    this.fitAndRedraw();
    }

    // ── Layer activation ──────────────────────────────────────────────────────

    async activateLayer(layerId, layer) {
        const prevId = this._layerId;
        this._layerId = layerId;
        this._layer   = layer;
        this._layerMeta.set(layerId, layer);

        document.getElementById("active-layer-name").textContent =
            `${layer.label}  [${layerId}]`;

        // Convert previous active layer to ghost (if it had visibility on)
        if (prevId && prevId !== layerId) {
            await this._refreshGhostsFor(prevId);
        }

        // Remove ghosts for newly activated layer (will show full color)
        this._space.removeGhostLayer(layerId);

        // Restore Keep button
        const state = this._browser.getLayerState(layerId);
        this._velSelector.setKept(state.kept);

        // Load dots if dotsOn
        const values = await api.getLayerValues(layerId);
        this._layerValues.set(layerId, values);

        if (state.dotsOn) {
            this._space.loadLayer(values, layer);
            if (state.kept) {
                const r = await api.fitAllVelocities(layerId, [...this._selected], this._coherence);
                const vf = {};
                for (const [v, d] of Object.entries(r)) vf[v] = d.fitted;
                this._space.applyKeep(vf);
            }
        } else {
            this._space.clearLayer();
        }

        const splineState = await api.getSpline(layerId);
        this._applyStateToUI(splineState);

        // Fit + show splines if splinesOn
        if (state.splinesOn) {
            await this.fitAndRedraw();
        } else {
            this._space.clearSplines();
        }
    }

    // ── Dot / spline visibility toggles (from LayerBrowser checkboxes) ────────

    async onToggleDots(layerId, on) {
        this._browser.setLayerState(layerId, { dotsOn: on });
        if (layerId === this._layerId) {
            // Active layer
            if (on) {
                const values = this._layerValues.get(layerId)
                    ?? await api.getLayerValues(layerId);
                this._layerValues.set(layerId, values);
                this._space.loadLayer(values, this._layer);
            } else {
                this._space.clearLayer();
            }
        } else {
            // Inactive layer → ghost
            await this._refreshGhostsFor(layerId);
        }
    }

    async onToggleSplines(layerId, on) {
        this._browser.setLayerState(layerId, { splinesOn: on });
        if (layerId === this._layerId) {
            if (on) await this.fitAndRedraw();
            else    this._space.clearSplines();
        } else {
            await this._refreshGhostsFor(layerId);
        }
    }

    // ── Ghost refresh for a specific layer ────────────────────────────────────

    async _refreshGhostsFor(layerId) {
        const state = this._browser.getLayerState(layerId);
        const layer = this._layerMeta.get(layerId);
        if (!layer) return;

        this._space.removeGhostLayer(layerId);

        if (state.dotsOn) {
            let values = this._layerValues.get(layerId);
            if (!values) {
                values = await api.getLayerValues(layerId);
                this._layerValues.set(layerId, values);
            }
            this._space.addGhostDots(layerId, values, layer);
        }

        if (state.splinesOn) {
            let curves = this._layerCurves.get(layerId);
            if (!curves) {
                const result = await api.fitAllVelocities(layerId, [0,1,2,3,4,5,6,7], 0);
                curves = {};
                for (const [v, d] of Object.entries(result)) curves[v] = d.curve;
                this._layerCurves.set(layerId, curves);
            }
            for (const [velStr, curve] of Object.entries(curves)) {
                this._space.addGhostSpline(layerId, parseInt(velStr), curve.x, curve.y, layer);
            }
        }
    }

    // ── Config ────────────────────────────────────────────────────────────────

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

    // ── Fit ───────────────────────────────────────────────────────────────────

    async fitAndRedraw() {
        if (!this._layerId) return;
        const state = this._browser.getLayerState(this._layerId);
        if (!state.splinesOn) return;   // don't fit if splines are off

        setStatus("Fitting…");
        try {
            const vels   = [...this._selected];
            const result = await api.fitAllVelocities(
                this._layerId, vels, this._coherence,
            );
            // Cache curves for potential ghost use later
            const cached = {};
            for (const [v, d] of Object.entries(result)) cached[v] = d.curve;
            this._layerCurves.set(this._layerId, cached);

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

            const state = this._browser.getLayerState(this._layerId);
            if (state.dotsOn) this._space.applyKeep(velFitted);

            if (state.splinesOn) {
                this._space.clearSplines();
                for (const [velStr, data] of Object.entries(result)) {
                    const vel = parseInt(velStr);
                    this._space.updateSpline(
                        vel, data.curve.x, data.curve.y,
                        VEL_COLORS[vel % VEL_COLORS.length],
                    );
                }
            }

            this._browser.setLayerState(this._layerId, { kept: true });
            setStatus("Kept ✓");
        } catch (err) {
            setStatus(`Keep error: ${err.message}`, true);
        }
    }

    async _doUnkeep() {
        if (!this._layerId) return;
        await api.unkeepLayer(this._layerId, [...this._selected]);
        this._space.clearKeep();
        this._browser.setLayerState(this._layerId, { kept: false });
        setStatus("Keep removed.");
        await this.fitAndRedraw();
    }

    async _doApply() {
        if (!this._layerId) return;
        setStatus("Applying…");
        try {
            await api.applyLayer(this._layerId, [...this._selected], this._coherence);

            this._space.clearKeep();
            this._velSelector.setKept(false);
            this._browser.setLayerState(this._layerId, { kept: false });

            const values = await api.getLayerValues(this._layerId);
            this._layerValues.set(this._layerId, values);

            const state = this._browser.getLayerState(this._layerId);
            if (state.dotsOn) this._space.loadLayer(values, this._layer);

            await this.fitAndRedraw();
            setStatus("Applied ✓");
        } catch (err) {
            setStatus(`Apply error: ${err.message}`, true);
        }
    }

    async _doFillMissing() {
        if (!this._layerId) return;
        setStatus("Filling missing values…");
        try {
            const res = await api.fillMissing(
                this._layerId, [...this._selected], this._coherence);

            // Reload values so newly filled points appear as blue dots
            const values = await api.getLayerValues(this._layerId);
            this._layerValues.set(this._layerId, values);

            const state = this._browser.getLayerState(this._layerId);
            if (state.dotsOn) this._space.loadLayer(values, this._layer);

            await this.fitAndRedraw();
            setStatus(`Filled ${res.filled} missing values ✓`);
        } catch (err) {
            setStatus(`Fill error: ${err.message}`, true);
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
