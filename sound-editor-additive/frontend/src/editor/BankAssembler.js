/**
 * src/editor/BankAssembler.js
 * ----------------------------
 * Phase 2: Assemble a target bank from base + catalog deep-copies.
 *
 * Workflow:
 *   1. Select base bank (latest extraction)
 *   2. Initialize assembler
 *   3. For each note: keep base, deep-copy from catalog, or proportional correct
 *   4. Save as edit-{bankname}-{timestamp}.json
 */

import api from "../comms/ApiClient.js";

export class BankAssembler {
    constructor(containerEl) {
        this._el = containerEl;
        this._banks = [];
        this._catalog = [];
        this._summary = null;
        this._sources = {};
        this._selectedMidi = 60;
        this._outputDir = "";
        this._build();
        this._loadBanks();
    }

    _build() {
        this._el.innerHTML = `
<div style="padding:6px;font-size:12px">
  <div style="font-weight:bold;margin-bottom:6px">Bank Assembler</div>

  <div style="display:flex;gap:4px;margin-bottom:6px;align-items:center">
    <span>Base:</span>
    <select id="ba-base" style="flex:1"></select>
    <button id="ba-init">Init</button>
  </div>

  <div id="ba-status" style="color:#888;margin-bottom:6px;font-size:11px">Not initialized</div>

  <div id="ba-keyboard" style="margin-bottom:6px;height:24px;display:flex;gap:1px;overflow:hidden"></div>

  <div id="ba-note-panel" style="display:none;border:1px solid #444;padding:6px;margin-bottom:6px">
    <div style="margin-bottom:4px">
      <b>MIDI <span id="ba-note-midi">60</span></b>
      — Source: <span id="ba-note-source" style="color:#4af">base</span>
    </div>

    <div id="ba-alternatives" style="margin-bottom:6px"></div>

    <div style="display:flex;gap:4px;flex-wrap:wrap">
      <button id="ba-keep-base">Keep Base</button>
      <button id="ba-play-target" title="Play current">&#9654; Target</button>
    </div>
  </div>

  <div style="display:flex;gap:4px;align-items:center;margin-top:8px">
    <input id="ba-output-dir" placeholder="Output dir (soundbanks-additive)"
           style="flex:1;font-size:11px">
    <button id="ba-save" class="primary" disabled>Save</button>
  </div>
</div>`;

        const $ = s => this._el.querySelector(s);

        $("#ba-init").onclick = () => this._init();
        $("#ba-save").onclick = () => this._save();
        $("#ba-keep-base").onclick = () => this._keepBase();
        $("#ba-play-target").onclick = () => {
            api.audition(this._selectedMidi, 80).catch(console.warn);
        };

        this._buildKeyboard();
    }

    _buildKeyboard() {
        const kb = this._el.querySelector("#ba-keyboard");
        kb.innerHTML = "";
        for (let m = 21; m <= 108; m++) {
            const key = document.createElement("div");
            key.style.cssText = "flex:1;min-width:2px;cursor:pointer;border-radius:1px;background:#555";
            key.title = `MIDI ${m}`;
            key.dataset.midi = m;
            key.onclick = () => this._selectNote(m);
            kb.appendChild(key);
        }
    }

    _colorKeyboard() {
        this._el.querySelectorAll("#ba-keyboard > div").forEach(k => {
            const m = parseInt(k.dataset.midi);
            // Check if any vel for this midi comes from catalog
            let fromCopy = false;
            for (let v = 0; v < 8; v++) {
                const key = `m${String(m).padStart(3,'0')}_vel${v}`;
                if (this._sources[key]?.startsWith("copy:")) { fromCopy = true; break; }
            }
            const sel = (m === this._selectedMidi);
            if (sel) k.style.background = '#fff';
            else if (fromCopy) k.style.background = '#48f';
            else k.style.background = '#555';
        });
    }

