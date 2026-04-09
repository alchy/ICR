#pragma once
/*
 * cores/physical_modeling_piano/physical_modeling_piano_math.h
 * ────────────────────────────────────────────────────────────
 * Pure DSP mathematics for physical piano modelling -- stateless, inline,
 * header-only.
 *
 * v1.0: Dual-rail digital waveguide (Teng 2012 / Smith 1992) with
 *       Chaigne-Askenfelt (1994) finite-difference hammer model.
 *
 * References:
 *   - Smith (1992): Physical Modelling using Digital Waveguides
 *   - Teng (2012): Piano Sounds Synthesis (MSc, Edinburgh)
 *   - Chaigne & Askenfelt (1994): Numerical simulations of piano strings
 *   - Van Duyne & Smith (1994): Dispersion via allpass cascade
 *   - Välimäki et al. (1996): Plucked string physical modeling
 *
 * Used by: PhysicalModelingPianoCore
 */

#include "dsp/dsp_math.h"
#include <cmath>
#include <algorithm>
#include <cstring>

namespace physics {

// ── Constants ────────────────────────────────────────────────────────────

static constexpr int   MAX_RAIL_LEN      = 2048;  // half-period, supports A0 @ 96 kHz
static constexpr int   MAX_STRINGS       = 3;
static constexpr int   MAX_DISP_STAGES   = 16;
static constexpr int   MAX_HAMMER_SAMPLES = 512;   // 10 ms @ 48 kHz

// ── Loss filter (Välimäki one-pole) ──────────────────────────────────────

/// One-pole low-pass loss filter for waveguide loop.
///   H(z) = g * (1-b) / (1 - b*z^-1)
struct LossFilter {
    float g = 0.999f;   // DC gain per round-trip
    float b = 0.3f;     // pole coefficient (treble damping)
    float s = 0.f;      // filter state
};

/// Compute loss filter from T60 decay times (Smith/Bank design).
inline LossFilter compute_loss_filter(float f0, float T60_fund,
                                       float T60_nyq, float sr) {
    float N = sr / f0;
    float g_dc  = std::pow(10.f, -3.f * N / std::max(T60_fund * sr, 1.f));
    float g_nyq = std::pow(10.f, -3.f * N / std::max(T60_nyq * sr, 0.001f * sr));
    g_dc  = std::max(0.5f,  std::min(g_dc,  0.9999f));
    g_nyq = std::max(0.01f, std::min(g_nyq, g_dc));
    float p = (g_dc - g_nyq) / std::max(g_dc + g_nyq, 1e-9f);
    p = std::max(0.f, std::min(p, 0.95f));
    return { g_dc, p, 0.f };
}

/// Process one sample through loss filter.
inline float loss_tick(float x, LossFilter& f) {
    float y = f.g * ((1.f - f.b) * x + f.b * f.s);
    f.s = y;
    return y;
}

/// Group delay of loss filter at DC (samples).
inline float loss_delay(const LossFilter& f) {
    float b2 = f.b * f.b;
    return f.b / std::max(1.f - b2, 0.01f);
}

// ── First-order allpass (tuning + dispersion) ────────────────────────────

struct AllpassState {
    float s = 0.f;
};

/// First-order allpass: H(z) = (a + z^-1) / (1 + a*z^-1)
inline float allpass_tick(float x, float a, AllpassState& st) {
    float y = a * x + st.s;
    st.s = x - a * y;
    return y;
}

/// Group delay of first-order allpass at DC: (1-a)/(1+a)
inline float allpass_delay_dc(float a) {
    return (1.f - a) / std::max(1.f + a, 0.01f);
}

// ── Dual-rail string ─────────────────────────────────────────────────────

// ── Bridge reflection filter ─────────────────────────────────────────
//
// Real piano bridge: frequency-dependent reflection coefficient.
// Low frequencies reflect almost fully (rigid, -1). High frequencies
// lose more energy to the soundboard (bridge is more compliant at HF).
//
// This is modeled as a one-pole low-pass on the reflected signal:
//   H_bridge(z) = -1 × [ (1-b_mix) + b_mix × LP(z) ]
//
// The effect: HF partials decay faster from the bridge side (additional
// damping beyond the nut-side loss filter), while LF sustains normally.
// This creates the asymmetric spectral decay characteristic of piano
// (bright attack, warm sustain) that distinguishes it from guitar/mandolin.

struct BridgeFilter {
    float state = 0.f;    // one-pole LPF state
    float coeff = 0.5f;   // pole coefficient (0=bypass, →1=more LF emphasis)
    float mix   = 0.15f;  // 0=rigid (-1), 1=full bridge LPF
};

/// Initialize bridge filter.
///   bridge_freq: cutoff frequency (Hz) — below this, reflection ≈ -1
///   bridge_mix:  how much HF is lost at bridge (0=rigid, 0.3=strong)
inline void bridge_filter_init(BridgeFilter& bf, float bridge_freq,
                               float /*bridge_Q*/, float bridge_mix,
                               float sr) {
    // One-pole coefficient from cutoff frequency
    float w = dsp::TAU * bridge_freq / sr;
    bf.coeff = std::exp(-w);
    bf.mix   = bridge_mix;
    bf.state = 0.f;
}

/// Apply bridge reflection: frequency-dependent, energy-conserving.
///   Returns reflected signal (always ≤ input magnitude).
inline float bridge_filter_tick(float x, BridgeFilter& bf) {
    // Low-pass filtered version of input
    bf.state = bf.coeff * bf.state + (1.f - bf.coeff) * x;

    // Mix: rigid reflection + bridge-filtered reflection
    // At DC (x ≈ state): output ≈ -x (rigid)
    // At HF (state ≈ 0): output ≈ -(1-mix)*x (attenuated)
    return -((1.f - bf.mix) * x + bf.mix * bf.state);
}

// ── Dual-rail string ─────────────────────────────────────────────────────

/// One dual-rail waveguide string with circular-buffer shift.
///
/// Two rails model physically traveling waves:
///   upper: right-traveling (nut → bridge), length M
///   lower: left-traveling  (bridge → nut), length M
///
/// Circular buffer avoids O(M) memmove per sample.
struct DualRailString {
    float upper[MAX_RAIL_LEN] = {};
    float lower[MAX_RAIL_LEN] = {};
    int   M          = 0;
    int   upper_base = 0;
    int   lower_base = 0;
    int   n0         = 0;

