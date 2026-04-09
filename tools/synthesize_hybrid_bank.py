"""
tools/synthesize_hybrid_bank.py
────────────────────────────────
Synthesize a hybrid additive bank from two source banks:
  - pl-grand: excellent treble (MIDI 78+), good deep bass
  - ks-grand: rich harmonics in middle register (MIDI 46-77)

Strategy per note:
  1. Start with pl-grand as base
  2. Measure k5/k1 amplitude ratio (spectral richness)
  3. If ratio < threshold → borrow spectral shape from ks-grand
  4. If ks-grand also weak → apply physics floor sin(n*pi/8)/n
  5. Preserve pl-grand's decay (tau1/tau2/a1) and noise params
  6. Recalibrate rms_gain

Usage:
    python tools/synthesize_hybrid_bank.py
    python tools/synthesize_hybrid_bank.py --out soundbanks-additive/hybrid.json
"""

import json
import math
import os
import sys
import copy
from datetime import datetime


def spectral_richness(partials):
    """k5/k1 amplitude ratio — proxy for harmonic richness."""
    pk = {p['k']: p for p in partials}
    a1 = pk.get(1, {}).get('A0', 0)
    a5 = pk.get(5, {}).get('A0', 0)
    if a1 < 1e-12:
        return 0.0
    return a5 / a1


def physics_floor_amplitudes(n_partials, a0_k1, strike_pos=1.0/8.0):
    """Physics minimum amplitudes from hammer at strike_pos.
    A_min(k) = sin(k * pi * strike_pos) / k, normalized to a0_k1.
    """
    ref = abs(math.sin(math.pi * strike_pos))  # k=1 reference
    if ref < 1e-12:
        return [0.0] * n_partials
    floor = []
    for k in range(1, n_partials + 1):
        raw = abs(math.sin(k * math.pi * strike_pos)) / k
        floor.append(raw / ref * a0_k1)
    return floor


def borrow_spectral_shape(target_partials, donor_partials, blend=0.7):
    """Blend donor's spectral tilt into target, preserving target's A0(k=1).

    Only boosts — never cuts. Preserves target's decay/beating params.
    blend=1.0 means fully replace shape, 0.0 means no change.
    """
    tp = {p['k']: p for p in target_partials}
    dp = {p['k']: p for p in donor_partials}

    t_a1 = tp.get(1, {}).get('A0', 1.0)
    d_a1 = dp.get(1, {}).get('A0', 1.0)

    if d_a1 < 1e-12 or t_a1 < 1e-12:
        return target_partials

    result = []
    for p in target_partials:
        k = p['k']
        rp = copy.deepcopy(p)

        if k in dp and k > 1:
            # Donor's relative amplitude for this partial
            donor_rel = dp[k]['A0'] / d_a1
            # Target value from donor shape
            donor_a0 = donor_rel * t_a1
            # Blend: only boost, never cut
            blended = rp['A0'] + blend * max(0, donor_a0 - rp['A0'])
            rp['A0'] = blended

        result.append(rp)

    return result


def apply_physics_floor(partials, scale=0.35):
    """Boost partials below physics floor. Only raises — never cuts."""
    pk = {p['k']: p for p in partials}
    a0_k1 = pk.get(1, {}).get('A0', 1.0)
    n = max(p['k'] for p in partials) if partials else 0
    floor = physics_floor_amplitudes(n, a0_k1)

    result = []
    for p in partials:
        rp = copy.deepcopy(p)
        k = rp['k']
        if 1 < k <= len(floor):
            f = floor[k - 1] * scale
            if rp['A0'] < f:
                rp['A0'] = f
        result.append(rp)

    return result