    async _selectNote(midi) {
        this._selectedMidi = midi;
        this._el.querySelector("#ba-note-midi").textContent = midi;
        this._el.querySelector("#ba-note-panel").style.display = "block";

        // Show source
        const src = this._sources[`m${String(midi).padStart(3,'0')}_vel4`] || "base";
        this._el.querySelector("#ba-note-source").textContent = src;

        // Load catalog alternatives for this midi
        try {
            const res = await api.catalogFind(midi);
            this._renderAlternatives(res.entries || []);
        } catch (e) {
            this._el.querySelector("#ba-alternatives").innerHTML = "";
        }

        this._colorKeyboard();
    }

    _renderAlternatives(entries) {
        const div = this._el.querySelector("#ba-alternatives");
        if (entries.length === 0) {
            div.innerHTML = '<span style="color:#666">No catalog entries for this note</span>';
            return;
        }
        div.innerHTML = entries.map(e => {
            const stars = '&#9733;'.repeat(e.rating) + '&#9734;'.repeat(5 - e.rating);
            return `<div style="display:flex;gap:4px;align-items:center;padding:2px 0">
                <span style="color:#fa0;font-size:13px">${stars}</span>
                <span style="color:#aaa;font-size:11px">${e.bank_file} v${e.vel}</span>
                <button class="ba-copy-btn" data-path="${e.bank_path}" data-midi="${e.midi}"
                        style="font-size:10px;margin-left:auto">Deep Copy</button>
            </div>`;
        }).join('');

        div.querySelectorAll(".ba-copy-btn").forEach(btn => {
            btn.onclick = () => this._deepCopy(
                parseInt(btn.dataset.midi),
                btn.dataset.path
            );
        });
    }

    // -- Actions --------------------------------------------------------------

    async _loadBanks() {
        try {
            const res = await api.listSoundbanks();
            this._banks = res.files.map(f => ({ filename: f, path: res.dir + "/" + f }));
            const sel = this._el.querySelector("#ba-base");
            sel.innerHTML = this._banks.map(b => `<option value="${b.path}">${b.filename}</option>`).join('');
            // Default output dir
            if (res.dir) this._el.querySelector("#ba-output-dir").value = res.dir;
            this._outputDir = res.dir;
        } catch (e) { console.warn(e); }
    }

    async _init() {
        const sel = this._el.querySelector("#ba-base");
        const path = sel.value;
        if (!path) return;

        try {
            const res = await api.assemblerInit(path);
            this._el.querySelector("#ba-status").innerHTML =
                `<span style="color:#4f4">Initialized: ${res.base_file} (${res.notes} notes)</span>`;
            this._el.querySelector("#ba-save").disabled = false;
            await this._refreshSources();
        } catch (e) {
            this._el.querySelector("#ba-status").innerHTML =
                `<span style="color:#f44">${e.message}</span>`;
        }
    }

    async _deepCopy(midi, sourcePath) {
        try {
            await api.assemblerDeepCopy(midi, -1, sourcePath);  // all vel layers
            await this._refreshSources();
            this._selectNote(midi);
        } catch (e) { console.warn(e); }
    }

    async _keepBase() {
        // Re-init from base effectively resets this note
        // For simplicity, re-init entire bank (TODO: per-note reset)
        const sel = this._el.querySelector("#ba-base");
        if (sel.value) {
            await api.assemblerInit(sel.value);
            await this._refreshSources();
            this._selectNote(this._selectedMidi);
        }
    }

    async _save() {
        const dir = this._el.querySelector("#ba-output-dir").value || this._outputDir;
        try {
            const res = await api.assemblerSave(dir, "edit-pl-grand");
            this._el.querySelector("#ba-status").innerHTML =
                `<span style="color:#4f4">Saved: ${res.saved}</span>`;
        } catch (e) {
            this._el.querySelector("#ba-status").innerHTML =
                `<span style="color:#f44">Save failed: ${e.message}</span>`;
        }
    }

    async _refreshSources() {
        try {
            this._sources = await api.assemblerSources();
            const sum = await api.assemblerSummary();
            this._summary = sum;
            const status = this._el.querySelector("#ba-status");
            if (sum.initialized) {
                status.innerHTML = `<span style="color:#4f4">${sum.total} notes: ` +
                    `${sum.from_base} base, ${sum.from_copy} copied, ${sum.from_edit} edited</span>`;
            }
            this._colorKeyboard();
        } catch (e) { console.warn(e); }
    }
}
