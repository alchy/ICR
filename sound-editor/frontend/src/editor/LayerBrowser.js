/**
 * src/editor/LayerBrowser.js
 * ──────────────────────────
 * Left-panel layer list with per-layer state badges.
 *
 * Each layer row:
 *   [colored bar] label         [👁] [K]
 *                  └ InEdit = active highlight (blue left border)
 *                               Visible toggle
 *                                    Keep indicator
 */

export class LayerBrowser {
    /**
     * @param {Function} onSelect        (layerId, layer) => void
     * @param {Function} onToggleVisible (layerId) => void
     */
    constructor(onSelect, onToggleVisible) {
        this._onSelect        = onSelect;
        this._onToggleVisible = onToggleVisible;
        this._groups          = {};
        this._activeId        = null;
        this._filterGroup     = "";
        this._filterK         = 0;
        // per-layer state: { kept: bool, visible: bool }
        this._states          = new Map();
    }

    async load(api) {
        this._groups = await api.getLayers();
        this._buildKSelect();
        this._render();
    }

    filterGroup(group) { this._filterGroup = group; this._render(); }
    filterK(k)         { this._filterK = k;          this._render(); }

    setActive(layerId) {
        this._activeId = layerId;
        document.querySelectorAll(".layer-item").forEach(el => {
            el.classList.toggle("active", el.dataset.lid === layerId);
        });
    }

    /** Called by SplineEditor when Keep/Visible state changes for a layer. */
    setLayerState(layerId, { kept, visible }) {
        this._states.set(layerId, { kept: !!kept, visible: visible !== false });
        this._updateBadges(layerId);
    }

    getLayerState(layerId) {
        return this._states.get(layerId) ?? { kept: false, visible: true };
    }

    // ── Private ───────────────────────────────────────────────────────────────

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
        const state = this.getLayerState(layer.id);

        const item  = document.createElement("div");
        item.className = "layer-item" + (layer.id === this._activeId ? " active" : "");
        item.dataset.lid = layer.id;
        item.style.borderLeftColor = layer.color_hex;
        item.style.display         = "flex";
        item.style.alignItems      = "center";
        item.style.justifyContent  = "space-between";

        // Label (clickable → activate)
        const label = document.createElement("span");
        label.textContent = layer.label;
        label.title       = layer.id;
        label.style.cssText = "flex:1;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
        label.onclick = () => { this._onSelect(layer.id, layer); this.setActive(layer.id); };

        // Badges container
        const badges = document.createElement("span");
        badges.style.cssText = "display:flex;gap:3px;flex-shrink:0;margin-left:4px;";

        // Visible toggle
        const vis = document.createElement("button");
        vis.dataset.badge = "vis";
        vis.title         = "Visible";
        vis.textContent   = "👁";
        _styleBadge(vis, state.visible, "#4af");
        vis.onclick = (e) => {
            e.stopPropagation();
            const cur = this.getLayerState(layer.id);
            this.setLayerState(layer.id, { ...cur, visible: !cur.visible });
            this._onToggleVisible?.(layer.id, !cur.visible);
        };

        // Keep indicator (read-only, updated via setLayerState)
        const keep = document.createElement("span");
        keep.dataset.badge = "keep";
        keep.title         = "Keep active";
        keep.textContent   = "K";
        _styleBadge(keep, state.kept, "#4a8a4a");

        badges.append(vis, keep);
        item.append(label, badges);
        return item;
    }

    _updateBadges(layerId) {
        const state = this.getLayerState(layerId);
        document.querySelectorAll(`.layer-item[data-lid="${layerId}"]`).forEach(row => {
            const vis  = row.querySelector("[data-badge=vis]");
            const keep = row.querySelector("[data-badge=keep]");
            if (vis)  _styleBadge(vis,  state.visible, "#4af");
            if (keep) _styleBadge(keep, state.kept,    "#4a8a4a");
        });
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _styleBadge(el, active, activeColor) {
    el.style.cssText =
        `font-size:9px;padding:1px 4px;border-radius:3px;cursor:default;` +
        `border:1px solid ${active ? activeColor : "#334"};` +
        `color:${active ? activeColor : "#446"};` +
        `background:${active ? activeColor + "22" : "transparent"};` +
        `transition:all .15s;`;
}
