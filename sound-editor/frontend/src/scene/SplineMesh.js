/**
 * src/scene/SplineMesh.js
 * ───────────────────────
 * Renders the fitted spline as a tube geometry.
 * One SplineMesh per (layer, velocity) combination.
 */

import * as THREE from "three";

export class SplineMesh {
    /**
     * @param {string} colorHex   layer colour from registry
     * @param {number} vel        velocity layer (Z axis)
     * @param {Function} xFromMidi  maps midi → world X
     * @param {Function} yFromVal   maps value → world Y
     */
    constructor(colorHex, vel, xFromMidi, yFromVal) {
        this._colorHex  = colorHex;
        this._vel       = vel;
        this._xFromMidi = xFromMidi;
        this._yFromVal  = yFromVal;

        this._tube = null;
        this._mat  = new THREE.MeshStandardMaterial({
            color:     colorHex,
            emissive:  colorHex,
            emissiveIntensity: 0.35,
            roughness: 0.5,
            metalness: 0.2,
        });
    }

    /**
     * Rebuild the tube from backend curve data.
     * @param {number[]} xMidi   MIDI positions (dense)
     * @param {number[]} yVals   fitted values
     * @param {THREE.Scene} scene
     */
    update(xMidi, yVals, scene) {
        this._remove(scene);

        if (!xMidi || xMidi.length < 2) return;

        const zWorld = this._vel * 1.2;  // vel → Z spacing

        const points = xMidi.map((m, i) =>
            new THREE.Vector3(
                this._xFromMidi(m),
                this._yFromVal(yVals[i]),
                zWorld,
            )
        );

        const curve = new THREE.CatmullRomCurve3(points);
        const geom  = new THREE.TubeGeometry(curve, points.length * 2, 0.03, 6, false);
        this._tube  = new THREE.Mesh(geom, this._mat);
        this._tube.userData.isSpline = true;
        scene.add(this._tube);
    }

    _remove(scene) {
        if (this._tube) {
            scene.remove(this._tube);
            this._tube.geometry.dispose();
            this._tube = null;
        }
    }

    dispose(scene) {
        this._remove(scene);
        this._mat.dispose();
    }
}