    // Per-string filter coefficients
    float g_dc       = 0.999f;
    float pole       = 0.3f;
    float ap_a       = 0.f;
    int   n_disp     = 0;
    float a_disp     = -0.15f;

    // Per-string filter states
    float lp_state   = 0.f;
    AllpassState ap_tune;
    AllpassState disp_st[MAX_DISP_STAGES];

    // Bridge admittance filter
    BridgeFilter bridge;

    // -- Circular buffer accessors --
    inline float  upper_at(int i) const { return upper[(upper_base + i) % M]; }
    inline float& upper_at(int i)       { return upper[(upper_base + i) % M]; }
    inline float  lower_at(int i) const { return lower[(lower_base + i) % M]; }
    inline float& lower_at(int i)       { return lower[(lower_base + i) % M]; }

    /// "Shift upper right" = new sample enters at position 0
    inline void shift_upper() { upper_base = (upper_base - 1 + M) % M; }
    /// "Shift lower left"  = new sample enters at position M-1
    inline void shift_lower() { lower_base = (lower_base + 1) % M; }
};

/// Initialize a dual-rail string for a given f0 with delay compensation.
///   bridge_freq/Q/mix: bridge admittance filter params (0 mix = rigid, old behavior)
inline void dual_rail_init(DualRailString& s, float f0, float sr,
                           int n_disp, float a_disp, float exc_x0,
                           float T60_fund, float T60_nyq, float gauge,
                           float bridge_freq = 400.f, float bridge_Q = 8.f,
                           float bridge_mix = 0.15f) {
    float N_period = sr / f0;

    // Loss filter
    float T60_nyq_eff = T60_nyq / std::max(gauge, 0.1f);
    LossFilter lf = compute_loss_filter(f0, T60_fund, T60_nyq_eff, sr);
    s.g_dc = lf.g;
    s.pole = lf.b;
    s.lp_state = 0.f;

    // Delay compensation: loss filter + dispersion cascade
    float filt_del = loss_delay(lf);
    float disp_del = (n_disp > 0) ? (float)n_disp * allpass_delay_dc(a_disp) : 0.f;

    // Each rail = half the compensated period
    float N_comp = N_period - filt_del - disp_del;
    int M = std::max(4, (int)(N_comp / 2.f));
    float frac = N_comp / 2.f - (float)M;
    if (frac < 0.1f) { M -= 1; frac += 1.f; }
    M = std::min(M, MAX_RAIL_LEN - 1);
    M = std::max(M, 4);

    s.M = M;
    s.ap_a = (1.f - frac) / (1.f + frac);
    s.n_disp = n_disp;
    s.a_disp = a_disp;
    s.n0 = std::max(1, std::min(M - 2, (int)std::round(exc_x0 * (float)M)));

    // Clear rails and states
    std::memset(s.upper, 0, sizeof(float) * M);
    std::memset(s.lower, 0, sizeof(float) * M);
    s.upper_base = 0;
    s.lower_base = 0;
    s.ap_tune.s = 0.f;
    for (int i = 0; i < MAX_DISP_STAGES; i++) s.disp_st[i].s = 0.f;

    // Bridge admittance filter
    bridge_filter_init(s.bridge, bridge_freq, bridge_Q, bridge_mix, sr);
}

/// Process one sample through the dual-rail waveguide.
/// Returns bridge output (right-traveling wave arriving at bridge).
inline float dual_rail_tick(DualRailString& s, float hammer_in) {
    // 1. Output: right-traveling wave at bridge
    float bridge_out = s.upper_at(s.M - 1);

    // 2. Bridge reflection: loss → dispersion → tuning → negate
    float x = bridge_out;

    // Loss filter
    float y = s.g_dc * ((1.f - s.pole) * x + s.pole * s.lp_state);
    s.lp_state = y;
    x = y;

    // Dispersion cascade
    for (int di = 0; di < s.n_disp; di++)
        x = allpass_tick(x, s.a_disp, s.disp_st[di]);

    // Tuning allpass
    x = allpass_tick(x, s.ap_a, s.ap_tune);

    // 3. Nut reflection: rigid termination (-1)
    float nut_ref = -s.lower_at(0);

    // 4. Shift upper → (new sample at position 0)
    s.shift_upper();
    s.upper_at(0) = nut_ref;

    // 5. Shift lower ← (new sample at position M-1)
    s.shift_lower();
    s.lower_at(s.M - 1) = bridge_filter_tick(x, s.bridge);  // bridge admittance

    // 6. Inject hammer force at n0
    s.upper_at(s.n0) += hammer_in;
    s.lower_at(s.n0) += hammer_in;

    return bridge_out;
}

// ── Chaigne-Askenfelt hammer model ───────────────────────────────────────
//
// Physical parameters from Chaigne & Askenfelt (1994):
//   C2 (MIDI 36): Ms=35g,   L=1.9m,  Mh=4.9g,  T=750N, p=2.3, K=1e8
//   C4 (MIDI 60): Ms=3.93g, L=0.62m, Mh=2.97g, T=670N, p=2.5, K=4.5e9
//   C7 (MIDI 96): Ms=0.467g,L=0.09m, Mh=2.2g,  T=750N, p=3.0, K=1e11

namespace hammer {

struct AnchorParams {
    float Ms_g, L_m, Mh_g, T_N, p_exp, K_stiff;
};

static constexpr AnchorParams ANCHOR_C2 = { 35.0f,  1.90f, 4.9f,  750.f, 2.3f, 1e8f  };
static constexpr AnchorParams ANCHOR_C4 = { 3.93f,  0.62f, 2.97f, 670.f, 2.5f, 4.5e9f };
static constexpr AnchorParams ANCHOR_C7 = { 0.467f, 0.09f, 2.2f,  750.f, 3.0f, 1e11f };

inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline float log_lerp(float a, float b, float t) {
    return std::pow(10.f, lerp(std::log10(a), std::log10(b), t));
}

/// Interpolate hammer parameter from 3 anchor notes.
inline AnchorParams interp_params(int midi) {
    AnchorParams r;
    if (midi <= 36) {
        r = ANCHOR_C2;
    } else if (midi <= 60) {
        float t = (float)(midi - 36) / 24.f;
        r.Ms_g    = lerp(ANCHOR_C2.Ms_g,    ANCHOR_C4.Ms_g,    t);
        r.L_m     = lerp(ANCHOR_C2.L_m,     ANCHOR_C4.L_m,     t);
        r.Mh_g    = lerp(ANCHOR_C2.Mh_g,    ANCHOR_C4.Mh_g,    t);
        r.T_N     = lerp(ANCHOR_C2.T_N,     ANCHOR_C4.T_N,     t);
        r.p_exp   = lerp(ANCHOR_C2.p_exp,   ANCHOR_C4.p_exp,   t);
        r.K_stiff = log_lerp(ANCHOR_C2.K_stiff, ANCHOR_C4.K_stiff, t);
    } else if (midi <= 96) {
        float t = (float)(midi - 60) / 36.f;
        r.Ms_g    = lerp(ANCHOR_C4.Ms_g,    ANCHOR_C7.Ms_g,    t);
        r.L_m     = lerp(ANCHOR_C4.L_m,     ANCHOR_C7.L_m,     t);
        r.Mh_g    = lerp(ANCHOR_C4.Mh_g,    ANCHOR_C7.Mh_g,    t);
        r.T_N     = lerp(ANCHOR_C4.T_N,     ANCHOR_C7.T_N,     t);
        r.p_exp   = lerp(ANCHOR_C4.p_exp,   ANCHOR_C7.p_exp,   t);
        r.K_stiff = log_lerp(ANCHOR_C4.K_stiff, ANCHOR_C7.K_stiff, t);
    } else {
        r = ANCHOR_C7;
    }
    return r;
}

/// Compute hammer force using Chaigne-Askenfelt finite-difference model.
///
/// Runs a short FD simulation (~3-7ms) of nonlinear hammer-string
/// interaction. The force is converted to velocity input (F / 2Z)
/// ready for waveguide injection.
///
/// Velocity-dependent felt hardness:
///   Real piano felt compresses more at forte → effective stiffness
///   and nonlinearity exponent increase with velocity.
///   K_eff = K_base × (1 + vel_hardening × vel_norm)
///   p_eff = p_base + vel_hardening_p × vel_norm
///   where vel_norm = v0 / V_MAX (0..1)
///
/// Args:
///   midi    — MIDI note (for parameter interpolation)
///   v0      — initial hammer velocity (m/s)
///   exc_x0  — striking position (fraction of string)
///   sr      — sample rate
///   v_in    — output buffer (at least MAX_HAMMER_SAMPLES)
///
/// Returns: number of samples written to v_in
inline int compute_force(int midi, float v0, float exc_x0, float sr,
                         float* v_in,
                         float K_hardening = 1.5f,
                         float p_hardening = 0.3f) {
    AnchorParams ap = interp_params(midi);

    // Velocity-dependent felt hardness
    static constexpr float V_MAX = 6.0f;
    float vel_norm = (std::min)(v0 / V_MAX, 1.f);
    ap.K_stiff *= (1.f + K_hardening * vel_norm);
    ap.p_exp   += p_hardening * vel_norm;

    float Ms = ap.Ms_g * 0.001f;       // g → kg
    float L  = ap.L_m;
    float Mh = ap.Mh_g * 0.001f;       // g → kg
    float T  = ap.T_N;
    float p  = ap.p_exp;
    float K  = ap.K_stiff;

    // Damping and stiffness (Chaigne & Askenfelt constants)
    static constexpr float b1 = 0.5f;
    static constexpr float b3 = 6.25e-9f;
    static constexpr float epsilon = 3.82e-5f;

    float rho_L = Ms / L;              // linear density
    float R0 = std::sqrt(T * rho_L);   // wave impedance
    float c  = std::sqrt(T / rho_L);   // wave speed

    // Spatial grid: find largest stable N for stiff string
    // Courant: r ≤ 1/sqrt(1 + 4*epsilon*N²)
    int N = 15;
    for (int N_try = 120; N_try >= 15; N_try--) {
        float r_try = c * (float)N_try / (sr * L);
        float r_max = 1.f / std::sqrt(1.f + 4.f * epsilon * (float)(N_try * N_try));
        if (r_try <= 0.9f * r_max) { N = N_try; break; }
    }

    int i0 = std::max(2, std::min(N - 3, (int)std::round(exc_x0 * (float)N)));
    float dt = 1.f / sr;
    float dt2 = dt * dt;

    // FD coefficients (Teng Appendix II)
    float D  = 1.f + b1 / sr + 2.f * b3 * sr;
    float r  = c * (float)N / (sr * L);
    float a1 = (2.f - 2.f*r*r + b3*sr - 6.f*epsilon*(float)(N*N)*r*r) / D;
    float a2 = (-1.f + b1/sr + 2.f*b3*sr) / D;
    float a3 = (r*r * (1.f + 4.f*epsilon*(float)(N*N))) / D;
    float a4 = (b3*sr - epsilon*(float)(N*N)*r*r) / D;
    float a5 = (-b3*sr) / D;

    // Rolling buffer: 4 time slices (circular), max 120 grid points
    static constexpr int MAX_N = 120;
    float y[4][MAX_N] = {};     // y[slot][spatial]
    float yh[MAX_HAMMER_SAMPLES] = {};
    float F[MAX_HAMMER_SAMPLES]  = {};

    // t=0: all zero
    // t=1
    yh[1] = v0 * dt;
    for (int i = 1; i < N-1; i++)
        y[1][i] = (y[0][i+1] + y[0][i-1]) / 2.f;
    float delta = yh[1] - y[1][i0];
    if (delta > 0.f) F[1] = K * std::pow(delta, p);

    // t=2
    for (int i = 1; i < N-1; i++)
        y[2][i] = y[1][i+1] + y[1][i-1] - y[0][i];
    y[2][i0] += dt2 * (float)N * F[1] / Ms;
    yh[2] = 2.f*yh[1] - yh[0] - dt2 * F[1] / Mh;
    delta = yh[2] - y[2][i0];
    if (delta > 0.f) F[2] = K * std::pow(delta, p);

    // Main FD loop (t=3..max)
    int max_steps = std::min((int)(0.010f * sr), MAX_HAMMER_SAMPLES - 1);
    int actual_len = max_steps;
    int no_contact = 0;

    for (int n = 3; n < max_steps; n++) {
        // Circular time indices
        int cur  = n & 3;
        int prv  = (n-1) & 3;
        int prv2 = (n-2) & 3;
        int prv3 = (n-3) & 3;

        // Boundary
        y[cur][0]   = 0.f;
        y[cur][N-1] = 0.f;

        // Edge i=1
        y[cur][1] = a1*y[prv][1] + a2*y[prv2][1]
                  + a3*(y[prv][2] + y[prv][0])
                  + a4*(y[prv][3] - y[prv][1])
                  + a5*(y[prv2][2] + y[prv2][0] + y[prv3][1]);

        // Edge i=N-2
        y[cur][N-2] = a1*y[prv][N-2] + a2*y[prv2][N-2]
                    + a3*(y[prv][N-1] + y[prv][N-3])
                    + a4*(y[prv][N-4] - y[prv][N-2])
                    + a5*(y[prv2][N-1] + y[prv2][N-3] + y[prv3][N-2]);

        // Interior 2..N-3
        for (int i = 2; i <= N-3; i++) {
            y[cur][i] = a1*y[prv][i] + a2*y[prv2][i]
                      + a3*(y[prv][i+1] + y[prv][i-1])
                      + a4*(y[prv][i+2] + y[prv][i-2])
                      + a5*(y[prv2][i+1] + y[prv2][i-1] + y[prv3][i]);
        }

        // Striking point (force injection)
        y[cur][i0] = a1*y[prv][i0] + a2*y[prv2][i0]
                   + a3*(y[prv][i0+1] + y[prv][i0-1])
                   + a4*(y[prv][i0+2] + y[prv][i0-2])
                   + a5*(y[prv2][i0+1] + y[prv2][i0-1] + y[prv3][i0])
                   + dt2 * (float)N * F[n-1] / Ms;

        // Hammer displacement (Verlet)
        yh[n] = 2.f*yh[n-1] - yh[n-2] - dt2 * F[n-1] / Mh;

        // Nonlinear felt compression
        delta = yh[n] - y[cur][i0];
        if (delta > 0.f) {
            F[n] = K * std::pow(delta, p);
            no_contact = 0;
        } else {
            F[n] = 0.f;
            no_contact++;
        }

        if (no_contact > 100) { actual_len = n + 1; break; }
    }

    // Convert force → velocity input for waveguide
    float inv_2R0 = 1.f / (2.f * R0);
    for (int i = 0; i < actual_len; i++)
        v_in[i] = F[i] * inv_2R0;

    return actual_len;
}

} // namespace hammer

// ── Multi-string detuning ────────────────────────────────────────────────

struct StringDetuning {
    float f0s[MAX_STRINGS] = {};
    float pan_l[MAX_STRINGS] = {};
    float pan_r[MAX_STRINGS] = {};
    int   count = 1;
};

/// Compute detuned frequencies and stereo panning for multi-string group.
inline StringDetuning compute_detuning(int n_strings, float detune_cents,
                                        float f0, float stereo_spread) {
    StringDetuning sd;
    sd.count = std::max(1, std::min(n_strings, MAX_STRINGS));

    for (int si = 0; si < sd.count; si++) {
        // Detune
        if (sd.count > 1) {
            float offset = ((float)si - (float)(sd.count - 1) / 2.f) * detune_cents;
            sd.f0s[si] = f0 * std::pow(2.f, offset / 1200.f);
        } else {
            sd.f0s[si] = f0;
        }

        // Pan (cos/sin equal-power)
        float pan;
        if (sd.count > 1) {
            float spread_norm = ((float)si - (float)(sd.count - 1) / 2.f)
                              / ((float)(sd.count - 1) / 2.f);
            pan = 0.5f + stereo_spread * spread_norm * 0.5f;
        } else {
            pan = 0.5f;
        }
        sd.pan_l[si] = std::cos(pan * dsp::PI / 2.f);
        sd.pan_r[si] = std::sin(pan * dsp::PI / 2.f);
    }
    return sd;
}

// ── Keyboard mapping helpers ─────────────────────────────────────────────

/// Inharmonicity B (physics-based defaults for dual-rail).
inline float default_B(int midi) {
    if (midi <= 48)
        return hammer::lerp(2e-3f, 1e-3f, (float)(midi - 21) / 27.f);
    else if (midi <= 72)
        return hammer::lerp(1e-3f, 4e-4f, (float)(midi - 48) / 24.f);
    else
        return hammer::lerp(4e-4f, 5e-5f, (float)(midi - 72) / 36.f);
}

/// String gauge (thickness multiplier).
inline float default_gauge(int midi) {
    if (midi <= 48)
        return hammer::lerp(3.0f, 2.0f, (float)(midi - 21) / 27.f);
    else if (midi <= 72)
        return hammer::lerp(2.0f, 1.2f, (float)(midi - 48) / 24.f);
    else
        return hammer::lerp(1.2f, 0.8f, (float)(midi - 72) / 36.f);
}

/// String count per note.
inline int default_n_strings(int midi) {
    return (midi <= 27) ? 1 : (midi <= 48) ? 2 : 3;
}

/// Detuning in cents.
inline float default_detune_cents(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 2.5f - t * 2.2f;
}

/// MIDI velocity to hammer velocity (m/s).
///   vel 0..1 → v0 0.5..6.0 m/s
inline float velocity_to_v0(float vel_norm) {
    return std::max(0.5f, vel_norm * 6.f);
}

} // namespace physics
