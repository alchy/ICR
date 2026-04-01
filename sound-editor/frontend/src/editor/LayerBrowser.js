/**
 * src/editor/LayerBrowser.js
 * ──────────────────────────
 * Populates the left-panel layer list from backend /layers endpoint.
 */

export class LayerBrowser {
    /**
     * @param {Function} onSelect  (layerId: string, layer: Object) => void
     */
    constructor(onSelect) {
        this._onSelect   = onSelect;
        this._groups     = {};
        this._activeId   = null;
        this._filterGroup = "";
        this._filterK     = 0;
    }

    async load(api) {
        this._groups = await api.getLayers();
        this._buildKSelect();
        this._render();
    }

    filterGroup(group) {
        this._filterGroup = group;
        this._render();
    }

    filterK(k) {
        this._filterK = k;
        this._render();
    }

    setActive(layerId) {
        this._activeId = layerId;
        document.querySelectorAll(".layer-item").forEach(el => {
            el.classList.toggle("active", el.dataset.lid === layerId);
        });
    }

    _buildKSelect() {
        const sel = document.getElementById("sel-partial-k");
        sel.innerHTML = '<option value="0">All partials</option>';
        // Determine max K from partial layers
        let maxK = 0;
        for (const layers of Object.values(this._groups)) {
            for (const l of layers) {
                if (l.partial_k && l.partial_k > maxK) maxK = l.partial_k;
            }
        }
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
            heading.style.cssText = "font-size:10px;color:#446;text-transform:uppercase;" +
                                    "letter-spacing:.1em;padding:4px 0 2px;";
            heading.textContent = group;
            list.appendChild(heading);

            for (const layer of layers) {
                if (this._filterK && layer.partial_k !== this._filterK) continue;

                const item = document.createElement("div");
                item.className     = "layer-item" + (layer.id === this._activeId ? " active" : "");
                item.dataset.lid   = layer.id;
                item.style.borderLeftColor = layer.color_hex;
                item.textContent   = layer.label;
                item.title         = layer.id;
                item.onclick       = () => {
                    this._onSelect(layer.id, layer);
                    this.setActive(layer.id);
                };
                list.appendChild(item);
            }
        }
    }
}
