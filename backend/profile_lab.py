"""Measures the per-customer behavioural profile layer (profiles.py).

Writes docs/profile-evaluation.md. Spec section 10.2.

    python profile_lab.py                          # synthetic + browser-lab stages
    python profile_lab.py --identities 20 --markdown /tmp/x.md     # quick smoke
    python profile_lab.py --latency-db postgresql+asyncpg://...profile_lab...
    python profile_lab.py --latency-only --latency-db URL --latency-label container \\
        --latency-json out.json                    # latency in another topology
    python profile_lab.py --latency-from out.json  # fold that run into the report

Run it under the backend lock: it loads the served model bundle (read only --
this script never writes a model file) so that every feature value is on the
scale the served API stores in behavior_data.

WHAT THIS MEASURES, AND WHAT IT CANNOT. There is no real customer data anywhere
in this project. Everything below is measured on:

  * SYNTHETIC IDENTITIES from train_model.simulate_identity_sessions(): a person
    is a draw of the human persona's parameters, and each of their sessions adds
    per-session noise at an ASSUMED ratio (train_model.WITHIN_IDENTITY_SD_RATIO).
    A synthetic person is a generator with fixed parameters, so their sessions
    are self-consistent in a way no real person is -- device, posture, time of
    day, hurry, which hand holds the phone. Every within-person variance here is
    too small, and conformal calibration only holds while sessions are
    exchangeable, which real drift breaks. So every false-challenge rate this
    script reports is a LOWER BOUND, biased in the direction that flatters the
    product.
  * The browser lab's lab/real_telemetry.json, which is SCRIPTED Playwright
    traffic grouped by run -- not one person across many sessions -- and which
    has too few runs per scenario for a profile ever to mature.

The "impostor" rate (identity B's sessions against identity A's profile) is the
only true positive this data can define, and it is a statement about how far
apart the generator puts two synthetic people, not about fraud.

Choices this script makes from data, each by a rule fixed here BEFORE the data
is looked at (the rules are printed into the report next to the result):

  PROFILE_MIN_FEATURE_OBS  the knee (largest distance below the chord, both axes
                           normalised) of the relative RMS error of the
                           1.4826*MAD scale estimate against observation count,
                           n = 2 .. PROFILE_MIN_SESSIONS.
  PROFILE_SCALE_FLOOR      the 5th percentile of the NON-ZERO per-feature
                           1.4826*MAD over synthetic 20-session reference sets
                           (features measured in >= PROFILE_MIN_FEATURE_OBS of
                           them), rounded DOWN to two significant figures. Zero
                           scales are left out of the percentile because a
                           floor of zero is the flooring failure the constant
                           exists to prevent (z = x / 0), and zero spreads are
                           excluded by any positive floor anyway.
  PROFILE_TOP_K            among K in {1, 2, 3, 12} at PROFILE_ALPHA, the K with
                           the highest impostor escalation rate; kept at 3 unless
                           that K beats K=3 with a paired cluster-bootstrap 95%
                           interval (resampling identities) that excludes zero.
                           3 is the default because it is also what the SOC
                           panel can show a human reviewer.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BACKEND_DIR)
DEFAULT_MARKDOWN = os.path.join(REPO_DIR, "docs", "profile-evaluation.md")
LAB_TELEMETRY = os.path.join(REPO_DIR, "lab", "real_telemetry.json")

# The sentence every figure in the report carries, verbatim (spec section 10).
SYNTHETIC_SENTENCE = "Sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur."
LAB_SENTENCE = (
    "Betikle yürütülen Playwright tarayıcı trafiği üzerinde ölçüldü (tek bir kişinin "
    "çok sayıda oturumu değil); gerçek müşteri verisi yoktur."
)
LOWER_BOUND = (
    "Every false-challenge figure is a **lower bound**: a synthetic identity is more "
    "self-consistent than any real person, so real customers will be challenged more often."
)

SEED_IDENTITIES = 20260916
SEED_BOTS = 20260917
SEED_RATIO = 20260918
SEED_BOOTSTRAP = 20260919
SEED_KNEE = 20260920

CANDIDATES_PER_IDENTITY = 20
# Reference sessions collected per identity and modality: enough for the
# alpha = 0.02 row of the sensitivity table, whose derived maturity is 49.
REFERENCE_POOL = 50
REFERENCE_ATTEMPT_CAP = 80
TOP_K_GRID = (1, 2, 3, 12)
ALPHA_GRID = (0.02, 0.05, 0.10)
POISON_GRID = (0, 1, 2, 4, 8, 10)
# Attacker sessions learned ON PROBATION, i.e. each one a passed step-up. 6 is
# past PROFILE_PROBATION_MAX, so the storage cap itself is exercised too.
PROBATION_GRID = (0, 1, 2, 4, 6)
RATIO_GRID = (0.3, 0.6, 1.0)
KNEE_SUBSETS = 60
BOOTSTRAP = 2000

# The starting values spec section 6.3 gave these three constants before any
# measurement. Fixed here rather than read from profiles.py, so that the
# "starting value against measured value" comparison still means something
# after profiles.py carries the measured values.
SPEC_STARTING_VALUES = {"PROFILE_MIN_FEATURE_OBS": 8, "PROFILE_SCALE_FLOOR": 0.02, "PROFILE_TOP_K": 3}

# The BEFORE column of the report's before/after table: what this script
# published on 2026-09-16, copied from that page, NOT re-run. That code ranked a
# session with a leave-one-out rank and counted probation vectors as references;
# both were changed afterwards because of what that page measured. Same seeds,
# so the synthetic identities are the same ones -- write_markdown checks that
# through the rank-independent section 4 statistics before it says so.
BEFORE_S4 = {
    "date": "2026-09-16",
    "code": "leave-one-out rank; probation vectors counted as references",
    "constants": (8, 0.0062, 3),
    "scale_floor": {"total": 3547, "zero": 698, "p5_positive": "0.00630"},
    "same": {"mouse": "6.0% (4.7%–7.5%)", "keyboard": "2.9% (2.2%–3.7%)"},
    "same_abstain": {"mouse": "0.0%", "keyboard": "21.5%"},
    "different": {"mouse": "48.1% (44.6%–51.6%)", "keyboard": "23.5% (20.4%–26.7%)"},
    # Section 7 then: k attacker vectors counted as references.
    "poisoning": {0: ("48.5% (43.8%–53.3%)", "0.0952"), 1: ("25.0% (21.4%–28.7%)", "0.0952"),
                  4: ("12.5% (10.2%–15.2%)", "0.2381")},
    # Section 5's calibration check then: the shipped (leave-one-out) rank
    # against full conformal on the same sessions, paired over identities.
    "calibration": {
        "mouse": "leave-one-out 6.0% against full conformal 4.9%: +1.2 pp (95% +0.2 … +2.4)",
        "keyboard": "leave-one-out 2.9% against full conformal 3.8%: -0.9 pp (95% -1.4 … -0.4)",
    },
    # Section 9 then: an exact replay of each identity's first learned session
    # (200 mouse profiles), escalated and p_value_low <= 0.05.
    "replay": ("1.5%", "8.5%"),
    # Section 11 then, keyed by the latency run label; p50 / p95 in ms, in
    # LATENCY_CONFIGS order.
    "latency": {
        "container, docker-compose topology": ((5.1, 7.1), (23.7, 29.9), (15.8, 19.1), (23.7, 29.2)),
        "Windows host via Docker Desktop port mapping": ((12.2, 14.3), (42.4, 48.7), (29.0, 32.8), (42.7, 46.9)),
    },
}


def _setup_imports():
    # sklearn's classification report and the Turkish sentences need UTF-8 on a
    # Windows console, which defaults to cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)


# --- small statistics helpers -------------------------------------------------


def median(values):
    return float(np.median(np.asarray(values, dtype=float)))


def pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{100.0 * x:.{digits}f}%"


def cluster_rate(hits, totals, rng, b: int = BOOTSTRAP):
    """Pooled rate with a 95% cluster-bootstrap interval, resampling IDENTITIES:
    one person's sessions are correlated, so a per-session binomial interval
    would be too narrow."""
    hits = np.asarray(hits, dtype=float)
    totals = np.asarray(totals, dtype=float)
    if totals.sum() == 0:
        return None, None, None
    point = hits.sum() / totals.sum()
    idx = rng.integers(0, len(hits), size=(b, len(hits)))
    boot = hits[idx].sum(axis=1) / np.maximum(totals[idx].sum(axis=1), 1.0)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(point), float(lo), float(hi)


def rate_cell(hits, totals, rng) -> str:
    point, lo, hi = cluster_rate(hits, totals, rng)
    if point is None:
        return "–"
    return f"{pct(point)} ({pct(lo)}–{pct(hi)})"


def auc_lower_is_positive(positives, negatives) -> float | None:
    """P(a positive's statistic < a negative's), ties counted half. Used with
    p_high, where smaller means more surprising."""
    pos = np.sort(np.asarray(positives, dtype=float))
    neg = np.sort(np.asarray(negatives, dtype=float))
    if not len(pos) or not len(neg):
        return None
    less = np.searchsorted(neg, pos, side="right")  # negatives <= each positive
    strictly_less = np.searchsorted(neg, pos, side="left")  # negatives < each positive
    greater = len(neg) - less
    ties = less - strictly_less
    return float((greater + 0.5 * ties).sum() / (len(pos) * len(neg)))


def knee(xs, ys) -> int:
    """Largest distance below the chord, both axes normalised to [0, 1]."""
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    xn = (x - x[0]) / (x[-1] - x[0])
    span = y.max() - y.min()
    yn = (y - y.min()) / span if span > 0 else np.zeros_like(y)
    chord = yn[0] + (yn[-1] - yn[0]) * xn
    return int(x[int(np.argmax(chord - yn))])


def floor_two_significant(value: float) -> float:
    if value <= 0:
        raise ValueError("olcek tabani pozitif olmali")
    step = 10.0 ** (math.floor(math.log10(value)) - 1)
    return round(math.floor(value / step) * step, 12)


# --- synthetic identities ---------------------------------------------------------


@dataclass
class Person:
    traits: dict
    refs: dict = field(default_factory=dict)  # modality -> [vec], simulation order
    ref_attempts: dict = field(default_factory=dict)  # modality -> sessions simulated
    to_mature: dict = field(default_factory=dict)  # modality -> sessions until 19 vectors
    cands: dict = field(default_factory=dict)  # modality -> [vec | None]
    ref_lists: dict = field(default_factory=dict)  # (modality, n) -> fixed list object


class Lab:
    def __init__(self, args):
        _setup_imports()
        import profiles  # noqa: E402
        import scorer  # noqa: E402
        import train_model  # noqa: E402
        from lstm_model import FEATURE_NAMES  # noqa: E402

        self.args = args
        self.profiles = profiles
        self.scorer = scorer
        self.train_model = train_model
        self.FEATURE_NAMES = FEATURE_NAMES
        self.results: dict = {"meta": {}, "checks": {}}
        self.code_constants = {
            "PROFILE_MIN_FEATURE_OBS": profiles.PROFILE_MIN_FEATURE_OBS,
            "PROFILE_SCALE_FLOOR": profiles.PROFILE_SCALE_FLOOR,
            "PROFILE_TOP_K": profiles.PROFILE_TOP_K,
            "PROFILE_ALPHA": profiles.PROFILE_ALPHA,
            "PROFILE_MIN_SESSIONS": profiles.PROFILE_MIN_SESSIONS,
            "PROFILE_BUFFER_MAX": profiles.PROFILE_BUFFER_MAX,
            "PROFILE_PROBATION_MAX": profiles.PROFILE_PROBATION_MAX,
            "WITHIN_IDENTITY_SD_RATIO": train_model.WITHIN_IDENTITY_SD_RATIO,
        }

    # -- plumbing ---------------------------------------------------------------

    def log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    @contextmanager
    def settings(self, **values):
        saved = {name: getattr(self.profiles, name) for name in values}
        for name, value in values.items():
            setattr(self.profiles, name, value)
        try:
            yield
        finally:
            for name, value in saved.items():
                setattr(self.profiles, name, value)

    def refs(self, person: Person, modality: str, n: int) -> list:
        """One list OBJECT per (person, modality, n): the same references for
        every setting a stage compares."""
        key = (modality, n)
        if key not in person.ref_lists:
            person.ref_lists[key] = list(person.refs[modality][:n])
        return person.ref_lists[key]

    # -- feature path, identical to the served one ------------------------------------

    def flush_row(self, payload: dict) -> dict:
        """One behavior_data row as /api/analyze stores it: the normalised
        features under the SERVED bundle's scaling and fallbacks, plus
        measured_mask exactly as scorer.compute_risk derives it."""
        raw = self.scorer.extract_raw(payload)
        features = self.scorer.extract_features(payload, raw)
        mask = sum(
            1 << i
            for i, name in enumerate(self.FEATURE_NAMES)
            if raw.get(name) is not None and math.isfinite(raw[name])
        )
        return {"measured_mask": mask, **features}

    def session_vector(self, windows: list[dict]):
        built = self.profiles.session_vector([self.flush_row(w) for w in windows])
        return None if built is None else built["vec"]

    @staticmethod
    def lab_modality(windows: list[dict]) -> str:
        """The simulator emits no client_signals. A session is `mouse` when any
        flush carries a pointer trajectory, otherwise `keyboard` -- which is
        what the SDK's pointer counters would say for these payloads: a
        headless element.click() fires no pointer event."""
        return "mouse" if any(w.get("mouse_trajectory") for w in windows) else "keyboard"

    def check_fidelity(self) -> None:
        """flush_row() must equal what compute_risk() hands /api/analyze."""
        tm = self.train_model
        tm.rng = np.random.default_rng(1)
        person = tm._identity_traits()
        windows = tm.simulate_identity_sessions(person, 2, "mouse")[0] + tm.simulate_identity_sessions(person, 2, "keyboard")[0]
        windows += tm.simulate_session_windows("bot", 1_700_000_000_000)
        mismatches = 0
        for payload in windows:
            row = self.flush_row(payload)
            served = self.scorer.compute_risk(payload)
            if row["measured_mask"] != served["measured_mask"] or any(
                row[name] != served["features"][name] for name in self.FEATURE_NAMES
            ):
                mismatches += 1
        self.results["checks"]["fidelity"] = {"flushes": len(windows), "mismatches": mismatches}
        if mismatches:
            raise SystemExit(f"flush_row compute_risk ile uyusmuyor ({mismatches} flush)")
        self.log(f"fidelity: {len(windows)} flushes equal to compute_risk")

    # -- simulation ---------------------------------------------------------------

    def simulate_person(self, traits: dict, pool: int = REFERENCE_POOL, candidates: int = CANDIDATES_PER_IDENTITY) -> Person:
        tm = self.train_model
        person = Person(traits=traits)
        for modality in tm.IDENTITY_MODALITIES:
            refs, attempts, to_mature = [], 0, None
            while len(refs) < pool and attempts < REFERENCE_ATTEMPT_CAP:
                windows = tm.simulate_identity_sessions(traits, 1, modality)[0]
                assert self.lab_modality(windows) == modality
                attempts += 1
                vec = self.session_vector(windows)
                if vec is not None:
                    refs.append(vec)
                    if to_mature is None and len(refs) == self.profiles.PROFILE_MIN_SESSIONS:
                        to_mature = attempts
            person.refs[modality] = refs
            person.ref_attempts[modality] = attempts
            person.to_mature[modality] = to_mature
            person.cands[modality] = [
                self.session_vector(w) for w in tm.simulate_identity_sessions(traits, candidates, modality)
            ]
        return person

    def simulate_people(self, n: int, seed: int, pool: int = REFERENCE_POOL) -> list[Person]:
        tm = self.train_model
        tm.rng = np.random.default_rng(seed)
        people = []
        started = time.time()
        for index in range(n):
            people.append(self.simulate_person(tm._identity_traits(), pool=pool))
            if (index + 1) % 25 == 0:
                self.log(f"  simulated {index + 1}/{n} identities ({time.time() - started:.0f}s)")
        return people

    # -- evaluation --------------------------------------------------------------------

    def impostors(self, people: list[Person], index: int, modality: str) -> list:
        """Candidate j of a DIFFERENT person for each j: twenty different people
        presented against one profile, never the profile's own person."""
        n = len(people)
        out = []
        for j in range(CANDIDATES_PER_IDENTITY):
            other = (index + 1 + (j % (n - 1))) % n
            out.append(people[other].cands[modality][j])
        return out

    def evaluate_block(self, people, modality, ref_n, alpha, *, refs_for=None, impostors_for=None, same_for=None):
        """Same-person and impostor verdicts for every person in one setting.

        Returns per-person escalation counts (for the cluster bootstrap), state
        counters, and the p-values of evaluated verdicts (for the AUC)."""
        p = self.profiles
        out = {
            "same_hits": [], "same_totals": [], "same_eval": [],
            "imp_hits": [], "imp_totals": [], "imp_eval": [],
            "same_states": Counter(), "imp_states": Counter(),
            "same_p": [], "imp_p": [],
        }
        for index, person in enumerate(people):
            refs = refs_for(person, index) if refs_for else self.refs(person, modality, ref_n)
            same = same_for(person, index) if same_for else person.cands[modality]
            imps = impostors_for(person, index) if impostors_for else self.impostors(people, index, modality)
            for kind, candidates in (("same", same), ("imp", imps)):
                hits = evaluated = 0
                for cand in candidates:
                    verdict = p.evaluate_profile(cand, refs, modality=modality, alpha=alpha)
                    out[f"{kind}_states"][verdict.state] += 1
                    if verdict.state == p.STATE_EVALUATED:
                        evaluated += 1
                        out[f"{kind}_p"].append(verdict.p_value)
                    hits += int(verdict.escalate)
                out[f"{kind}_hits"].append(hits)
                out[f"{kind}_totals"].append(len(candidates))
                out[f"{kind}_eval"].append(evaluated)
        return out

    def check_port(self, people) -> None:
        """profiles.evaluate_profile (the shipped full-conformal rank) must give
        the verdicts this script's own, independently written full-conformal
        implementation gives (_full_conformal): escalate, or abstain. Compared
        on real lab data before any number is reported."""
        p = self.profiles
        rng = np.random.default_rng(3)
        mismatches = cases = 0
        for _ in range(150):
            person = people[int(rng.integers(0, len(people)))]
            modality = ("mouse", "keyboard")[int(rng.integers(0, 2))]
            cand = person.cands[modality][int(rng.integers(0, CANDIDATES_PER_IDENTITY))]
            refs = self.refs(person, modality, 20)
            verdict = p.evaluate_profile(cand, refs, modality=modality)
            shipped = verdict.escalate if verdict.state == p.STATE_EVALUATED else None
            mismatches += int(shipped != self._full_conformal(cand, refs, p.PROFILE_ALPHA))
            cases += 1
        self.results["checks"]["port"] = {"cases": cases, "mismatches": mismatches}
        if mismatches:
            raise SystemExit(f"profiles.evaluate_profile bagimsiz tam konformal uygulamayla uyusmuyor ({mismatches})")
        self.log(f"port check: {cases} verdicts identical to the independent full-conformal implementation")

    # -- stages ----------------------------------------------------------------------

    def stage_min_feature_obs(self, people) -> int:
        """Spread of the per-feature median / 1.4826*MAD estimate against n."""
        p = self.profiles
        rng = np.random.default_rng(SEED_KNEE)
        ns = list(range(2, p.PROFILE_MIN_SESSIONS + 1))
        rows = {m: {n: {"centre": [], "scale": []} for n in ns} for m in self.train_model.IDENTITY_MODALITIES}
        used = Counter()
        for person in people:
            for modality in self.train_model.IDENTITY_MODALITIES:
                pool = person.refs[modality] + [c for c in person.cands[modality] if c is not None]
                for name in self.FEATURE_NAMES:
                    values = np.array([v[name] for v in pool if v.get(name) is not None], dtype=float)
                    if len(values) < 3 * p.PROFILE_MIN_SESSIONS // 2:
                        continue
                    centre = float(np.median(values))
                    scale = 1.4826 * float(np.median(np.abs(values - centre)))
                    if scale <= 0:
                        continue
                    used[modality] += 1
                    order = np.argsort(rng.random((KNEE_SUBSETS, len(values))), axis=1)
                    for n in ns:
                        subsets = values[order[:, :n]]
                        c_hat = np.median(subsets, axis=1)
                        s_hat = 1.4826 * np.median(np.abs(subsets - c_hat[:, None]), axis=1)
                        rows[modality][n]["centre"].append(float(np.sqrt(np.mean(((c_hat - centre) / scale) ** 2))))
                        rows[modality][n]["scale"].append(float(np.sqrt(np.mean(((s_hat - scale) / scale) ** 2))))
        table = []
        pooled_scale = []
        for n in ns:
            entry = {"n": n}
            for modality in self.train_model.IDENTITY_MODALITIES:
                entry[f"{modality}_centre"] = median(rows[modality][n]["centre"]) if rows[modality][n]["centre"] else None
                entry[f"{modality}_scale"] = median(rows[modality][n]["scale"]) if rows[modality][n]["scale"] else None
            all_scale = rows["mouse"][n]["scale"] + rows["keyboard"][n]["scale"]
            all_centre = rows["mouse"][n]["centre"] + rows["keyboard"][n]["centre"]
            entry["pooled_scale"] = median(all_scale)
            entry["pooled_centre"] = median(all_centre)
            pooled_scale.append(entry["pooled_scale"])
            table.append(entry)
        chosen = knee(ns, pooled_scale)
        self.results["min_feature_obs"] = {"table": table, "knee": chosen, "series": dict(used)}
        self.log(f"PROFILE_MIN_FEATURE_OBS knee = {chosen}")
        return chosen

    def stage_scale_floor(self, people, min_obs: int) -> float:
        p = self.profiles
        per_feature = {m: {name: [] for name in self.FEATURE_NAMES} for m in self.train_model.IDENTITY_MODALITIES}
        positive = []
        zero = total = 0
        for person in people:
            for modality in self.train_model.IDENTITY_MODALITIES:
                refs = self.refs(person, modality, p.PROFILE_BUFFER_MAX)
                stats = p.feature_stats(refs)
                for name in self.FEATURE_NAMES:
                    stat = stats[name]
                    if stat.n_obs < min_obs:
                        continue
                    per_feature[modality][name].append(stat.scale)
                    total += 1
                    if stat.scale > 0:
                        positive.append(stat.scale)
                    else:
                        zero += 1
        p5 = float(np.percentile(positive, 5))
        floor = floor_two_significant(p5)
        table = []
        for modality in self.train_model.IDENTITY_MODALITIES:
            for name in self.FEATURE_NAMES:
                values = np.asarray(per_feature[modality][name], dtype=float)
                if not len(values):
                    continue
                table.append({
                    "modality": modality,
                    "feature": name,
                    "sets": int(len(values)),
                    "zero": float(np.mean(values == 0)),
                    "p5": float(np.percentile(values, 5)),
                    "p50": float(np.percentile(values, 50)),
                    "below_old": float(np.mean(values < SPEC_STARTING_VALUES["PROFILE_SCALE_FLOOR"])),
                    "below_new": float(np.mean(values < floor)),
                })
        self.results["scale_floor"] = {
            "p5_positive": p5, "floor": floor, "zero": zero, "total": total, "table": table,
        }
        self.log(f"PROFILE_SCALE_FLOOR: p5 of positive scales {p5:.5f} -> {floor}")
        return floor

    def stage_grid(self, people, min_obs: int, floor: float) -> None:
        """The alpha x K sensitivity table. Maturity follows alpha."""
        grid = {}
        for alpha in ALPHA_GRID:
            min_sessions = math.ceil(1 / alpha) - 1
            ref_n = max(self.profiles.PROFILE_BUFFER_MAX, min_sessions + 1)
            for k in TOP_K_GRID:
                started = time.time()
                with self.settings(PROFILE_TOP_K=k, PROFILE_MIN_SESSIONS=min_sessions,
                                   PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
                    for modality in self.train_model.IDENTITY_MODALITIES:
                        grid[(alpha, k, modality)] = self.evaluate_block(people, modality, ref_n, alpha)
                self.log(f"  grid alpha={alpha} K={k} ({time.time() - started:.0f}s)")
        self.grid = grid
        self.results["grid_meta"] = {
            str(alpha): {"min_sessions": math.ceil(1 / alpha) - 1,
                         "ref_n": max(self.profiles.PROFILE_BUFFER_MAX, math.ceil(1 / alpha))}
            for alpha in ALPHA_GRID
        }

    def choose_top_k(self, people) -> int:
        alpha = self.profiles.PROFILE_ALPHA
        rng = np.random.default_rng(SEED_BOOTSTRAP)

        def per_person(k):
            hits = np.zeros(len(people))
            totals = np.zeros(len(people))
            for modality in self.train_model.IDENTITY_MODALITIES:
                block = self.grid[(alpha, k, modality)]
                hits += np.asarray(block["imp_hits"], dtype=float)
                totals += np.asarray(block["imp_totals"], dtype=float)
            return hits, totals

        base_hits, base_totals = per_person(3)
        comparisons = {}
        best_k, best_rate = 3, base_hits.sum() / base_totals.sum()
        for k in TOP_K_GRID:
            hits, totals = per_person(k)
            rate = hits.sum() / totals.sum()
            idx = rng.integers(0, len(people), size=(BOOTSTRAP, len(people)))
            diff = hits[idx].sum(1) / totals[idx].sum(1) - base_hits[idx].sum(1) / base_totals[idx].sum(1)
            lo, hi = np.percentile(diff, [2.5, 97.5])
            comparisons[k] = {"rate": float(rate), "diff_lo": float(lo), "diff_hi": float(hi)}
            if k != 3 and rate > best_rate and lo > 0:
                best_k, best_rate = k, rate
        self.results["top_k"] = {"comparisons": comparisons, "chosen": best_k}
        self.log(f"PROFILE_TOP_K chosen = {best_k}")
        return best_k

    def stage_provisional_comparison(self, people, min_obs, floor, top_k) -> None:
        """The spec's starting constants against the measured ones, same data."""
        rows = {}
        settings = {
            "starting": (SPEC_STARTING_VALUES["PROFILE_MIN_FEATURE_OBS"], SPEC_STARTING_VALUES["PROFILE_SCALE_FLOOR"], SPEC_STARTING_VALUES["PROFILE_TOP_K"]),
            "measured": (min_obs, floor, top_k),
        }
        for label, (obs, flr, k) in settings.items():
            with self.settings(PROFILE_TOP_K=k, PROFILE_MIN_SESSIONS=19, PROFILE_MIN_FEATURE_OBS=obs, PROFILE_SCALE_FLOOR=flr):
                for modality in self.train_model.IDENTITY_MODALITIES:
                    rows[(label, modality)] = self.evaluate_block(people, modality, 20, 0.05)
        self.provisional = rows
        self.results["provisional_settings"] = {k: list(v) for k, v in settings.items()}

    def _full_conformal(self, cand, refs, alpha):
        """Full conformal: each of the n+1 points -- the candidate included --
        is scored against the other n, with its own participating features.
        The score function is symmetric in the points, so under exchangeability
        the rank is exactly valid. Returns None when the layer would abstain."""
        p = self.profiles
        if cand is None:
            return None
        points = list(refs) + [cand]
        scores = []
        for j, point in enumerate(points):
            stats = p.feature_stats(points[:j] + points[j + 1:])
            feats = p.participating_features(point, stats)
            scores.append(p.deviation(point, stats, feats)[0] if len(feats) >= p.PROFILE_MIN_FEATURES else None)
        if scores[-1] is None:
            return None
        calibration = [s for s in scores[:-1] if s is not None]
        if len(calibration) < p.PROFILE_MIN_SESSIONS:
            return None
        p_high = (1 + sum(1 for s in calibration if s >= scores[-1])) / (len(calibration) + 1)
        return p_high <= alpha

    def stage_calibration_check(self, people, min_obs, floor, top_k) -> None:
        """The same candidates under the shipped rank (profiles.evaluate_profile)
        and under this script's own, independently written full-conformal
        implementation (_full_conformal), verdict for verdict.

        The shipped rank used to be leave-one-out: the candidate scored against
        all 20 references on its own participating features, each reference
        against the other 19 on the subset of the candidate's features it could
        be scored on -- not the same function, so the finite-sample guarantee
        was not exact. This check measured the gap (mouse 6.0% against 4.9%
        same-person challenges) and profiles.py now ships full conformal, so
        the two must now agree exactly; any disagreement is a porting bug, and
        is counted rather than bootstrapped."""
        rows = {}
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            for modality in self.train_model.IDENTITY_MODALITIES:
                counts = {"same_hits": [], "same_totals": [], "imp_hits": [], "imp_totals": [],
                          "same_abstain": 0, "imp_abstain": 0, "disagreements": 0, "verdicts": 0}
                for index, person in enumerate(people):
                    refs = self.refs(person, modality, 20)
                    for kind, candidates in (("same", person.cands[modality]), ("imp", self.impostors(people, index, modality))):
                        hits = 0
                        for cand in candidates:
                            verdict = self._full_conformal(cand, refs, 0.05)
                            hits += int(bool(verdict))
                            counts[f"{kind}_abstain"] += int(verdict is None)
                            shipped = self.profiles.evaluate_profile(cand, refs, modality=modality, alpha=0.05)
                            shipped = shipped.escalate if shipped.state == self.profiles.STATE_EVALUATED else None
                            counts["disagreements"] += int(shipped != verdict)
                            counts["verdicts"] += 1
                        counts[f"{kind}_hits"].append(hits)
                        counts[f"{kind}_totals"].append(len(candidates))
                rows[modality] = counts
        self.calibration = rows

    def stage_poisoning(self, people, min_obs, floor, top_k) -> None:
        """k of the victim's 20 reference slots hold the attacker's own session
        vectors, the k oldest of the victim's evicted to make room: what a
        PROMOTED attacker vector does (settled by the merchant, or healed), and
        what every probation vector did while probation vectors still counted."""
        rows = []
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            for k in POISON_GRID:
                poisoned_lists: dict[int, list] = {}

                def refs_for(person, index, k=k, poisoned_lists=poisoned_lists):
                    if index not in poisoned_lists:
                        attacker = people[(index + 1) % len(people)]
                        poisoned_lists[index] = attacker.refs["mouse"][:k] + person.refs["mouse"][k:20]
                    return poisoned_lists[index]

                def impostors_for(person, index):
                    return people[(index + 1) % len(people)].cands["mouse"]

                block = self.evaluate_block(people, "mouse", 20, 0.05, refs_for=refs_for, impostors_for=impostors_for)
                rows.append((k, block))
        self.poisoning = rows

    def shipped_references(self, stored: list[dict]) -> list:
        """The reference list main._read_profile_context reads from one stored
        (profile, modality) buffer, done in Python: disputed rows filtered out,
        ORDER BY probation ASC, created_at DESC, LIMIT PROFILE_BUFFER_MAX +
        PROFILE_PROBATION_MAX, then the rows profiles.is_reference accepts (not
        on probation), at most PROFILE_BUFFER_MAX of them."""
        p = self.profiles
        rows = [entry for entry in stored if entry.get("outcome") != "disputed"]
        rows.sort(key=lambda entry: entry["created_at"], reverse=True)
        rows.sort(key=lambda entry: bool(entry.get("probation")))  # stable: probation ASC, then newest first
        rows = rows[: p.PROFILE_BUFFER_MAX + p.PROFILE_PROBATION_MAX]
        return [entry["vec"] for entry in rows if p.is_reference(entry)][: p.PROFILE_BUFFER_MAX]

    @staticmethod
    def stored_entry(vec, day: int, probation: bool) -> dict:
        return {"vec": vec, "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
                "probation": probation, "outcome": "pending"}

    def victim_buffer(self, refs: list) -> list[dict]:
        """A victim's stored buffer holding `refs` as references, oldest first,
        admitted one by one through profiles.admit_vector."""
        kept: list[dict] = []
        for day, vec in enumerate(refs):
            kept, _ = self.profiles.admit_vector(kept, self.stored_entry(vec, day, False))
        return kept

    def stage_probation_poisoning(self, people, min_obs, floor, top_k) -> None:
        """The shipped rule for an attacker who passes step-up: k of his
        sessions learned ON PROBATION into the victim's buffer.

        Nothing here is taken from the code's intent. Each victim's buffer is
        built through the shipped storage function (profiles.admit_vector: the
        victim's 20 references, then the attacker's k vectors flagged
        probation), the reference set is read back by the decision path's rule
        (shipped_references), and the attacker's and the victim's fresh
        sessions are ranked against what comes back. The stage also counts, per
        k, how many victims' reference sets came back identical -- the same
        vector objects in the same order -- to the k = 0 set, which is what
        "probation vectors are not references" predicts."""
        p = self.profiles
        rows = []
        clean: dict[int, list] = {}
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            for k in PROBATION_GRID:
                ref_sets: dict[int, list] = {}
                identical = 0
                stored = set()
                for index, person in enumerate(people):
                    attacker = people[(index + 1) % len(people)]
                    kept = self.victim_buffer(person.refs["mouse"][:20])
                    for j, vec in enumerate(attacker.refs["mouse"][:k]):
                        kept, _ = p.admit_vector(kept, self.stored_entry(vec, 30 + j, True))
                    refs = self.shipped_references(kept)
                    if k == 0:
                        clean[index] = refs
                    ref_sets[index] = refs
                    identical += int(len(refs) == len(clean[index]) and all(a is b for a, b in zip(refs, clean[index])))
                    stored.add(sum(1 for entry in kept if entry["probation"]))
                block = self.evaluate_block(
                    people, "mouse", 20, 0.05,
                    refs_for=lambda person, index, ref_sets=ref_sets: ref_sets[index],
                    impostors_for=lambda person, index: people[(index + 1) % len(people)].cands["mouse"],
                )
                rows.append({"k": k, "block": block, "identical": identical, "victims": len(people),
                             "stored_probation": sorted(stored)})
        self.probation_poisoning = rows

    def stage_rescued_returning(self, people, min_obs, floor, top_k) -> None:
        """The accepted cost of excluding probation vectors, in the grandchild's
        terms: a DIFFERENT person pays on a mature profile, is challenged,
        passes step-up (the session is stored on probation), and comes back in
        the same pattern. How often is that next session challenged again?

        For every victim one fixed other identity plays the grandchild and
        presents its 20 fresh sessions in order. Every consecutive pair of them
        whose first session was challenged counts once: the first is stored as
        the rescued session, and the second is ranked against the reference set
        read back from that buffer. Two rules, same sessions:

          shipped  profiles.admit_vector with probation=True, read back by
                   shipped_references -- the rescued session is not a reference;
          counted  the rule before probation vectors were excluded: one buffer
                   of at most PROFILE_BUFFER_MAX, oldest evicted first, so the
                   rescued session replaces the victim's oldest reference.

        A synthetic person's sessions are independent draws around their
        traits, so conditioning on the first session having been challenged
        mostly selects grandchildren who behave far from the victim -- which is
        the population the question is about."""
        p = self.profiles
        rows = {}
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            for modality in self.train_model.IDENTITY_MODALITIES:
                out = {"first_hits": [], "first_totals": [], "shipped_hits": [], "counted_hits": [], "pairs": []}
                for index, person in enumerate(people):
                    refs = self.refs(person, modality, 20)
                    grandchild = people[(index + 1) % len(people)].cands[modality]
                    verdicts = [p.evaluate_profile(c, refs, modality=modality, alpha=0.05) for c in grandchild]
                    shipped_hits = counted_hits = pairs = 0
                    for j in range(len(grandchild) - 1):
                        if not verdicts[j].escalate:
                            continue
                        pairs += 1
                        buffer = self.victim_buffer(refs)
                        buffer, _ = p.admit_vector(buffer, self.stored_entry(grandchild[j], 30, True))
                        shipped = self.shipped_references(buffer)
                        shipped_hits += int(p.evaluate_profile(grandchild[j + 1], shipped, modality=modality, alpha=0.05).escalate)
                        counted = (list(refs) + [grandchild[j]])[-p.PROFILE_BUFFER_MAX:]
                        counted_hits += int(p.evaluate_profile(grandchild[j + 1], counted, modality=modality, alpha=0.05).escalate)
                    out["first_hits"].append(sum(int(v.escalate) for v in verdicts))
                    out["first_totals"].append(len(grandchild))
                    out["shipped_hits"].append(shipped_hits)
                    out["counted_hits"].append(counted_hits)
                    out["pairs"].append(pairs)
                rows[modality] = out
        self.rescued = rows

    def stage_cross_modality(self, people, min_obs, floor, top_k) -> None:
        rows = {}
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            # Production: the reference query filters on the candidate's
            # modality, so a customer with only a mouse history arriving on the
            # keyboard is compared against nothing.
            rows["production"] = self.evaluate_block(
                people, "keyboard", 20, 0.05, refs_for=lambda person, index: [],
                impostors_for=lambda person, index: [],
            )
            # Counterfactual 1: what pooling would do -- the other modality's
            # sessions ranked against this modality's references.
            for ref_m, cand_m in (("mouse", "keyboard"), ("keyboard", "mouse")):
                rows[f"pooled_{ref_m}_refs_{cand_m}_cands"] = self.evaluate_block(
                    people, ref_m, 20, 0.05,
                    same_for=lambda person, index, cand_m=cand_m: person.cands[cand_m],
                    impostors_for=lambda person, index, cand_m=cand_m: self.impostors(people, index, cand_m),
                )
            # Counterfactual 2: one mixed buffer, 10 sessions from each.
            mixed: dict[int, list] = {}

            def mixed_refs(person, index):
                if index not in mixed:
                    mixed[index] = person.refs["mouse"][:10] + person.refs["keyboard"][:10]
                return mixed[index]

            for cand_m in ("mouse", "keyboard"):
                rows[f"mixed_{cand_m}_cands"] = self.evaluate_block(
                    people, cand_m, 20, 0.05, refs_for=mixed_refs,
                    same_for=lambda person, index, cand_m=cand_m: person.cands[cand_m],
                    impostors_for=lambda person, index, cand_m=cand_m: self.impostors(people, index, cand_m),
                )
        self.cross = rows

    def stage_bots_and_replay(self, people, min_obs, floor, top_k, per_persona: int) -> None:
        tm = self.train_model
        p = self.profiles
        tm.rng = np.random.default_rng(SEED_BOTS)
        pick = np.random.default_rng(SEED_BOTS + 1)
        rows = []
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
            for persona in ("bot_sophisticated", "bot"):
                counts = Counter()
                for _ in range(per_persona):
                    base_t = 1_700_000_000_000 + int(tm.rng.integers(0, 10**9))
                    windows = tm.simulate_session_windows(persona, base_t)
                    modality = self.lab_modality(windows)
                    vec = self.session_vector(windows)
                    victim = people[int(pick.integers(0, len(people)))]
                    verdict = p.evaluate_profile(vec, self.refs(victim, modality, 20), modality=modality, alpha=0.05)
                    counts[(modality, "sessions")] += 1
                    counts[(modality, verdict.state)] += 1
                    counts[(modality, "escalated")] += int(verdict.escalate)
                rows.append((persona, counts))
            # Replay: a session the victim already taught the profile, sent
            # again under a new session id (the production query excludes only
            # the candidate's OWN session id, so the recorded twin stays in the
            # reference set).
            replay = Counter()
            p_low = []
            for person in people:
                refs = self.refs(person, "mouse", 20)
                verdict = p.evaluate_profile(dict(refs[0]), refs, modality="mouse", alpha=0.05)
                replay[verdict.state] += 1
                replay["escalated"] += int(verdict.escalate)
                if verdict.p_value_low is not None:
                    p_low.append(verdict.p_value_low)
                    replay["low_tail"] += int(verdict.p_value_low <= 0.05)
        self.bots = rows
        self.replay = (replay, p_low)

    def stage_ratio(self, n_people: int, min_obs, floor, top_k) -> None:
        tm = self.train_model
        rows = []
        saved = tm.WITHIN_IDENTITY_SD_RATIO
        try:
            for ratio in RATIO_GRID:
                tm.WITHIN_IDENTITY_SD_RATIO = ratio
                started = time.time()
                people = self.simulate_people(n_people, SEED_RATIO, pool=20)
                with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=min_obs, PROFILE_SCALE_FLOOR=floor):
                    for modality in tm.IDENTITY_MODALITIES:
                        rows.append((ratio, modality, self.evaluate_block(people, modality, 20, 0.05)))
                self.log(f"  ratio {ratio} ({time.time() - started:.0f}s)")
        finally:
            tm.WITHIN_IDENTITY_SD_RATIO = saved
        self.ratio = rows

    def stage_browser_lab(self, min_obs, floor, top_k) -> None:
        """Sanity pass over lab/real_telemetry.json, grouped by run.

        The file stores normalised features only (no raw telemetry, no
        measured_mask), normalised under the scaling in force at capture time.
        measured_mask is therefore INFERRED: in a fallback-eligible feature, the
        single most frequent exact value is a neutral fallback when it covers
        at least 10% of rows and is not a clip bound (0 or 1). The two counts
        are always measured."""
        p = self.profiles
        if not os.path.exists(LAB_TELEMETRY):
            self.browser = None
            return
        with open(LAB_TELEMETRY, encoding="utf-8") as fh:
            samples = json.load(fh)["samples"]
        fallbacks = {}
        for name in self.scorer.NEUTRAL_FEATURES:
            counts = Counter(round(float(s["features"][name]), 6) for s in samples)
            # A clip bound can be the most frequent value of a measured feature
            # (ivme_degisimi sits at 1.0 in 90 rows), so it is skipped rather
            # than allowed to hide the fallback atom behind it.
            candidates = [(v, c) for v, c in counts.most_common() if v not in (0.0, 1.0)]
            if candidates and candidates[0][1] >= 0.10 * len(samples):
                fallbacks[name] = candidates[0][0]
        runs: dict[str, list] = {}
        for sample in samples:
            features = sample["features"]
            mask = 0
            for i, name in enumerate(self.FEATURE_NAMES):
                if name in fallbacks and round(float(features[name]), 6) == fallbacks[name]:
                    continue
                mask |= 1 << i
            runs.setdefault(sample["run_id"], []).append({"measured_mask": mask, **features, "_scenario": sample["scenario"]})
        by_scenario: dict[str, dict] = {}
        for run_id, rows in sorted(runs.items()):
            scenario = rows[0]["_scenario"]
            entry = by_scenario.setdefault(scenario, {"runs": 0, "flushes": [], "vectors": [], "popcounts": []})
            entry["runs"] += 1
            entry["flushes"].append(len(rows))
            entry["popcounts"].extend(bin(r["measured_mask"]).count("1") for r in rows)
            built = p.session_vector(rows)
            if built is not None:
                entry["vectors"].append(built["vec"])
        # Under the layer's own rules nothing matures: a scenario has at most
        # eight runs. Descriptive pass OUTSIDE those rules, stated as such:
        # leave-one-run-out, feature observation minimum relaxed to what seven
        # references allow, deviation of a held-out run of the SAME scenario vs
        # every run of the OTHER scenarios against the same references.
        descriptive = {}
        relaxed_obs = 5
        with self.settings(PROFILE_TOP_K=top_k, PROFILE_MIN_FEATURE_OBS=relaxed_obs, PROFILE_SCALE_FLOOR=floor):
            for scenario, entry in by_scenario.items():
                vectors = entry["vectors"]
                if len(vectors) < relaxed_obs + 1:
                    continue
                own, others = [], []
                for index, held_out in enumerate(vectors):
                    refs = vectors[:index] + vectors[index + 1:]
                    stats = p.feature_stats(refs)
                    feats = p.participating_features(held_out, stats)
                    if len(feats) >= p.PROFILE_MIN_FEATURES:
                        d, _ = p.deviation(held_out, stats, feats)
                        own.append(d)
                    for other, other_entry in by_scenario.items():
                        if other == scenario:
                            continue
                        for cand in other_entry["vectors"]:
                            feats = p.participating_features(cand, stats)
                            if len(feats) >= p.PROFILE_MIN_FEATURES:
                                d, _ = p.deviation(cand, stats, feats)
                                others.append(d)
                if len(own) >= relaxed_obs and others:
                    descriptive[scenario] = {
                        "own_n": len(own), "own_median": median(own),
                        "other_n": len(others), "other_median": median(others),
                        # Larger d = further from the scenario's own runs.
                        "auc": auc_lower_is_positive([-d for d in others], [-d for d in own]),
                    }
        self.browser = {"fallbacks": fallbacks, "scenarios": by_scenario, "descriptive": descriptive, "relaxed_obs": relaxed_obs}

    # -- run ---------------------------------------------------------------------------

    def run(self) -> dict:
        args = self.args
        tm = self.train_model
        started = time.time()
        self.log("loading the served model bundle (read only)")
        self.scorer.get_bundle()
        self.results["meta"].update({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "identities": args.identities,
            "ratio_identities": args.ratio_identities,
            "bot_sessions": args.bot_sessions,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "model": os.path.basename(self.scorer.MODEL_PATH),
            "code_constants": self.code_constants,
        })
        self.check_fidelity()

        self.log(f"simulating {args.identities} identities x {tm.IDENTITY_MODALITIES}")
        people = self.simulate_people(args.identities, SEED_IDENTITIES)
        self.people = people
        self.check_port(people)

        min_obs = self.stage_min_feature_obs(people)
        floor = self.stage_scale_floor(people, min_obs)
        self.log("alpha x K grid")
        self.stage_grid(people, min_obs, floor)
        top_k = self.choose_top_k(people)
        self.measured = {"PROFILE_MIN_FEATURE_OBS": min_obs, "PROFILE_SCALE_FLOOR": floor, "PROFILE_TOP_K": top_k}
        self.results["measured"] = self.measured
        self.log("starting values vs measured")
        self.stage_provisional_comparison(people, min_obs, floor, top_k)
        self.log("calibration check (full conformal)")
        self.stage_calibration_check(people, min_obs, floor, top_k)
        self.log("poisoning: promoted references")
        self.stage_poisoning(people, min_obs, floor, top_k)
        self.log("poisoning: stored on probation (shipped rule)")
        self.stage_probation_poisoning(people, min_obs, floor, top_k)
        self.log("rescued-then-returning sessions")
        self.stage_rescued_returning(people, min_obs, floor, top_k)
        self.log("cross-modality")
        self.stage_cross_modality(people, min_obs, floor, top_k)
        self.log("bots and replay")
        self.stage_bots_and_replay(people, min_obs, floor, top_k, args.bot_sessions)
        self.log("within-identity ratio sensitivity")
        self.stage_ratio(args.ratio_identities, min_obs, floor, top_k)
        self.log("browser lab sanity pass")
        self.stage_browser_lab(min_obs, floor, top_k)
        self.results["meta"]["runtime_s"] = round(time.time() - started)
        return self.results


