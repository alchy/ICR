/**
 * src/editor/LayerBrowser.js
 *
 * Dynamic layer browser — builds tabs and layer rows from /schema endpoint.
 * Three dimensions, each with its own tab:
 *   scalar      — one float per (midi, vel)       e.g. f0_hz, B, rms_gain
 *   per_partial — one float per (midi, vel, k)    e.g. tau1_k3, A0_k1
 *   eq          — array per (midi, vel)            e.g. gains_db curve
 *
 * Schema is re-fetched after every bank load so new/removed keys appear
 * automatically without any code change.
 *
 * Each layer row:
 *   [K] label              [●][⌇]
 *        └ green = kept     dots  splines
 */

export class LayerBrowser {
    /**
     * @param {Function} onSelect         (layerId, layer) => void
     * @param {Function} onToggleDots     (layerId, on) => void
     * @param {Function} onToggleSplines  (layerId, on) => void
     */
    constructor(onSelect, onToggleDots, onToggleSplines) {
        this._onSelect        = onSelect;
        this._onToggleDots    = onToggleDots;
        this._onToggleSplines = onToggleSplines;

        // schema: { scalar: [Layer], per_partial: [Layer], eq: [Layer], k_max: int }
        this._schema     = { scalar: [], per_partial: [], eq: [], k_max: 0 };
        this._dimension  = "scalar";   // active tab
        this._filterK    = 0;          // 0 = all partials
        this._activeId   = null;
        this._states     = new Map();  // layerId → {dotsOn, splinesOn, kept}
    }

    // ── Public API ────────────────────────────────────────────────────────────

    async load(api) {
        this._schema = await api.getSchema();
        this._buildKSelect();
        this._render();
    }

    setDimension(dim) {
        this._dimension = dim;
        this._filterK   = 0;

        // Update tab button styles
        document.querySelectorAll(".dim-tab").forEach(btn => {
            btn.classList.toggle("active-tab", btn.id === `tab-${dim}`);
        });

        // Show/hide k-filter
        const ksel = document.getElementById("sel-partial-k");
        if (ksel) ksel.style.display = (dim === "per_partial") ? "block" : "none";

        this._render();
    }

    filterK(k) { this._filterK = k; this._render(); }

    // Legacy: called by index.html's old sel-group onchange — no-op now
    filterGroup(_g) {}

    setActive(layerId) {
        this._activeId = layerId;
        document.querySelectorAll(".layer-item").forEach(el => {
            el.classList.toggle("active", el.dataset.lid === layerId);
        });
    }

    setLayerState(layerId, patch) {
        Object.assign(this._getState(layerId), patch);
        this._updateRow(layerId);
    }

    getLayerState(layerId) { return this._getState(layerId); }

    // ── Private ───────────────────────────────────────────────────────────────

    _getState(layerId) {
        if (!this._states.has(layerId))
            this._states.set(layerId, { dotsOn: false, splinesOn: false, kept: false });
        return this._states.get(layerId);
    }

    _buildKSelect() {
        const sel = document.getElementById("sel-partial-k");
        if (!sel) return;
        sel.innerHTML = '<option value="0">All partials</option>';
        const kMax = this._schema.k_max || 0;
        for (let k = 1; k <= kMax; k++) {
            const opt = document.createElement("option");
            opt.value = k; opt.textContent = `k = ${k}`;
            sel.appendChild(opt);
        }
        sel.style.display = (this._dimension === "per_partial") ? "block" : "none";
    }

    _layersForDim() {
        return this._schema[this._dimension] || [];
    }

    _render() {
        const list = document.getElementById("layer-list");
        list.innerHTML = "";

        // ── Header ────────────────────────────────────────────────────────────
        const hdr = document.createElement("div");
        hdr.style.cssText =
            "display:flex;align-items:center;justify-content:space-between;" +
            "padding:2px 0 6px;border-bottom:1px solid #223;margin-bottom:4px;";
        hdr.innerHTML = `
            <button id="btn-clear-viz"
                style="font-size:10px;padding:1px 6px;background:#111;
                       border:1px solid #335;color:#558;cursor:pointer;">
                Clear all
            </button>
            <span style="display:flex;gap:6px;font-size:10px;color:#446;padding-right:4px;">
                <span title="Dots column">●</span>
                <span title="Splines column">⌇</span>
            </span>`;
        hdr.querySelector("#btn-clear-viz").onclick = () => this._clearAll();
        list.appendChild(hdr);

        // ── Layer rows ────────────────────────────────────────────────────────
        const layers = this._layersForDim();

        if (layers.length === 0) {
            const empty = document.createElement("div");
            empty.style.cssText = "font-size:10px;color:#446;padding:8px 4px;";
            empty.textContent   = this._schema.k_max === 0
                ? "Load a soundbank to see layers."
                : `No ${this._dimension} layers found.`;
            list.appendChild(empty);
            return;
        }

        // Group by first token of id (e.g. "tau1" from "tau1_k3") for per_partial,
        // or by key name for scalar — gives visual sub-grouping within the tab.
        const grouped = _groupLayers(layers, this._dimension);

        for (const [groupName, groupLayers] of Object.entries(grouped)) {
            // Skip filtered k for per_partial
            const visible = (this._dimension === "per_partial" && this._filterK > 0)
                ? groupLayers.filter(l => l.partial_k === this._filterK)
                : groupLayers;
            if (visible.length === 0) continue;

            const heading = document.createElement("div");
            heading.style.cssText =
                "font-size:10px;color:#446;text-transform:uppercase;" +
                "letter-spacing:.1em;padding:4px 0 2px;";
            heading.textContent = groupName;
            list.appendChild(heading);

            for (const layer of visible) {
                list.appendChild(this._makeRow(layer));
            }
        }
    }