def synthesize_hybrid(pl_path, ks_path, richness_threshold=0.10):
    """Create hybrid bank."""

    with open(pl_path) as f:
        pl = json.load(f)
    with open(ks_path) as f:
        ks = json.load(f)

    pl_notes = pl['notes']
    ks_notes = ks['notes']

    hybrid_notes = {}
    stats = {'pl_kept': 0, 'ks_borrowed': 0, 'floor_applied': 0, 'total': 0}

    for key, note in pl_notes.items():
        midi = note['midi']
        vel = note['vel']
        partials = note.get('partials', [])
        stats['total'] += 1

        if not partials:
            hybrid_notes[key] = copy.deepcopy(note)
            stats['pl_kept'] += 1
            continue

        richness = spectral_richness(partials)

        if richness >= richness_threshold:
            # pl-grand is fine — keep as-is
            hybrid_notes[key] = copy.deepcopy(note)
            stats['pl_kept'] += 1
            continue

        # pl-grand is hollow — try ks-grand donor
        ks_key = f"m{midi:03d}_vel{vel}"
        ks_note = ks_notes.get(ks_key)

        new_note = copy.deepcopy(note)

        if ks_note and spectral_richness(ks_note.get('partials', [])) > richness_threshold:
            # Borrow spectral shape from ks-grand
            new_note['partials'] = borrow_spectral_shape(
                partials, ks_note['partials'], blend=0.7)
            stats['ks_borrowed'] += 1
        else:
            # Both banks weak — apply physics floor
            new_note['partials'] = apply_physics_floor(partials, scale=0.35)
            stats['floor_applied'] += 1

        hybrid_notes[key] = new_note

    # Build output bank
    hybrid = {
        'metadata': {
            'instrument_name': 'hybrid-grand',
            'midi_range_from': pl['metadata'].get('midi_range_from', 21),
            'midi_range_to': pl['metadata'].get('midi_range_to', 108),
            'source': f'hybrid: pl-grand + ks-grand spectral shape',
            'sr': pl['metadata'].get('sr', 44100),
            'target_rms': pl['metadata'].get('target_rms', 0.06),
            'vel_gamma': pl['metadata'].get('vel_gamma', 0.7),
            'k_max': pl['metadata'].get('k_max', 60),
            'rng_seed': pl['metadata'].get('rng_seed', 0),
            'duration_s': pl['metadata'].get('duration_s', 3.0),
            'created': datetime.now().isoformat(timespec='seconds'),
            'description': (
                f'Hybrid: pl-grand base + ks-grand spectral shape for hollow notes '
                f'(k5/k1 < {richness_threshold}). Physics floor as fallback.'
            ),
        },
        'notes': hybrid_notes,
    }

    return hybrid, stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Synthesize hybrid additive bank')
    parser.add_argument('--pl', default='soundbanks-additive/pl-grand-04072232.json',
                        help='pl-grand source bank')
    parser.add_argument('--ks', default='soundbanks-additive/ks-grand-04090735-relaxed.json',
                        help='ks-grand source bank')
    parser.add_argument('--out', default=None, help='Output path')
    parser.add_argument('--threshold', type=float, default=0.10,
                        help='k5/k1 ratio below which to apply correction')
    args = parser.parse_args()

    if args.out is None:
        ts = datetime.now().strftime('%m%d%H%M')
        args.out = f'soundbanks-additive/hybrid-grand-{ts}.json'

    hybrid, stats = synthesize_hybrid(args.pl, args.ks, args.threshold)

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(hybrid, f, indent=2)

    size_kb = os.path.getsize(args.out) / 1024
    print(f'Hybrid bank: {args.out} ({size_kb:.0f} KB)')
    print(f'  Total notes:     {stats["total"]}')
    print(f'  pl-grand kept:   {stats["pl_kept"]} (rich enough)')
    print(f'  ks-grand shape:  {stats["ks_borrowed"]} (borrowed spectral tilt)')
    print(f'  Physics floor:   {stats["floor_applied"]} (both banks weak)')

    # Verify improvement
    print('\nVerification (vel=4):')
    notes = hybrid['notes']
    for midi in [48, 53, 55, 60, 61, 65, 71, 88]:
        key = f'm{midi:03d}_vel4'
        if key not in notes:
            continue
        r = spectral_richness(notes[key].get('partials', []))
        print(f'  MIDI {midi}: k5/k1 = {r:.4f}')