# --- latency (spec 7.5) ----------------------------------------------------------------

LATENCY_CONFIGS = {
    # name: (PROFILE_ENABLED, PROFILE_ESCALATION, send a customer_ref, flush value, expected action)
    "off": (False, False, False, 0.5, "allow"),
    "shadow": (True, False, True, 0.5, "allow"),
    "enforcing_escalates": (True, True, True, 1.5, "verify"),
    "enforcing_learns": (True, True, True, 0.5, "allow"),
}


def measure_latency(url: str, n: int, warmup: int, label: str) -> dict:
    """/api/decision p50/p95 with the profile layer off, in shadow and enforcing,
    same process, configurations interleaved round-robin so drift hits all of
    them equally.

    The ASGI app is called directly in one event loop (no HTTP client, no
    network hop to the API) against the Postgres at `url`, which must be a
    THROWAWAY database: this seeds thousands of rows and never cleans up. Each
    measured request uses a fresh session (so per-session rate limits and
    learn-once do not change the path) and, for the profile configurations, its
    own mature profile of 20 reference vectors (so the daily learning cap and
    the per-profile budget do not change it either). The breaker ceiling and the
    per-customer profile bucket are lifted for the run so neither trips part-way
    and turns the enforcing path into a cheaper one.
    """
    _setup_imports()
    from urllib.parse import urlparse

    database_name = urlparse(url.replace("+asyncpg", "")).path.lstrip("/")
    if "profile_lab" not in database_name:
        raise SystemExit(
            "Gecikme olcumu yalnizca adi 'profile_lab' iceren atilabilir bir veritabaninda "
            f"calisir (verilen: {database_name!r}); binlerce satir yazar ve silmez."
        )
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("DEEPCHECK_SECRET", "profile-lab-secret-not-for-production")
    os.environ.setdefault("DASHBOARD_KEY", "profile-lab-dashboard-not-for-production")
    os.environ.setdefault("DEBUG", "0")

    import logging

    import database
    import main
    import profiles
    from lstm_model import FEATURE_NAMES
    from sqlalchemy import text

    logging.getLogger("deepcheck").setLevel(logging.WARNING)
    merchant_key = "k" * 40
    profile_key = b"q" * 40
    main.MERCHANT_KEYS = {"lab": merchant_key}
    main.PROFILE_KEY = profile_key
    main.PROFILE_LAYER = True
    main.RATE_LIMITS["profile"] = (10**9, 3600)
    profiles.PROFILE_BREAKER_MAX = 10**9
    merchant_headers = {"X-Merchant-Id": "lab", "X-Merchant-Key": merchant_key}
    all_bits = (1 << len(FEATURE_NAMES)) - 1
    columns = ", ".join(FEATURE_NAMES)
    placeholders = ", ".join(f":{name}" for name in FEATURE_NAMES)

    async def seed(prefix, count, value, with_profile):
        now = datetime.now(timezone.utc)
        sessions, flushes, profile_rows, vectors = [], [], [], []
        rng = np.random.default_rng(7)
        for i in range(count):
            sid = str(uuid.uuid4())
            ref = f"{prefix}-{i}"
            sessions.append({"id": sid, "ls": now + timedelta(hours=2)})
            for _ in range(5):
                flushes.append({"s": sid, "m": all_bits, "cs": json.dumps({"pointer_mouse": 12}),
                                **{name: value for name in FEATURE_NAMES}})
            if with_profile:
                pid = profiles.derive_profile_id("lab", ref, profile_key)
                profile_rows.append({"p": pid, "v": profiles.FEATURE_SCHEMA_VERSION})
                for j in range(20):
                    vec = {name: float(0.5 + rng.normal(0, 0.08)) for name in FEATURE_NAMES}
                    vectors.append({"p": pid, "s": f"ref-{ref}-{j}", "c": now - timedelta(days=40) + timedelta(hours=j),
                                    "v": profiles.FEATURE_SCHEMA_VERSION, "vec": json.dumps(vec)})
        async with database.get_engine().begin() as conn:
            await conn.execute(
                text("INSERT INTO sessions (id, created_at, last_seen_at, risk_score, label, confidence, "
                     "shap_explanation, response_time_ms, profile_learned) VALUES (:id, now(), :ls, 12.0, "
                     "'Gerçek Kullanıcı', 0.9, '[]', 1.0, false)"),
                sessions,
            )
            await conn.execute(
                text(f"INSERT INTO behavior_data (session_id, created_at, mouse_trajectory, click_timing, "
                     f"scroll_rhythm, hesitation_intervals, focus_changes, key_events, {columns}, risk_score, "
                     f"measured_mask, client_signals, raw_purged) VALUES (:s, now(), '[]', '[]', '[]', '[]', "
                     f"'[]', '[]', {placeholders}, 5.0, :m, CAST(:cs AS json), false)"),
                flushes,
            )
            if with_profile:
                await conn.execute(
                    text("INSERT INTO customer_profiles (profile_id, merchant_id, key_version, "
                         "feature_schema_version, profiling_enabled, consent_basis, is_demo, escalation_count, "
                         "consecutive_passed_escalations) VALUES (:p, 'lab', 1, :v, true, 'explicit_consent', "
                         "false, 0, 0)"),
                    profile_rows,
                )
                await conn.execute(
                    text("INSERT INTO customer_profile_vectors (profile_id, session_id, created_at, modality, "
                         "feature_schema_version, vec, disp, flush_count, probation, outcome) VALUES (:p, :s, :c, "
                         "'mouse', :v, CAST(:vec AS jsonb), '{}'::jsonb, 5, false, 'pending')"),
                    vectors,
                )
        return [(s["id"], f"{prefix}-{i}") for i, s in enumerate(sessions)]

    async def asgi_post(path, body, headers):
        payload = json.dumps(body).encode()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
            + [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        }
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        status, chunks = None, []

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await main.app(scope, receive, send)
        return status, json.loads(b"".join(chunks))

    async def run():
        async with main.lifespan(main.app):
            async with database.get_engine().begin() as conn:
                existing = (await conn.execute(text("SELECT count(*) FROM sessions"))).scalar()
            if existing:
                raise SystemExit(
                    f"Gecikme olcumu bos bir veritabani ister ({existing} oturum zaten var); "
                    "atilabilir yeni bir veritabani olusturun."
                )
            pools = {name: await seed(name, n + warmup, cfg[3], cfg[2]) for name, cfg in LATENCY_CONFIGS.items()}
            samples = {name: [] for name in LATENCY_CONFIGS}
            for i in range(n + warmup):
                for name, (enabled, escalation, send_ref, _, expected) in LATENCY_CONFIGS.items():
                    sid, ref = pools[name][i]
                    main.PROFILE_ENABLED = enabled
                    main.PROFILE_ESCALATION = escalation
                    body = {"session_id": sid}
                    headers = {"x-deepcheck-token": main.sign_session(sid)}
                    if send_ref:
                        body["customer_ref"] = ref
                        headers.update(merchant_headers)
                    t0 = time.perf_counter()
                    status, out = await asgi_post("/api/decision", body, headers)
                    elapsed = (time.perf_counter() - t0) * 1000
                    if status != 200 or out.get("action") != expected:
                        raise SystemExit(f"beklenmeyen karar {name}: {status} {out}")
                    if i >= warmup:
                        samples[name].append(elapsed)
            async with database.get_engine().begin() as conn:
                audit, learned, escalations = (await conn.execute(text(
                    "SELECT (SELECT count(*) FROM decision_audit), "
                    "(SELECT count(*) FROM customer_profile_vectors WHERE session_id NOT LIKE 'ref-%'), "
                    "(SELECT count(*) FROM decision_audit WHERE reason = 'profile_deviation')"
                ))).first()
        rows = {}
        for name, values in samples.items():
            values.sort()
            rows[name] = {
                "n": len(values),
                "p50": statistics.median(values),
                "p95": values[int(round(0.95 * (len(values) - 1)))],
                "mean": statistics.fmean(values),
            }
            print(f"{name:22s} n={len(values)} p50={rows[name]['p50']:6.2f} ms p95={rows[name]['p95']:6.2f} ms")
        return {"label": label, "platform": f"{platform.system()} / Python {platform.python_version()}",
                "rows": rows, "audit_rows": int(audit), "learned": int(learned), "escalations": int(escalations)}

    return asyncio.run(run())