    _makeRow(layer) {
        const state    = this._getState(layer.id);
        const isActive = layer.id === this._activeId;

        const row = document.createElement("div");
        row.className   = "layer-item" + (isActive ? " active" : "");
        row.dataset.lid = layer.id;
        row.style.cssText =
            `display:flex;align-items:center;justify-content:space-between;` +
            `border-left:3px solid ${layer.color_hex};` +
            (isActive ? "background:#0a1a0a;" : "");

        // Keep badge
        const kbadge = document.createElement("span");
        kbadge.dataset.badge = "keep";
        kbadge.textContent   = "K";
        kbadge.title         = "Keep active";
        _styleBadge(kbadge, state.kept, "#4a8a4a");
        kbadge.style.marginRight = "4px";
        kbadge.style.flexShrink  = "0";

        // Label
        const label = document.createElement("span");
        label.textContent   = layer.label;
        label.title         = layer.id;
        label.style.cssText =
            "flex:1;cursor:pointer;overflow:hidden;text-overflow:ellipsis;" +
            "white-space:nowrap;font-size:11px;";
        label.onclick = () => { this._onSelect(layer.id, layer); this.setActive(layer.id); };

        // Toggle buttons (dots + splines) — EQ tab gets info button instead
        const checks = document.createElement("span");
        checks.style.cssText = "display:flex;gap:3px;flex-shrink:0;margin-left:4px;";

        if (this._dimension === "eq") {
            const btn = document.createElement("button");
            btn.textContent = "edit";
            btn.title       = "Open EQ editor for selected note";
            btn.style.cssText =
                "font-size:9px;padding:1px 5px;border-radius:3px;cursor:pointer;" +
                "border:1px solid #446;color:#88a;background:transparent;";
            btn.onclick = (e) => { e.stopPropagation(); this._onSelect(layer.id, layer); };
            checks.appendChild(btn);
        } else {
            const cbDots    = _makeCheckbox("●", state.dotsOn,    "#4af", "Show data points");
            const cbSplines = _makeCheckbox("⌇", state.splinesOn, "#fa8", "Show spline tubes");

            cbDots.onclick = (e) => {
                e.stopPropagation();
                const s = this._getState(layer.id);
                s.dotsOn = !s.dotsOn;
                _setCheckboxActive(cbDots, s.dotsOn, "#4af");
                this._onToggleDots?.(layer.id, s.dotsOn);
            };
            cbSplines.onclick = (e) => {
                e.stopPropagation();
                const s = this._getState(layer.id);
                s.splinesOn = !s.splinesOn;
                _setCheckboxActive(cbSplines, s.splinesOn, "#fa8");
                this._onToggleSplines?.(layer.id, s.splinesOn);
            };
            checks.append(cbDots, cbSplines);
        }

        row.append(kbadge, label, checks);
        return row;
    }

    _updateRow(layerId) {
        const row = document.querySelector(`.layer-item[data-lid="${layerId}"]`);
        if (!row) return;
        const state = this._getState(layerId);

        const keep = row.querySelector("[data-badge=keep]");
        if (keep) _styleBadge(keep, state.kept, "#4a8a4a");

        const [cbDots, cbSplines] = row.querySelectorAll(".layer-cb");
        if (cbDots)    _setCheckboxActive(cbDots,    state.dotsOn,    "#4af");
        if (cbSplines) _setCheckboxActive(cbSplines, state.splinesOn, "#fa8");
    }

    _clearAll() {
        for (const [lid, s] of this._states) {
            if (s.dotsOn)    { s.dotsOn    = false; this._onToggleDots?.(lid,    false); }
            if (s.splinesOn) { s.splinesOn = false; this._onToggleSplines?.(lid, false); }
        }
        this._render();
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Group layers for display within a tab.
 * scalar/eq:      group by key name (each key = its own group header)
 * per_partial:    group by param name (e.g. all tau1_kN under "tau1")
 */
function _groupLayers(layers, dimension) {
    const groups = {};
    for (const layer of layers) {
        let groupName;
        if (dimension === "per_partial") {
            // "tau1_k3" → "tau1",  "beat_hz_k1" → "beat_hz"
            groupName = layer.id.replace(/_k\d+$/, "");
        } else {
            groupName = layer.id;
        }
        (groups[groupName] ??= []).push(layer);
    }
    return groups;
}

function _makeCheckbox(symbol, active, activeColor, title) {
    const el = document.createElement("button");
    el.className   = "layer-cb";
    el.textContent = symbol;
    el.title       = title;
    _setCheckboxActive(el, active, activeColor);
    return el;
}

function _setCheckboxActive(el, active, activeColor) {
    el.style.cssText =
        `font-size:10px;padding:1px 5px;border-radius:3px;cursor:pointer;` +
        `border:1px solid ${active ? activeColor : "#334"};` +
        `color:${active ? activeColor : "#446"};` +
        `background:${active ? activeColor + "22" : "transparent"};`;
}

function _styleBadge(el, active, color) {
    el.style.cssText =
        `font-size:9px;padding:1px 4px;border-radius:3px;` +
        `border:1px solid ${active ? color : "#334"};` +
        `color:${active ? color : "#446"};` +
        `background:${active ? color + "22" : "transparent"};`;
}
