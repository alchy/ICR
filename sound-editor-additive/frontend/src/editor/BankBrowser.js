/**
 * src/editor/BankBrowser.js
 * --------------------------
 * Phase 1: Browse extractions, rate notes, build a catalog of good notes.
 *
 * Workflow:
 *   1. Select a bank from the dropdown
 *   2. Click MIDI notes on the mini-keyboard
 *   3. Listen via audition (Play button)
 *   4. Rate (1-5 stars) and Add to Catalog
 *   5. Switch banks and repeat
 */

import api from "../comms/ApiClient.js";

const MIDI_LO = 21, MIDI_HI = 108;

export class BankBrowser {
    constructor(containerEl) {
        this._el = containerEl;
        this._banks = [];
        this._selectedBank = null;   // { filename, path }
        this._midi = 60;
        this._vel = 4;
        this._rating = 4;
        this._velocity = 80;
        this._catalog = [];
        this._build();
        this._loadBanks();
        this._loadCatalog();
    }

    _build() {
        this._el.innerHTML = `
<div style="padding:6px;font-size:12px">
  <div style="font-weight:bold;margin-bottom:6px">Bank Browser</div>

  <div style="margin-bottom:6px">
    <select id="bb-bank" style="width:100%"></select>
  </div>

  <div id="bb-keyboard" style="margin-bottom:6px;height:24px;display:flex;gap:1px;overflow:hidden"></div>

  <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
    <span>MIDI <b id="bb-midi-label">60</b></span>
    <select id="bb-vel" style="width:54px">
      ${[0,1,2,3,4,5,6,7].map(v => `<option value="${v}"${v===4?' selected':''}>vel ${v}</option>`).join('')}
    </select>
    <button id="bb-play" title="Audition">&#9654;</button>
    <label style="font-size:10px">
      Vel: <input id="bb-audition-vel" type="range" min="1" max="127" value="80" style="width:50px">
    </label>
  </div>

  <div style="display:flex;gap:4px;align-items:center;margin-bottom:8px">
    <span style="font-size:11px">Rating:</span>
    ${[1,2,3,4,5].map(r => `<button class="bb-star" data-r="${r}" style="cursor:pointer;background:none;border:none;font-size:16px;padding:0">&#9734;</button>`).join('')}
    <button id="bb-add" class="primary" style="margin-left:8px">Add to Catalog</button>
  </div>

  <div style="border-top:1px solid #444;padding-top:6px;margin-top:4px">
    <div style="display:flex;justify-content:space-between;margin-bottom:4px">
      <b>Catalog (<span id="bb-cat-count">0</span>)</b>
      <button id="bb-cat-clear" style="font-size:10px">Clear</button>
    </div>
    <div id="bb-catalog" style="max-height:200px;overflow-y:auto;font-size:11px"></div>
  </div>
</div>`;

        const $ = s => this._el.querySelector(s);

        // Bank selector
        $("#bb-bank").onchange = e => {
            const idx = e.target.selectedIndex;
            if (idx >= 0 && idx < this._banks.length)
                this._selectedBank = this._banks[idx];
        };

        // Mini keyboard
        this._buildKeyboard();

        // Velocity / audition
        $("#bb-vel").onchange = e => this._vel = parseInt(e.target.value);
        $("#bb-audition-vel").oninput = e => this._velocity = parseInt(e.target.value);
        $("#bb-play").onclick = () => this._play();

        // Rating stars
        this._el.querySelectorAll(".bb-star").forEach(btn => {
            btn.onclick = () => this._setRating(parseInt(btn.dataset.r));
        });

        // Add to catalog
        $("#bb-add").onclick = () => this._addToCatalog();
        $("#bb-cat-clear").onclick = () => this._clearCatalog();
    }

    _buildKeyboard() {
        const kb = this._el.querySelector("#bb-keyboard");
        kb.innerHTML = "";
        for (let m = MIDI_LO; m <= MIDI_HI; m++) {
            const isBlack = [1,3,6,8,10].includes(m % 12);
            const key = document.createElement("div");
            key.style.cssText = `flex:1;min-width:2px;cursor:pointer;border-radius:1px;` +
                `background:${isBlack ? '#444' : '#888'};`;
            key.title = `MIDI ${m}`;
            key.onclick = () => this._selectNote(m);
            key.dataset.midi = m;
            kb.appendChild(key);
        }
    }

    _selectNote(midi) {
        this._midi = midi;
        this._el.querySelector("#bb-midi-label").textContent = midi;
        // Highlight selected key
        this._el.querySelectorAll("#bb-keyboard > div").forEach(k => {
            const m = parseInt(k.dataset.midi);
            const isBlack = [1,3,6,8,10].includes(m % 12);
            k.style.background = (m === midi) ? '#4af' : (isBlack ? '#444' : '#888');
        });
    }

    _setRating(r) {
        this._rating = r;
        this._el.querySelectorAll(".bb-star").forEach(btn => {
            const br = parseInt(btn.dataset.r);
            btn.innerHTML = br <= r ? '&#9733;' : '&#9734;';  // filled vs empty star
            btn.style.color = br <= r ? '#fa0' : '#666';
        });
    }

    async _play() {
        if (!this._selectedBank) return;
        api.audition(this._midi, this._velocity).catch(e => console.warn(e));
    }

    // -- Banks ----------------------------------------------------------------

    async _loadBanks() {
        try {
            const res = await api.listSoundbanks();
            this._banks = res.files.map(f => ({
                filename: f,
                path: res.dir + "/" + f,
            }));
            const sel = this._el.querySelector("#bb-bank");
            sel.innerHTML = this._banks.map(b =>
                `<option>${b.filename}</option>`).join('');
            if (this._banks.length > 0) this._selectedBank = this._banks[0];
        } catch (e) {
            console.warn("Failed to load banks:", e);
        }
    }

    // -- Catalog --------------------------------------------------------------

    async _loadCatalog() {
        try {
            const res = await api.getCatalog();
            this._catalog = res.entries || [];
            this._renderCatalog();
        } catch (e) {
            console.warn("Failed to load catalog:", e);
        }
    }

    async _addToCatalog() {
        if (!this._selectedBank) return;
        try {
            await api.catalogAdd(
                this._midi, this._vel, this._rating,
                this._selectedBank.filename, this._selectedBank.path);
            await this._loadCatalog();
        } catch (e) {
            console.warn("Failed to add to catalog:", e);
        }
    }

    async _removeCatalogEntry(id) {
        try {
            await api.catalogRemove(id);
            await this._loadCatalog();
        } catch (e) {
            console.warn("Failed to remove:", e);
        }
    }

    async _clearCatalog() {
        if (!confirm("Clear entire catalog?")) return;
        try {
            await api.catalogClear();
            await this._loadCatalog();
        } catch (e) {
            console.warn("Failed to clear:", e);
        }
    }

    _renderCatalog() {
        const div = this._el.querySelector("#bb-catalog");
        this._el.querySelector("#bb-cat-count").textContent = this._catalog.length;
        if (this._catalog.length === 0) {
            div.innerHTML = '<span style="color:#666">Empty — browse banks and add good notes</span>';
            return;
        }
        div.innerHTML = this._catalog.map(e => {
            const stars = '&#9733;'.repeat(e.rating) + '&#9734;'.repeat(5 - e.rating);
            return `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #333">
                <span>m${String(e.midi).padStart(3,'0')} v${e.vel}
                  <span style="color:#fa0">${stars}</span>
                  <span style="color:#888">${e.bank_file}</span></span>
                <button onclick="this.dispatchEvent(new CustomEvent('cat-rm',{bubbles:true,detail:${e.id}}))"
                        style="font-size:10px;cursor:pointer">&times;</button>
            </div>`;
        }).join('');

        div.onclick = (ev) => {
            const ce = ev.target.closest('[onclick]');
            if (ce) {
                const rmEv = new CustomEvent('cat-rm', { bubbles: true });
                // handled via event delegation below
            }
        };
        // Event delegation for remove buttons
        div.addEventListener('cat-rm', (ev) => {
            this._removeCatalogEntry(ev.detail);
        });
    }
}