# --- report -----------------------------------------------------------------------------


# Section 7's prose, kept out of write_markdown so that test_profiles can render
# it from the published tables and check the page says what the code now does.
POISONING_INTRO = (
    "Mouse profiles, alpha 0.05. The attacker is one fixed other identity per victim. After `k` of the "
    "attacker's sessions have been learned into the victim's buffer, the attacker's fresh sessions and the "
    "victim's own fresh sessions are presented. How a learned session is stored decides what it does, so "
    "there are two tables. **Stored on probation** is the shipped rule for a session learned only because a "
    "passed step-up rescued a profile challenge, i.e. the attacker holding a phished code: the buffer is built "
    "through `profiles.admit_vector` and the reference set is read back by the rule "
    "`main._read_profile_context` uses, under which a probation vector is not a reference (at most "
    "`PROFILE_PROBATION_MAX = {probation_max}` are stored per profile and input type, beside the 20 references "
    "and never evicting one). **Promoted to a reference** is what such a vector becomes when the merchant "
    "settles the payment (`POST /api/outcome`), and what every probation vector was before probation vectors "
    "were excluded: the `k` oldest victim references give way to `k` attacker references. A self-healing "
    "rebuild (`PROFILE_HEAL_AFTER = {heal_after}` passed challenges in a row) also promotes, but cuts the "
    "profile to `PROFILE_HEAL_KEEP = {heal_keep}` references, below maturity, so the layer then abstains on "
    "that profile instead of ranking against the second table."
)


