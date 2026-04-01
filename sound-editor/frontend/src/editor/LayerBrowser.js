/**
 * src/editor/LayerBrowser.js
 *
 * Each layer row:
 *   [K] label              [●][ ]
 *        └ green = active   dots  splines
 *
 * Header:
 *   [Clear all]  [●]  [⌇]   ← toggle all dots / all splines
 *
 * State per layer: { dotsOn, splinesOn, kept }
 * Default: dotsOn=false, splinesOn=false (nothing shown until user checks)
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
        this._groups          = {};
        this._activeId        = null;
        this._filterGroup     = "";
        this._filterK         = 0;
        this._states          = new Map();   // layerId → {dotsOn, splinesOn, kept}
    }

    async load(api) {
        this._groups = await api.getLayers();
        this._buildKSelect();
        this._render();
    }

    filterGroup(g) { this._filterGroup = g; this._render(); }
    filterK(k)     { this._filterK = k;     this._render(); }

    setActive(layerId) {
        this._activeId = layerId;
        document.querySelectorAll(".layer-item").forEach(el => {
            const isActive = el.dataset.lid === layerId;
            el.classList.toggle("active", isActive);
        });
    }

    /** Called by SplineEditor to reflect Keep/dots/splines state. */
    setLayerState(layerId, patch) {
        const s = this._getState(layerId);
        Object.assign(s, patch);
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
        sel.innerHTML = '<option value="0">All partials</option>';
        let maxK = 0;
        for (const layers of Object.values(this._groups))
            for (const l of layers)
                if (l.partial_k && l.partial_k > maxK) maxK = l.partial_k;
        for (let k = 1; k <= maxK; k++) {
            const opt = document.createElement("option");
            opt.value = k; opt.textContent = `Partial k=${k}`;
            sel.appendChild(opt);
        }
    }

    _render() {
        const list = document.getElementById("layer-list");
        list.innerHTML = "";

        // ── Header row ────────────────────────────────────────────────────────
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
        for (const [group, layers] of Object.entries(this._groups)) {
            if (this._filterGroup && group !== this._filterGroup) continue;

            const heading = document.createElement("div");
            heading.style.cssText =
                "font-size:10px;color:#446;text-transform:uppercase;" +
                "letter-spacing:.1em;padding:4px 0 2px;";
            heading.textContent = group;
            list.appendChild(heading);

            for (const layer of layers) {
                if (this._filterK && layer.partial_k !== this._filterK) continue;
                list.appendChild(this._makeRow(layer));
            }
        }
    }

    _makeRow(layer) {
        const state   = this._getState(layer.id);
        const isActive = layer.id === this._activeId;

        const row = document.createElement("div");
        row.className  = "layer-item" + (isActive ? " active" : "");
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

        // Checkboxes
        const checks = document.createElement("span");
        checks.style.cssText = "display:flex;gap:3px;flex-shrink:0;margin-left:4px;";

        const cbDots    = _makeCheckbox("●", state.dotsOn,    "#4af",  "Show data points");
        const cbSplines = _makeCheckbox("⌇", state.splinesOn, "#fa8",  "Show spline tubes");

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
        row.append(kbadge, label, checks);
        return row;
    }

    _updateRow(layerId) {
        const row = document.querySelector(`.layer-item[data-lid="${layerId}"]`);
        if (!row) return;
        const state = this._getState(layerId);

        const keep = row.querySelector("[data-badge=keep]");
        if (keep) _styleBadge(keep, state.kept, "#4a8a4a");

        // Update checkbox visuals
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
