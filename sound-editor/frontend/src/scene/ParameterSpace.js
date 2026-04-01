/**
 * src/scene/ParameterSpace.js
 * ───────────────────────────
 * Main 3D scene: MIDI × Value × Velocity space.
 *
 * Axes:
 *   X  →  MIDI (21–108),  world range [−5, +5]
 *   Y  →  normalised value,  world range [0, 8]
 *   Z  →  velocity (0–7), world range [0, 8.4]
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CardMesh }   from "./CardMesh.js";
import { SplineMesh } from "./SplineMesh.js";
import { AxisHelper } from "./AxisHelper.js";

// ── World-space mapping helpers ───────────────────────────────────────────────

const MIDI_MIN  = 21;
const MIDI_MAX  = 108;
const WORLD_X_MIN = -5;
const WORLD_X_MAX =  5;

export function xFromMidi(m) {
    return WORLD_X_MIN + (m - MIDI_MIN) / (MIDI_MAX - MIDI_MIN) * (WORLD_X_MAX - WORLD_X_MIN);
}

export function midiFromX(x) {
    return MIDI_MIN + (x - WORLD_X_MIN) / (WORLD_X_MAX - WORLD_X_MIN) * (MIDI_MAX - MIDI_MIN);
}

/** Normalise value to world-Y.  layer.min_val / max_val provided by registry. */
export function makeYMapper(minVal, maxVal, worldH = 8) {
    const range = maxVal - minVal || 1;
    return (v) => ((v - minVal) / range) * worldH;
}


// ── ParameterSpace ────────────────────────────────────────────────────────────

export class ParameterSpace {
    constructor(container) {
        this._container = container;
        this._cards     = new Map();   // noteKey → CardMesh
        this._splines   = new Map();   // vel → SplineMesh
        this._keptDots  = [];          // THREE.Mesh — blue kept-position spheres
        this._axes      = null;
        this._raycaster = new THREE.Raycaster();
        this._mouse     = new THREE.Vector2();
        this._hoveredCard = null;
        this._yMapper   = makeYMapper(0, 1);

        this._initRenderer();
        this._initScene();
        this._initControls();
        this._initLights();
        this._initGrid();
        this._axes = new AxisHelper(this._scene);
        this._bindEvents();
        this._animate();
    }

    // ── Callbacks set by the editor ──────────────────────────────────────────

    onCardClick    = null;   // (card: CardMesh) => void
    onCardAltClick = null;   // (card: CardMesh) => void  → set anchor

    // ── Init ─────────────────────────────────────────────────────────────────

    _initRenderer() {
        this._renderer = new THREE.WebGLRenderer({ antialias: true });
        this._renderer.setPixelRatio(window.devicePixelRatio);
        this._renderer.setSize(
            this._container.clientWidth,
            this._container.clientHeight,
        );
        this._renderer.shadowMap.enabled = true;
        this._container.appendChild(this._renderer.domElement);
    }

    _initScene() {
        this._scene  = new THREE.Scene();
        this._scene.background = new THREE.Color(0x080810);
        this._scene.fog = new THREE.Fog(0x080810, 20, 60);

        this._camera = new THREE.PerspectiveCamera(
            60, this._container.clientWidth / this._container.clientHeight, 0.1, 100,
        );
        this._camera.position.set(0, 6, 18);
        this._camera.lookAt(0, 4, 4);
    }

    _initControls() {
        this._controls = new OrbitControls(this._camera, this._renderer.domElement);
        this._controls.enableDamping  = true;
        this._controls.dampingFactor  = 0.08;
        this._controls.target.set(0, 4, 4);
        this._controls.update();
    }

    _initLights() {
        this._scene.add(new THREE.AmbientLight(0x6688aa, 2.5));
        const dir = new THREE.DirectionalLight(0xffffff, 3.0);
        dir.position.set(5, 12, 8);
        dir.castShadow = true;
        this._scene.add(dir);
        // Fill light from front-below so cards facing camera are lit
        const fill = new THREE.DirectionalLight(0x88ccff, 1.5);
        fill.position.set(0, 2, 20);
        this._scene.add(fill);
    }