def poisoning_reading(probation: dict, promoted: dict, identical: bool, probation_max: int,
                      heal_after: int, max_escalations: int) -> str:
    """`probation` and `promoted` map k to the published escalation rate of the
    attacker's sessions ("48.5%"); `identical` is whether every victim's
    reference set came back identical to the one with no attacker session, for
    every k. The first sentence is decided by the numbers, not written in
    advance."""
    ks = sorted(probation)
    if identical and len(set(probation.values())) == 1:
        first = (
            f"**Stored on probation, the curve is flat.** The attacker's sessions were escalated {probation[ks[0]]} "
            f"of the time with none of his sessions stored and {probation[ks[-1]]} with {ks[-1]} learned "
            f"({probation_max} kept, the storage cap), because every victim's reference set came back identical to "
            "the one with none: a passed step-up no longer buys the attacker anything inside the statistic. "
        )
    elif identical:
        first = (
            "**Stored on probation, the curve is NOT flat** ("
            + ", ".join(f"k = {k}: {probation[k]}" for k in ks)
            + ") although every reference set came back identical, which the rank cannot produce: investigate "
            "before quoting any row. "
        )
    else:
        first = (
            "**Stored on probation, the curve is NOT flat** ("
            + ", ".join(f"k = {k}: {probation[k]}" for k in ks)
            + ") and not every reference set came back identical to the one with no attacker session: a probation "
            "vector is reaching the statistic, which the code says it cannot. Investigate before quoting any row. "
        )
    return first + (
        f"**Promoted, it is not flat:** one attacker reference took the attacker's escalation rate from {promoted[0]} "
        f"to {promoted[1]}, and {probation_max} to {promoted[probation_max]}. The mechanism is the rank itself: at "
        "alpha 0.05 with 20 references a session is challenged only when its deviation exceeds the deviation of "
        "**every** reference (each scored against the other points), so one reference that is itself far from the "
        "customer's centre, the attacker's learned session, shields every later session less extreme than it. "
        "\"One absorbed vector moves the p-value by at most 1/(n+1)\" is true, and 1/(n+1) is exactly the step "
        "between challenged and not. That is why a probation vector is not a reference, and why promotion asks for "
        "what one phished code does not give: the merchant's statement that the payment was legitimate, or "
        f"{heal_after} passed challenges in a row. What passing still buys lies outside the statistic and is not "
        f"measured here: each passed challenge spends one of the profile's `PROFILE_MAX_ESCALATIONS = {max_escalations}` "
        f"per 30 days (a challenge nobody answers spends nothing), and {heal_after} passed in a row rebuild the "
        "profile below maturity, after which the layer says nothing on it until it matures again. Against an "
        "attacker who can pass the merchant's step-up at will, this layer's strength is that channel's."
    )


def grandchild_reading(again: dict, counted: dict, heal_after: int, max_escalations: int) -> str:
    """`again` and `counted` map the input type to the published rate at which
    the returning session was challenged again, under the shipped rule and with
    the rescued session counted as a reference."""
    return (
        "**The accepted cost, measured.** The grandchild's rescued session is not a reference either, because the "
        "statistic cannot tell it from the attacker's. When a different person was challenged, passed step-up and "
        f"came back in the same pattern, the next session was challenged again {again['mouse']} of the time on "
        f"mouse and {again['keyboard']} on keyboard; under the old rule, in which the rescued session counted as a "
        f"reference, the same sessions were challenged again {counted['mouse']} and {counted['keyboard']} of the "
        "time. Each of those challenges is one more verification, never a block, until the merchant settles the "
        f"earlier payment or {heal_after} passed challenges in a row promote the pattern; and once {max_escalations} "
        "challenges have been passed in 30 days, the profile asks for no more in that window."
    )


def write_markdown(lab: Lab, latency_runs: list[dict], path: str) -> None:
    r = lab.results
    rng = np.random.default_rng(SEED_BOOTSTRAP + 1)
    meta = r["meta"]
    measured = lab.measured
    code = lab.code_constants
    p = lab.profiles
    out: list[str] = []
    w = out.append

    def banner(sentence: str = SYNTHETIC_SENTENCE, lower_bound: bool = True):
        w("")
        w(f"> **{sentence}**" + (f" {LOWER_BOUND}" if lower_bound else ""))
        w("")

    def states_cell(counter: Counter, total: int) -> str:
        abstain = total - counter.get(p.STATE_EVALUATED, 0)
        return pct(abstain / total) if total else "–"

    w("# Per-customer profile — evaluation")
    w("")
    w(f"> **{SYNTHETIC_SENTENCE}**")
    w(">")
    w("> Every number on this page was measured on **synthetic identities** generated by "
      "`train_model.simulate_identity_sessions()`, plus a sanity pass over **scripted Playwright "
      "browser runs** (`lab/real_telemetry.json`). **No real customer has been measured.** "
      "Every false-challenge rate is a **lower bound**: a synthetic person is a generator with fixed "
      "parameters, so their sessions are self-consistent in a way no real person is (device, posture, "
      "time of day, hurry, which hand holds the phone), and conformal calibration holds only while a "
      "person's sessions are exchangeable, which real drift breaks. The bias is in the direction that "
      "flatters the product.")
    w("")
    w(f"Generated by `backend/profile_lab.py` on {meta['date']} ({meta['identities']} identities, "
      f"{CANDIDATES_PER_IDENTITY} fresh sessions each per modality, served bundle `{meta['model']}` read only, "
      f"Python {meta['python']}, numpy {meta['numpy']}, runtime {meta.get('runtime_s', '?')} s). "
      "Re-run: `cd backend && python profile_lab.py` (under the backend lock).")
    w("")
    w("What this layer is, so the numbers are read correctly: it can only turn `allow`/`warn` into "
      "`verify`. It never blocks, never approves, never changes a risk score. It is aimed at **human "
      "account takeover**; card-testing bots arrive without a customer reference and never reach it.")
    w("")
    # The before/after section is rendered LAST, from the very strings the
    # sections below print, and inserted here: one bootstrap draw per cell, so
    # a figure reads the same at the top of the page as in its section.
    before_after_at = len(out)
    cells: dict = {}

    # -- the assumption --------------------------------------------------------------
    w("## 1. The assumption every number depends on")
    w("")
    w(f"`WITHIN_IDENTITY_SD_RATIO = {code['WITHIN_IDENTITY_SD_RATIO']}`: a person's session-to-session "
      "spread of each behavioural parameter is 0.6 of the spread between people. **Nobody has measured "
      "this**; it needs real customers. The between-person spreads in `train_model.IDENTITY_PARAMETERS` "
      "are assumptions of the same kind. Section 9 shows how the headline numbers move when the ratio "
      "moves. Touch is **not simulated at all** (the generator has no model of a finger), so nothing "
      "below says anything about phones.")
    w("")

    # -- measured constants -------------------------------------------------------------
    w("## 2. Constants set from this measurement")
    w("")
    w("| constant | spec starting value | in `profiles.py` when this ran | measured | rule (fixed before looking) |")
    w("|---|---|---|---|---|")
    rules = {
        "PROFILE_MIN_FEATURE_OBS": "knee of the scale-estimate error curve, n = 2..19 (section 3)",
        "PROFILE_SCALE_FLOOR": "5th percentile of the non-zero reference-set scales, rounded down to 2 significant figures (section 4)",
        "PROFILE_TOP_K": "highest impostor escalation at alpha 0.05; stays 3 unless the paired bootstrap interval of the difference excludes zero (section 6)",
    }
    for name, rule in rules.items():
        w(f"| `{name}` | {SPEC_STARTING_VALUES[name]} | {code[name]} | **{measured[name]}** | {rule} |")
    banner()

    prov = lab.provisional
    w("Same data, the spec's starting values against the measured ones (alpha 0.05, 20 references; the "
      "tuple is min feature observations, scale floor, K):")
    w("")
    w("| constants | modality | false challenge (95% CI) | abstained | impostor escalation (95% CI) |")
    w("|---|---|---|---|---|")
    for label in ("starting", "measured"):
        for modality in lab.train_model.IDENTITY_MODALITIES:
            b = prov[(label, modality)]
            total = sum(b["same_totals"])
            w(f"| {label} {tuple(r['provisional_settings'][label])} | {modality} | "
              f"{rate_cell(b['same_hits'], b['same_totals'], rng)} | {states_cell(b['same_states'], total)} | "
              f"{rate_cell(b['imp_hits'], b['imp_totals'], rng)} |")
    banner()

    # -- min feature obs ---------------------------------------------------------------------
    mfo = r["min_feature_obs"]
    w("## 3. `PROFILE_MIN_FEATURE_OBS` — how many observations a median/MAD needs")
    w("")
    w("For each (identity, modality, feature) with a non-zero spread, the 'true' centre and scale are "
      f"taken from all of that identity's sessions (up to {REFERENCE_POOL + CANDIDATES_PER_IDENTITY}); "
      f"then {KNEE_SUBSETS} random subsets of n sessions re-estimate them. Cells are the median, over "
      "identity x feature, of the RMS error in units of the true scale. The subsets come from the same "
      "pool as the 'truth', so the error at large n is slightly understated (finite-population effect).")
    w("")
    w("| n | scale error (mouse) | scale error (keyboard) | scale error (pooled) | centre error (pooled) |")
    w("|---|---|---|---|---|")
    for row in mfo["table"]:
        mark = " **← knee**" if row["n"] == mfo["knee"] else ""
        w(f"| {row['n']}{mark} | {row['mouse_scale']:.3f} | {row['keyboard_scale']:.3f} | "
          f"{row['pooled_scale']:.3f} | {row['pooled_centre']:.3f} |")
    banner(lower_bound=False)
    w(f"Knee: **n = {mfo['knee']}**. Below it the scale estimate improves fast with each extra session; "
      "above it slowly. The odd/even alternation is the median's own: an even count averages the two middle "
      "values. Note what the knee does not say: at the knee the scale is still wrong by the tabled "
      "fraction; section 5 measures what the whole layer then costs in false challenges.")
    w("")

    # -- scale floor --------------------------------------------------------------------------
    sf = r["scale_floor"]
    w("## 4. `PROFILE_SCALE_FLOOR` — which spreads are too small to trust")
    w("")
    w(f"Over {sf['total']} (identity, modality, feature) reference sets of 20 sessions with the feature "
      f"measured at least {measured['PROFILE_MIN_FEATURE_OBS']} times, {sf['zero']} had a scale of exactly "
      f"zero (excluded by any positive floor). The 5th percentile of the rest is {sf['p5_positive']:.5f}, "
      f"so the floor is **{measured['PROFILE_SCALE_FLOOR']}**.")
    w("")
    w(f"| modality | feature | sets | scale = 0 | p5 | p50 | excluded at {SPEC_STARTING_VALUES['PROFILE_SCALE_FLOOR']} | excluded at {measured['PROFILE_SCALE_FLOOR']} |")
    w("|---|---|---|---|---|---|---|---|")
    for row in sf["table"]:
        w(f"| {row['modality']} | `{row['feature']}` | {row['sets']} | {pct(row['zero'], 0)} | {row['p5']:.4f} | "
          f"{row['p50']:.4f} | {pct(row['below_old'], 0)} | {pct(row['below_new'], 0)} |")
    banner(lower_bound=False)
    no_spread = [f"`{row['feature']}` ({row['modality']})" for row in sf["table"] if row["zero"] >= 0.9]
    readmitted = [
        f"`{row['feature']}` ({row['modality']}: {pct(row['below_old'], 0)} → {pct(row['below_new'], 0)})"
        for row in sf["table"]
        if row["below_old"] - row["below_new"] >= 0.25
    ]
    if no_spread:
        w("No spread in at least 90% of reference sets, so excluded whatever the floor — the case the "
          "exclusion rule exists for: " + ", ".join(no_spread) + ".")
        w("")
    if readmitted:
        w(f"Features the starting floor ({SPEC_STARTING_VALUES['PROFILE_SCALE_FLOOR']}) excluded for at least a quarter more reference sets than the "
          f"measured one: " + ", ".join(readmitted) + ". Their spread is small on the 0..1 scale but not "
          "zero. What the change costs and buys, on the same data, is the starting-vs-measured table in "
          "section 2.")
        w("")

    # -- headline -----------------------------------------------------------------------------
    alpha = p.PROFILE_ALPHA
    k = measured["PROFILE_TOP_K"]
    w("## 5. Same person vs a different person (the account-takeover case)")
    w("")
    w(f"Measured constants, alpha {alpha}, a mature profile of 20 sessions per identity and modality. "
      f"**Same person**: {CANDIDATES_PER_IDENTITY} fresh sessions of the identity that owns the profile — a "
      "legitimate returning customer, so every escalation is a **false challenge**. **Different person**: "
      f"{CANDIDATES_PER_IDENTITY} sessions of {CANDIDATES_PER_IDENTITY} other identities on the same modality "
      "(the grandchild holding grandmother's phone, or an account-takeover attacker) — here an escalation "
      "is the extra verification the layer exists to ask for. Rates are over **all** sessions presented; "
      "abstentions (thin session, too few participating features) count as no challenge. Intervals are "
      "95% cluster bootstrap over identities.")
    w("")
    w("| modality | same person: false challenge | same person: abstained | different person: escalated | different person: abstained | AUC of p-value (evaluated only) |")
    w("|---|---|---|---|---|---|")
    headline = {}
    for modality in lab.train_model.IDENTITY_MODALITIES:
        b = lab.grid[(alpha, k, modality)]
        same_total = sum(b["same_totals"])
        imp_total = sum(b["imp_totals"])
        auc = auc_lower_is_positive(b["imp_p"], b["same_p"])
        headline[modality] = {
            "false_challenge": cluster_rate(b["same_hits"], b["same_totals"], rng),
            "impostor": cluster_rate(b["imp_hits"], b["imp_totals"], rng),
            "same_sessions": same_total,
            "imp_sessions": imp_total,
            "same_abstain": 1 - b["same_states"].get(p.STATE_EVALUATED, 0) / same_total,
            "imp_abstain": 1 - b["imp_states"].get(p.STATE_EVALUATED, 0) / imp_total,
            "auc": auc,
        }
        cells[("same", modality)] = rate_cell(b["same_hits"], b["same_totals"], rng)
        cells[("different", modality)] = rate_cell(b["imp_hits"], b["imp_totals"], rng)
        w(f"| {modality} | {cells[('same', modality)]} | {states_cell(b['same_states'], same_total)} | "
          f"{cells[('different', modality)]} | {states_cell(b['imp_states'], imp_total)} | "
          f"{'–' if auc is None else f'{auc:.3f}'} |")
    r["headline"] = headline
    banner()
    w("Why the same-person rate is not zero: with 20 references the smallest p-value is 1/21, so a session "
      "is challenged exactly when its deviation exceeds that of all 20 of the customer's own past sessions, "
      "each of them scored against the other 20 points (full conformal). For exchangeable sessions that "
      f"happens at most 1 time in 21 ({pct(1 / 21)}) of the sessions the layer evaluates (1 in 20 when one "
      "reference cannot be scored), which is the floor on the cost of this layer for a customer as consistent "
      "as a synthetic one.")
    w("")
    w("**Calibration check.** The shipped rank (`profiles.evaluate_profile`) is full conformal: the candidate "
      "and every reference are scored by one function, each against the other 20 of the 21 points on its own "
      "participating features, which makes the rank exact under exchangeability. Below, the same candidates "
      "and references under the shipped code and under this script's own, independently written "
      "full-conformal implementation; the two must agree verdict for verdict.")
    w("")
    w("| modality | implementation | same person: false challenge | different person: escalated |")
    w("|---|---|---|---|")
    calibration_summary = {}
    for modality in lab.train_model.IDENTITY_MODALITIES:
        b = lab.grid[(alpha, k, modality)]
        c = lab.calibration[modality]
        w(f"| {modality} | shipped (`profiles.py`) | {rate_cell(b['same_hits'], b['same_totals'], rng)} | "
          f"{rate_cell(b['imp_hits'], b['imp_totals'], rng)} |")
        w(f"| {modality} | independent (`profile_lab.py`) | {rate_cell(c['same_hits'], c['same_totals'], rng)} | "
          f"{rate_cell(c['imp_hits'], c['imp_totals'], rng)} |")
        calibration_summary[modality] = {
            "shipped_same": cluster_rate(b["same_hits"], b["same_totals"], rng),
            "independent_same": cluster_rate(c["same_hits"], c["same_totals"], rng),
            "independent_different": cluster_rate(c["imp_hits"], c["imp_totals"], rng),
            "disagreements": c["disagreements"],
            "verdicts": c["verdicts"],
        }
    r["calibration"] = calibration_summary
    for modality, v in calibration_summary.items():
        cells[("calibration", modality)] = (v["disagreements"], v["verdicts"])
    banner()
    for modality, v in calibration_summary.items():
        w(f"- {modality}: verdicts that differ between the shipped and the independent implementation: "
          f"{v['disagreements']} of {v['verdicts']}.")
    w("")
    w("Before the shipped rank was full conformal it was leave-one-out: the candidate scored against the 20 "
      "references on its own participating features, each reference against the other 19 on the part of the "
      "candidate's features it could be scored on. This check measured that rank miscalibrated in both "
      "directions on the same synthetic sessions (2026-09-16: mouse 6.0% same-person challenges against 4.9% "
      "for full conformal, keyboard 2.9% against 3.8%), which is why it was replaced. Rates on this page count "
      "abstentions as no challenge and are subject to sampling error; like every false-challenge figure here "
      "they are lower bounds on real customers, whose sessions are far less exchangeable than a synthetic "
      "person's.")
    w("")
    w("Abstention by state (all sessions presented):")
    w("")
    w("| modality | who | " + " | ".join(p.PROFILE_STATES) + " |")
    w("|---|---|" + "---|" * len(p.PROFILE_STATES))
    for modality in lab.train_model.IDENTITY_MODALITIES:
        b = lab.grid[(alpha, k, modality)]
        for who, key in (("same person", "same"), ("different person", "imp")):
            total = sum(b[f"{key}_totals"])
            w(f"| {modality} | {who} | " + " | ".join(pct(b[f'{key}_states'].get(s, 0) / total) for s in p.PROFILE_STATES) + " |")
    banner()

    mature = {m: [person.to_mature[m] for person in lab.people] for m in lab.train_model.IDENTITY_MODALITIES}
    w("Maturity: sessions an identity needed before 19 of them produced a session vector "
      f"(a session with fewer than {p.PROFILE_MIN_FLUSHES} non-provisional flushes, or fewer than "
      f"{p.PROFILE_MIN_FEATURES} measured features, teaches nothing).")
    w("")
    w("| modality | identities matured within the cap | median sessions to maturity | max |")
    w("|---|---|---|---|")
    for modality, values in mature.items():
        ok = [v for v in values if v is not None]
        w(f"| {modality} | {pct(len(ok) / len(values))} | {median(ok) if ok else '–'} | {max(ok) if ok else '–'} |")
    banner(lower_bound=False)
    w(f"With `PROFILE_LEARN_PER_DAY = {p.PROFILE_LEARN_PER_DAY}` a profile cannot mature in fewer than "
      f"{math.ceil(p.PROFILE_MIN_SESSIONS / p.PROFILE_LEARN_PER_DAY)} Istanbul days of allowed checkouts on one "
      "modality. How many real customers ever get there is not measured here — nothing in this project "
      "says how often a real customer checks out — and spec §12 expects most never to.")
    w("")

    # -- sensitivity ---------------------------------------------------------------------------
    w("## 6. Sensitivity: `PROFILE_TOP_K` x `PROFILE_ALPHA`")
    w("")
    w("Maturity follows alpha (`ceil(1/alpha) - 1`), and the reference set is the larger of 20 and that "
      "plus one — at alpha 0.02 a profile needs 49 references, more than `PROFILE_BUFFER_MAX = 20` holds, so "
      "**in the shipped buffer alpha 0.02 can never fire**; the row shows what a 50-session buffer would buy.")
    w("")
    w("| alpha | maturity | references | K | modality | false challenge | abstained | impostor escalation |")
    w("|---|---|---|---|---|---|---|---|")
    for a in ALPHA_GRID:
        gm = r["grid_meta"][str(a)]
        for kk in TOP_K_GRID:
            for modality in lab.train_model.IDENTITY_MODALITIES:
                b = lab.grid[(a, kk, modality)]
                total = sum(b["same_totals"])
                bold = "**" if (a == alpha and kk == k) else ""
                w(f"| {a} | {gm['min_sessions']} | {gm['ref_n']} | {bold}{kk}{bold} | {modality} | "
                  f"{rate_cell(b['same_hits'], b['same_totals'], rng)} | {states_cell(b['same_states'], total)} | "
                  f"{rate_cell(b['imp_hits'], b['imp_totals'], rng)} |")
    banner()
    tk = r["top_k"]
    w(f"`PROFILE_TOP_K` decision at alpha {alpha}, both modalities pooled (difference to K = 3, paired "
      "cluster bootstrap over identities):")
    w("")
    w("| K | impostor escalation | difference to K=3, 95% interval |")
    w("|---|---|---|")
    for kk in TOP_K_GRID:
        c = tk["comparisons"][kk]
        w(f"| {kk} | {pct(c['rate'])} | {c['diff_lo'] * 100:+.1f} … {c['diff_hi'] * 100:+.1f} pp |")
    banner(lower_bound=False)
    w(f"Chosen: **K = {tk['chosen']}**.")
    w("")

    # -- poisoning --------------------------------------------------------------------------------
    w("## 7. Poisoning: attacker vectors in the victim's buffer")
    w("")
    w(POISONING_INTRO.format(probation_max=p.PROFILE_PROBATION_MAX, heal_after=p.PROFILE_HEAL_AFTER,
                             heal_keep=p.PROFILE_HEAL_KEEP))
    w("")
    w("**Stored on probation** (the shipped rule):")
    w("")
    w("| attacker sessions learned on probation k | stored on probation | reference set identical to k = 0 | attacker escalated | attacker median p | victim false challenge |")
    w("|---|---|---|---|---|---|")
    probation_rates, probation_summary, identical = {}, {}, True
    for row in lab.probation_poisoning:
        b = row["block"]
        attacker = rate_cell(b["imp_hits"], b["imp_totals"], rng)
        victim = rate_cell(b["same_hits"], b["same_totals"], rng)
        med = f"{median(b['imp_p']):.4f}" if b["imp_p"] else "–"
        stored = "/".join(str(v) for v in row["stored_probation"])
        w(f"| {row['k']} | {stored} | {row['identical']} / {row['victims']} | {attacker} | {med} | {victim} |")
        probation_rates[row["k"]] = attacker.split(" ")[0]
        identical = identical and row["identical"] == row["victims"]
        probation_summary[str(row["k"])] = {
            "attacker_escalated": attacker, "attacker_median_p": med, "victim_false_challenge": victim,
            "identical_reference_sets": row["identical"], "victims": row["victims"], "stored_probation": row["stored_probation"],
        }
        cells[("probation", row["k"])] = (probation_rates[row["k"]], med, f"{row['identical']} / {row['victims']}")
    r["probation_poisoning"] = probation_summary
    banner()
    w("**Promoted to a reference** (settled by the merchant; also the rule before probation vectors were excluded):")
    w("")
    w("| attacker references k | attacker escalated | attacker median p | victim false challenge |")
    w("|---|---|---|---|")
    promoted_rates, promoted_summary = {}, {}
    for kk, b in lab.poisoning:
        attacker = rate_cell(b["imp_hits"], b["imp_totals"], rng)
        victim = rate_cell(b["same_hits"], b["same_totals"], rng)
        med = f"{median(b['imp_p']):.4f}" if b["imp_p"] else "–"
        w(f"| {kk} | {attacker} | {med} | {victim} |")
        promoted_rates[kk] = attacker.split(" ")[0]
        promoted_summary[str(kk)] = {"attacker_escalated": attacker, "attacker_median_p": med, "victim_false_challenge": victim}
        cells[("promoted", kk)] = (promoted_rates[kk], med)
    r["poisoning"] = promoted_summary
    banner()
    w(poisoning_reading(probation_rates, promoted_rates, identical, p.PROFILE_PROBATION_MAX, p.PROFILE_HEAL_AFTER,
                        p.PROFILE_MAX_ESCALATIONS))
    w("")
    w("**Rescued, then returning** (the grandchild's side of the same rule). For every mature profile one fixed other "
      "identity plays the grandchild and presents its 20 fresh sessions in order; every consecutive pair whose first "
      "session was challenged counts once. The first session is stored as rescued (a passed step-up), the second is "
      "ranked against the reference set read back from that buffer: under the shipped rule, and under the old one in "
      "which the rescued session replaced the victim's oldest reference. Alpha 0.05, 20 references; intervals are "
      "cluster bootstrap over profiles.")
    w("")
    w("| input type | grandchild sessions challenged | challenged sessions followed by another | returning session challenged again (shipped: rescued session on probation) | same sessions, rescued session counted as a reference (old rule) |")
    w("|---|---|---|---|---|")
    again, counted, rescued_summary = {}, {}, {}
    for modality in lab.train_model.IDENTITY_MODALITIES:
        g = lab.rescued[modality]
        first = rate_cell(g["first_hits"], g["first_totals"], rng)
        shipped = rate_cell(g["shipped_hits"], g["pairs"], rng)
        old = rate_cell(g["counted_hits"], g["pairs"], rng)
        w(f"| {modality} | {first} | {sum(g['pairs'])} | {shipped} | {old} |")
        again[modality] = shipped.split(" ")[0]
        counted[modality] = old.split(" ")[0]
        rescued_summary[modality] = {"first": first, "pairs": sum(g["pairs"]), "again_shipped": shipped, "again_counted": old}
        cells[("rescued", modality)] = (again[modality], counted[modality])
    r["rescued_returning"] = rescued_summary
    banner()
    w(grandchild_reading(again, counted, p.PROFILE_HEAL_AFTER, p.PROFILE_MAX_ESCALATIONS))
    w("")

    # -- cross modality ------------------------------------------------------------------------------
    w("## 8. Cross-modality")
    w("")
    w("| comparison | same person: challenged | same person: abstained | different person: challenged |")
    w("|---|---|---|---|")
    labels = {
        "production": "**production**: keyboard session, customer has only mouse history (query filters on modality)",
        "pooled_mouse_refs_keyboard_cands": "counterfactual: keyboard sessions ranked against 20 mouse references",
        "pooled_keyboard_refs_mouse_cands": "counterfactual: mouse sessions ranked against 20 keyboard references",
        "mixed_mouse_cands": "counterfactual: one pooled buffer (10 mouse + 10 keyboard), mouse sessions",
        "mixed_keyboard_cands": "counterfactual: one pooled buffer (10 mouse + 10 keyboard), keyboard sessions",
    }
    cross_summary = {}
    for key, text_label in labels.items():
        b = lab.cross[key]
        total = sum(b["same_totals"])
        imp = rate_cell(b["imp_hits"], b["imp_totals"], rng) if sum(b["imp_totals"]) else "–"
        cross_summary[key] = {
            "same": cluster_rate(b["same_hits"], b["same_totals"], rng),
            "different": cluster_rate(b["imp_hits"], b["imp_totals"], rng),
            "abstain": 1 - b["same_states"].get(p.STATE_EVALUATED, 0) / total,
        }
        w(f"| {text_label} | {rate_cell(b['same_hits'], b['same_totals'], rng)} | {states_cell(b['same_states'], total)} | {imp} |")
    r["cross"] = cross_summary
    banner()
    w("The production row is structural — no references of the session's modality means no comparison — "
      "and is shown to make the cost of the alternative visible: the counterfactual rows are what a "
      "pooled profile would do to the **same** customer on their other input device.")
    w("")

    # -- bots ------------------------------------------------------------------------------------------------
    w("## 9. Bots, replay, and the assumed ratio")
    w("")
    w(f"Bot sessions from the training personas, {meta['bot_sessions']} per persona, each presented with a "
      "customer reference against a random identity's mature profile of the modality the bot's telemetry "
      "implies. This is the account-takeover-by-script case; card-testing bots at guest checkout never "
      "carry a reference and never reach the layer. Blocking bots is the Random Forest's job "
      "(`docs/evaluation.md`), not this layer's.")
    w("")
    w("| persona | modality | sessions | thin (no vector) | too few features / immature | evaluated | escalated |")
    w("|---|---|---|---|---|---|---|")
    bot_summary = {}
    for persona, counts in lab.bots:
        for modality in ("mouse", "keyboard"):
            n = counts.get((modality, "sessions"), 0)
            if not n:
                continue
            thin = counts.get((modality, p.STATE_THIN_SESSION), 0)
            other = counts.get((modality, p.STATE_TOO_FEW_FEATURES), 0) + counts.get((modality, p.STATE_IMMATURE), 0)
            evaluated = counts.get((modality, p.STATE_EVALUATED), 0)
            esc = counts.get((modality, "escalated"), 0)
            bot_summary[f"{persona}/{modality}"] = {"sessions": n, "thin": thin / n, "escalated": esc / n}
            w(f"| {persona} | {modality} | {n} | {pct(thin / n)} | {pct(other / n)} | {pct(evaluated / n)} | {pct(esc / n)} |")
    r["bots"] = bot_summary
    banner(lower_bound=False)
    replay, p_low = lab.replay
    n_replay = len(lab.people)
    w(f"**Replay.** Each identity's first learned session sent again under a new session id ({n_replay} "
      f"profiles): escalated {pct(replay['escalated'] / n_replay)}; `p_value_low` <= 0.05 in "
      f"{pct(replay['low_tail'] / n_replay)}. A replay of the customer's own behaviour matches the profile by "
      "construction, so the high tail cannot be what stops it — which is why a match buys nothing. The low "
      "tail is recorded, not enforced (spec §12), and at this rate would not stop it either.")
    # An exact replay ties with the reference it copies, and conformal_rank
    # counts a tie against the candidate in BOTH tails, so neither p-value can
    # go below 2/(n+1) while that reference is in the set. Said only when the
    # measured values bear it out (p_value_low is stored rounded to 4 places).
    tie_floor = round(2 / (p.PROFILE_BUFFER_MAX + 1), 4)
    if p_low and replay["low_tail"] == 0 and replay["escalated"] == 0 and min(p_low) >= tie_floor:
        w("")
        w(f"Why both are {pct(0.0)} under full conformal: the replay and the reference it copies score exactly "
          "alike, and `profiles.conformal_rank` counts a tie against the candidate in both tails, so neither "
          f"p-value can fall below 2/{p.PROFILE_BUFFER_MAX + 1} = {tie_floor} while the copied session is a "
          f"reference (smallest `p_value_low` measured here: {min(p_low):.4f}). The leave-one-out rank of "
          f"{BEFORE_S4['date']} scored the candidate with its twin among its references but the twin without the "
          f"candidate, so the two scores could differ, and it put {BEFORE_S4['replay'][1]} of these replays in the "
          "low tail. For an exact replay of a stored session that signal is gone: a replay detector will need "
          "something other than this rank.")
    r["replay"] = {"escalated": replay["escalated"] / n_replay, "low_tail": replay["low_tail"] / n_replay,
                   "min_p_value_low": min(p_low) if p_low else None}
    cells["replay"] = (pct(replay["escalated"] / n_replay), pct(replay["low_tail"] / n_replay))
    banner(lower_bound=False)
    w(f"**`WITHIN_IDENTITY_SD_RATIO`**, {meta['ratio_identities']} identities per ratio, measured constants, "
      "alpha 0.05, 20 references:")
    w("")
    w("| ratio | modality | same person: false challenge | different person: escalated |")
    w("|---|---|---|---|")
    for ratio, modality, b in lab.ratio:
        bold = "**" if ratio == code["WITHIN_IDENTITY_SD_RATIO"] else ""
        w(f"| {bold}{ratio}{bold} | {modality} | {rate_cell(b['same_hits'], b['same_totals'], rng)} | "
          f"{rate_cell(b['imp_hits'], b['imp_totals'], rng)} |")
    banner()
    w("Read the rows together: a larger ratio means each synthetic person varies more between their own "
      "sessions relative to how far apart people are. Nothing measured says which row real customers are "
      "in; if they vary more than assumed, the larger-ratio rows are the relevant ones.")
    w("")

    # -- browser lab ----------------------------------------------------------------------------------------
    w("## 10. Browser lab sanity pass")
    w("")
    br = lab.browser
    if br is None:
        w("`lab/real_telemetry.json` not found; skipped.")
    else:
        w("**Scripted Playwright traffic, not one person across many sessions.** Each run of a scenario is "
          "treated as one session and each scenario as one 'customer'. The file stores normalised features "
          "only, under the scaling in force when it was captured, and no `measured_mask`; the mask is inferred "
          "(the most frequent exact value of a fallback-eligible feature is taken as its neutral fallback when "
          "it covers at least 10% of rows and is not a clip bound): "
          + ", ".join(f"`{k2}` = {v}" for k2, v in br["fallbacks"].items()) + ".")
        w("")
        w("| scenario | runs | flushes per run | median measured features per flush | runs yielding a session vector | can mature (19 references)? |")
        w("|---|---|---|---|---|---|")
        for scenario, entry in sorted(br["scenarios"].items()):
            w(f"| {scenario} | {entry['runs']} | {min(entry['flushes'])}–{max(entry['flushes'])} | "
              f"{median(entry['popcounts']):.0f} | {len(entry['vectors'])} | no |")
        banner(LAB_SENTENCE, lower_bound=False)
        w("Under the layer's own rules the lab produces **no mature profile and therefore no verdict**: a "
          "scenario has at most eight runs. The pass below is **outside those rules and descriptive only** — "
          f"`PROFILE_MIN_FEATURE_OBS` relaxed to {br['relaxed_obs']}, no conformal rank (seven references "
          "cannot reach alpha): the top-K deviation of each held-out run against the other runs of its "
          "scenario, versus every run of the other scenarios against the same references.")
        w("")
        w("| scenario as 'customer' | own held-out runs | median deviation | other scenarios' runs | median deviation | AUC (other further than own) |")
        w("|---|---|---|---|---|---|")
        for scenario, d in sorted(br["descriptive"].items()):
            w(f"| {scenario} | {d['own_n']} | {d['own_median']:.2f} | {d['other_n']} | {d['other_median']:.2f} | {d['auc']:.3f} |")
        banner(LAB_SENTENCE, lower_bound=False)
        w("Scenarios are scripted behaviours, several of them written to look like one another, so this "
          "says only that the statistic behaves sensibly on real-browser telemetry, not that it separates "
          "people.")
    w("")

    # -- latency ----------------------------------------------------------------------------------------------
    w("## 11. Latency of `/api/decision` (spec §7.5)")
    w("")
    if not latency_runs:
        w("Not measured in this run (needs `--latency-db` pointing at a throwaway Postgres).")
    else:
        w("Configurations interleaved round-robin in one process, the ASGI app called directly (no HTTP "
          "client), against a **throwaway Postgres 16**; each request a fresh synthetic session and, where "
          "profiled, its own synthetic mature profile of 20 vectors. `enforcing` is reported for both of its "
          "paths: a deviating session (escalation, a share lock on the profile row, the audit row with the compared "
          "vector) and a matching one (audit row plus the learning insert). Budget: 50 ms (CLAUDE.md rule 2).")
        w("")
        w("| topology | layer off p50 / p95 | shadow p50 / p95 | enforcing, escalates p50 / p95 | enforcing, learns p50 / p95 |")
        w("|---|---|---|---|---|")
        for run in latency_runs:
            rows = run["rows"]
            row_cells = [f"{rows[name]['p50']:.1f} / {rows[name]['p95']:.1f} ms" for name in LATENCY_CONFIGS]
            w(f"| {run['label']} ({run['platform']}, n={rows['off']['n']}) | " + " | ".join(row_cells) + " |")
        banner(lower_bound=False)
        over, near, over_without_insert = [], [], []
        for run in latency_runs:
            for name, row in run["rows"].items():
                if row["p95"] > 50.0:
                    over.append(f"{run['label']} / {name} ({row['p95']:.1f} ms)")
                    # layer off and a challenged session never run the
                    # learning insert, so moving it cannot fix these.
                    if name in ("off", "enforcing_escalates"):
                        over_without_insert.append(f"{run['label']} / {name}")
                elif row["p95"] >= 45.0:
                    near.append(f"{run['label']} / {name} ({row['p95']:.1f} ms)")
        if over:
            within = [run["label"] for run in latency_runs if all(row["p95"] <= 50.0 for row in run["rows"].values())]
            w("**Over the 50 ms budget at p95:** " + "; ".join(over) + "."
              + (" Within it in every configuration: " + "; ".join(within) + "." if within else "")
              + " Spec §7.5 names the only change allowed when a configuration does not fit: the learning insert, "
              "and only it, moves off the response path, because it is not needed to answer the current decision. "
              "In the code measured here it is still awaited inside `/api/decision`; this run did not move it. "
              "`enforcing, escalates` does not run that insert"
              + (", and was over the budget as well in " + "; ".join(over_without_insert)
                 + ", so moving the insert would not bring that run within it"
                 if over_without_insert else "")
              + ".")
        elif near:
            w("Within the 50 ms budget, but with less than 10% headroom at p95 in: " + "; ".join(near) + ". "
              "The learning insert is on the response path and is what spec §7.5 moves off it first if a "
              "run crosses 50 ms; p95 on a desktop host varies between runs.")
        else:
            w("Every configuration is within the 50 ms budget at p95.")
        w("")
        w("`shadow` and `enforcing, learns` take the same path in the code (profile read, audit row, "
          "learning insert) and measure the same; `enforcing, escalates` skips the learning insert, because "
          "a challenged session is not learned, and measures lower. The split between those three writes "
          "was not timed separately.")
        w("")
        moves = []
        for run in latency_runs:
            before = next((b for label, b in BEFORE_S4["latency"].items() if run["label"].startswith(label)), None)
            if before is None:
                continue
            (off50, _), (shadow50, _) = before[0], before[1]
            moves.append(f"{run['label']}: layer off {off50} → {run['rows']['off']['p50']:.1f} ms "
                         f"({(run['rows']['off']['p50'] / off50 - 1) * 100:+.0f}%), shadow {shadow50} → "
                         f"{run['rows']['shadow']['p50']:.1f} ms ({(run['rows']['shadow']['p50'] / shadow50 - 1) * 100:+.0f}%)")
        if moves:
            w(f"Against the sitting of {BEFORE_S4['date']}, p50: " + "; ".join(moves) + ". Layer off does not run "
              "the profile layer, so its move is not the profile layer's. No same-sitting comparison against the "
              "code of that date was run, so how much of the rest is the code is not established here.")
            w("")
        w("The rank change alone was timed in an earlier sitting (2026-09-17, container topology, n = 300 per "
          "configuration), running the leave-one-out and the full-conformal `profiles.py` alternately, A-B-A-B. "
          "End to end it could not be told apart from run-to-run variation: shadow p95 was 34.3 and 30.2 ms for "
          "leave-one-out, 28.3 and 31.4 ms for full conformal. `profiles.evaluate_profile` alone, 3000 calls each "
          "on 20 synthetic references, went from 1.61 to 1.70 ms p50 for a matching session and from 1.64 to "
          "1.73 ms for a deviating one.")
    w("")

    # -- what is missing -------------------------------------------------------------------------------------------
    w("## 12. The measurement that is still missing (spec §10.3)")
    w("")
    w("The browser lab cannot produce one person across twenty sessions and three device classes, so **no "
      "threshold on this page is validated**. The protocol that would: one consenting person, at least 20 "
      "sittings, on a phone, a laptop trackpad and a mouse, recorded with `record_session.py --person`. "
      "It is the prerequisite for `PROFILE_ESCALATION=1`. Until then the honest answer to a jury is: this is "
      "our only account-takeover control, it does nothing against card testing, it is computed and visible, "
      "and we have not yet earned the right to enforce it.")
    w("")
    w(f"> **{SYNTHETIC_SENTENCE}**")
    w("")
    checks = r["checks"]
    w(f"<sub>Self-checks: feature path equal to `compute_risk` on {checks['fidelity']['flushes']} flushes; "
      f"`profiles.evaluate_profile` equal to this script's independent full-conformal implementation on {checks['port']['cases']} verdicts.</sub>")
    w("")
    out[before_after_at:before_after_at] = before_after_section(lab, cells, latency_runs)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))