    _initGrid() {
        // MIDI axis guide lines
        for (let m = 24; m <= 108; m += 12) {
            const geom = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(xFromMidi(m), 0, -0.5),
                new THREE.Vector3(xFromMidi(m), 0, 9.5),
            ]);
            const mat  = new THREE.LineBasicMaterial({ color: 0x223344 });
            this._scene.add(new THREE.Line(geom, mat));
        }
        // floor plane
        const floor = new THREE.Mesh(
            new THREE.PlaneGeometry(12, 11),
            new THREE.MeshStandardMaterial({ color: 0x0d0d18, roughness: 1 }),
        );
        floor.rotation.x = -Math.PI / 2;
        floor.position.set(0, -0.05, 4.25);
        floor.receiveShadow = true;
        this._scene.add(floor);
    }

    _bindEvents() {
        window.addEventListener("resize", () => this._onResize());
        this._renderer.domElement.addEventListener("mousemove", e => this._onMouseMove(e));
        this._renderer.domElement.addEventListener("click",     e => this._onClick(e));
        this._renderer.domElement.addEventListener("contextmenu", e => {
            e.preventDefault();
            this._onClick(e, true);
        });
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Load all cards for a layer.
     * @param {Object} layerValues   { "m060_vel3": 0.41, ... }
     * @param {Object} layer         Layer registry entry (min_val, max_val, color_hex)
     */
    loadLayer(layerValues, layer) {
        this._clearCards();
        this._yMapper = makeYMapper(layer.min_val, layer.max_val);

        // Rebuild axes for this layer's value range
        this._axes.build(this._yMapper, layer);

        for (const [noteKey, value] of Object.entries(layerValues)) {
            const midi = parseInt(noteKey.slice(1, 4));
            const vel  = parseInt(noteKey.split("_vel")[1]);
            const card = new CardMesh({ midi, vel, value, label: noteKey });
            card.setPosition(
                xFromMidi(midi),
                this._yMapper(value),
                vel * 1.2,
            );
            this._cards.set(noteKey, card);
            this._scene.add(card.mesh);
        }
    }

    /**
     * Update the spline tube for a velocity layer.
     * @param {number}   vel
     * @param {number[]} xMidi    MIDI positions (dense)
     * @param {number[]} yVals    fitted values
     * @param {string}   colorHex
     */
    updateSpline(vel, xMidi, yVals, colorHex) {
        if (!this._splines.has(vel)) {
            this._splines.set(vel, new SplineMesh(
                colorHex, vel, xFromMidi, v => this._yMapper(v),
            ));
        }
        this._splines.get(vel).update(xMidi, yVals, this._scene);
    }

    /** Mark a card as anchor (visual only). */
    setCardAnchor(noteKey, on) {
        this._cards.get(noteKey)?.setAnchor(on);
    }

    /** Update a card's Y after a value change. */
    updateCardValue(noteKey, value) {
        const card = this._cards.get(noteKey);
        if (!card) return;
        card.value = value;
        card.mesh.position.y = this._yMapper(value);
    }

    clearSplines() {
        for (const sm of this._splines.values()) sm.dispose(this._scene);
        this._splines.clear();
    }

    setSplineVisibility(visible) {
        for (const sm of this._splines.values())
            for (const obj of sm._objects) obj.visible = visible;
    }

    /**
     * Keep activated: gray-out originals, spawn blue kept-position dots.
     * @param {Object} velFitted  { vel: { midi: value, … }, … }
     */
    applyKeep(velFitted) {
        this._clearKeptDots();

        // Gray out all existing cards
        for (const card of this._cards.values()) card.setGhost(true);

        // Spawn new blue dots at fitted positions
        const keptMat = new THREE.MeshStandardMaterial({
            color: 0x2255ff, roughness: 0.2, metalness: 0.6,
            emissive: 0x1133cc, emissiveIntensity: 1.0,
        });
        const keptGeo = new THREE.SphereGeometry(0.07, 8, 6);

        for (const [velStr, fittedMap] of Object.entries(velFitted)) {
            const vel = parseInt(velStr);
            for (const [midiStr, value] of Object.entries(fittedMap)) {
                const midi = parseInt(midiStr);
                const dot  = new THREE.Mesh(keptGeo, keptMat);
                dot.position.set(
                    xFromMidi(midi),
                    this._yMapper(value),
                    vel * 1.2,
                );
                this._scene.add(dot);
                this._keptDots.push(dot);
            }
        }
    }

    /** Keep deactivated: remove blue dots, restore original card colors. */
    clearKeep() {
        this._clearKeptDots();
        for (const card of this._cards.values()) card.setGhost(false);
    }

    _clearKeptDots() {
        for (const dot of this._keptDots) {
            this._scene.remove(dot);
            dot.geometry?.dispose();
            dot.material?.dispose();
        }
        this._keptDots = [];
    }

    // ── Internal ─────────────────────────────────────────────────────────────

    _clearCards() {
        for (const c of this._cards.values()) {
            this._scene.remove(c.mesh);
            c.dispose();
        }
        this._cards.clear();
        this.clearSplines();
    }

    _onResize() {
        const w = this._container.clientWidth;
        const h = this._container.clientHeight;
        this._camera.aspect = w / h;
        this._camera.updateProjectionMatrix();
        this._renderer.setSize(w, h);
    }

    _onMouseMove(e) {
        const rect = this._renderer.domElement.getBoundingClientRect();
        this._mouse.set(
            ((e.clientX - rect.left)  / rect.width)  *  2 - 1,
            -((e.clientY - rect.top) / rect.height) *  2 + 1,
        );

        this._raycaster.setFromCamera(this._mouse, this._camera);
        const meshes = [...this._cards.values()].map(c => c.mesh);
        const hits   = this._raycaster.intersectObjects(meshes);

        const hit = hits[0]?.object?.userData?.card ?? null;
        if (this._hoveredCard !== hit) {
            this._hoveredCard?.setHover(false);
            hit?.setHover(true);
            this._hoveredCard = hit;

            const hud = document.getElementById("hud-note");
            if (hit) {
                hud.textContent = `MIDI ${hit.midi}  vel ${hit.vel}  = ${hit.value.toFixed(4)}`;
                hud.style.display = "block";
            } else {
                hud.style.display = "none";
            }
        }
    }

    _onClick(e, altBtn = false) {
        if (!this._hoveredCard) return;
        if (altBtn || e.altKey) {
            this.onCardAltClick?.(this._hoveredCard);
        } else {
            this.onCardClick?.(this._hoveredCard);
        }
    }

    _animate() {
        requestAnimationFrame(() => this._animate());
        this._controls.update();
        this._renderer.render(this._scene, this._camera);
    }
}