def before_after_section(lab: Lab, cells: dict, latency_runs: list[dict]) -> list[str]:
    """The table a reader needs first: what the two corrections since the first
    run did to the numbers. Every AFTER cell is a string the section below it
    printed; every BEFORE cell is copied from the first run (BEFORE_S4)."""
    b4 = BEFORE_S4
    r = lab.results
    sf = r["scale_floor"]
    measured = lab.measured
    out: list[str] = []
    w = out.append
    same_identities = (
        sf["total"] == b4["scale_floor"]["total"]
        and sf["zero"] == b4["scale_floor"]["zero"]
        and f"{sf['p5_positive']:.5f}" == b4["scale_floor"]["p5_positive"]
    )
    w("## Before and after: what changed since the first run")
    w("")
    w(f"This page was first generated on {b4['date']}. Two things it measured changed the code, and this run "
      "re-measures everything after both: **(1) a probation vector is no longer a reference** (section 7 measured "
      "one passed step-up shielding an attacker's later sessions), and **(2) the conformal rank is full conformal "
      "instead of leave-one-out** (section 5's calibration check measured the leave-one-out rank challenging more "
      f"legitimate sessions than full conformal on mouse and fewer on keyboard). The before column is copied from the {b4['date']} page ({b4['code']}); it was not re-run. "
      + ("Both runs drew the same synthetic identities: the rank-independent statistics of section 4 came out "
         f"identical ({sf['total']} reference sets, {sf['zero']} with zero scale, 5th percentile "
         f"{sf['p5_positive']:.5f}), so every difference below except latency is the code's. "
         if same_identities else
         "**The rank-independent statistics of section 4 differ from the first run's**, so these are not the same "
         "synthetic identities and the differences below mix code and data. ")
      + "Latency was measured in a different sitting from the before column (section 11 shows how much sittings "
      "alone move it).")
    w("")
    w("| measurement | before: " + b4["date"] + " | after: this run | changed by |")
    w("|---|---|---|---|")
    k = (0, 1, lab.profiles.PROFILE_PROBATION_MAX)
    w(f"| constants (min feature observations, scale floor, K) | {b4['constants']} | "
      f"{(measured['PROFILE_MIN_FEATURE_OBS'], measured['PROFILE_SCALE_FLOOR'], measured['PROFILE_TOP_K'])} | "
      "re-selected by the same rules |")
    for modality in lab.train_model.IDENTITY_MODALITIES:
        w(f"| same person: false challenge, {modality} (**lower bound**) | {b4['same'][modality]} | "
          f"{cells[('same', modality)]} | (2) |")
    for modality in lab.train_model.IDENTITY_MODALITIES:
        w(f"| different person: escalated, {modality} | {b4['different'][modality]} | "
          f"{cells[('different', modality)]} | (2) |")
    ks = " / ".join(str(v) for v in k)
    w(f"| attacker escalated with {ks} of his sessions learned after a passed step-up (mouse) | "
      + " / ".join(b4["poisoning"][v][0].split(" ")[0] for v in k) + " (counted as references) | "
      + " / ".join(cells[("probation", v)][0] for v in k)
      + (" (stored on probation; every profile's reference set came back identical to the one with none)"
         if all(cells[("probation", v)][2].split(" / ")[0] == cells[("probation", v)][2].split(" / ")[1] for v in k)
         else " (stored on probation; reference set identical to the one with none in "
         + ", ".join(cells[("probation", v)][2].replace(" / ", " of ") for v in k) + " profiles)")
      + " | (1) + (2) |")
    w(f"| attacker median p with {ks} learned after a passed step-up | "
      + " / ".join(b4["poisoning"][v][1] for v in k) + " | "
      + " / ".join(cells[("probation", v)][1] for v in k) + " | (1) + (2) |")
    w(f"| attacker escalated with {ks} of his sessions promoted (settled), for comparison | "
      + " / ".join(b4["poisoning"][v][0].split(" ")[0] for v in k) + " (then every learned session counted) | "
      + " / ".join(cells[("promoted", v)][0] for v in k) + " | (2) |")
    w("| rescued, then returning: next session challenged again (mouse / keyboard) | not measured; the old storage "
      "rule re-applied to this run's sessions and rank (rescued session counted as a reference): "
      + " / ".join(cells[("rescued", m)][1] for m in lab.train_model.IDENTITY_MODALITIES) + " | "
      + " / ".join(cells[("rescued", m)][0] for m in lab.train_model.IDENTITY_MODALITIES)
      + " | (1): the accepted cost |")
    for modality in lab.train_model.IDENTITY_MODALITIES:
        differ, total = cells[("calibration", modality)]
        w(f"| calibration check, {modality}: shipped rank against full conformal, same sessions | "
          f"{b4['calibration'][modality]} | shipped rank is full conformal; {differ} of {total} verdicts differ from "
          "the independent implementation | (2) |")
    w(f"| exact replay of the customer's own learned session (mouse, {len(lab.people)} profiles): escalated · "
      f"`p_value_low` <= 0.05 | {b4['replay'][0]} · {b4['replay'][1]} | {cells['replay'][0]} · {cells['replay'][1]} "
      "| (2), section 9 |")
    for label, before in b4["latency"].items():
        runs = [run for run in latency_runs if run["label"].startswith(label)]
        after = "; ".join(
            (f"{run['label'][len(label):].lstrip(', ')}: " if run["label"] != label else "")
            + " · ".join(f"{run['rows'][name]['p50']:.1f} / {run['rows'][name]['p95']:.1f}" for name in LATENCY_CONFIGS)
            for run in runs
        ) or "not re-measured in this run"
        w(f"| latency p50 / p95 ms, {label}: off · shadow · enforcing, escalates · enforcing, learns | "
          + " · ".join(f"{p50} / {p95}" for p50, p95 in before) + f" | {after} | (1) + (2), different sitting |")
    w("")
    w(f"> **{SYNTHETIC_SENTENCE}** {LOWER_BOUND}")
    w("")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Musteri davranis profili katmanini sentetik kimliklerle olcer.")
    parser.add_argument("--identities", type=int, default=200)
    parser.add_argument("--ratio-identities", type=int, default=100)
    parser.add_argument("--bot-sessions", type=int, default=300)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", help="also write the headline results as JSON")
    parser.add_argument("--latency-db", help="THROWAWAY Postgres URL; database name must contain 'profile_lab'")
    parser.add_argument("--latency-n", type=int, default=300)
    parser.add_argument("--latency-warmup", type=int, default=30)
    parser.add_argument("--latency-label", default="host")
    parser.add_argument("--latency-json", help="write this run's latency result as JSON")
    parser.add_argument("--latency-from", action="append", default=[], help="include a latency JSON from another run")
    parser.add_argument("--latency-only", action="store_true")
    args = parser.parse_args()
    _setup_imports()

    latency_runs = []
    for path in args.latency_from:
        with open(path, encoding="utf-8") as fh:
            latency_runs.append(json.load(fh))

    if args.latency_only:
        if not args.latency_db:
            parser.error("--latency-only icin --latency-db gerekli")
        result = measure_latency(args.latency_db, args.latency_n, args.latency_warmup, args.latency_label)
        if args.latency_json:
            with open(args.latency_json, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        return 0

    lab = Lab(args)
    lab.run()
    if args.latency_db:
        result = measure_latency(args.latency_db, args.latency_n, args.latency_warmup, args.latency_label)
        latency_runs.insert(0, result)
        if args.latency_json:
            with open(args.latency_json, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
    lab.results["latency"] = latency_runs
    write_markdown(lab, latency_runs, args.markdown)
    lab.log(f"wrote {args.markdown}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "measured": lab.measured,
                "headline": lab.results.get("headline"),
                "poisoning": lab.results.get("poisoning"),
                "probation_poisoning": lab.results.get("probation_poisoning"),
                "rescued_returning": lab.results.get("rescued_returning"),
                "calibration": lab.results.get("calibration"),
                "cross": lab.results.get("cross"),
                "bots": lab.results.get("bots"),
                "replay": lab.results.get("replay"),
                "min_feature_obs": lab.results["min_feature_obs"],
                "scale_floor": {k: v for k, v in lab.results["scale_floor"].items() if k != "table"},
                "top_k": lab.results["top_k"],
                "latency": latency_runs,
                "meta": lab.results["meta"],
            }, fh, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
