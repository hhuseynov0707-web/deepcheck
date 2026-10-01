import asyncio
import hashlib
import hmac
import json
import logging
import functools
import math
import os
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, distinct, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import profiles
import scorer
from database import get_db, get_sessionmaker, init_db
from lstm_model import FEATURE_NAMES
from models import (
    BehaviorData,
    CustomerProfile,
    CustomerProfileVector,
    DecisionAudit,
    ProfileAccessAudit,
    Session,
)

# The session's *official* risk_score (the badge, the 40/60/80 gating
# thresholds in the demo, the sessions list) is scorer.smooth_session_score():
# the median of the last few flushes, with a level-shift exception so a
# mid-session handover is not smoothed away. BehaviorData.risk_score (used by
# the dashboard's history chart and the sequential test) stays the raw
# per-flush value so the underlying signal is still visible for analysis.
#
# The previous flushes read before scoring: the smoothing window, of which the
# newest also serves the replay checks.
HISTORY_FETCH_ROWS = scorer.SMOOTHING_WINDOW - 1

# The dashboard re-fetches both of these every 3s per open viewer, and neither
# table is ever pruned. Unbounded reads meant the payload grew for the whole
# life of the deployment -- fine on a laptop for ten minutes, not fine for a
# demo stand running all day in front of visitors.
SESSIONS_PAGE_LIMIT = 200
HISTORY_LIMIT = 200

# --- Replay protection for /api/analyze -------------------------------------
#
# Telemetry timestamps used to be checked only for being between 1970 and
# 2100. That let an attacker record one genuine human session once and replay
# its flushes, byte for byte, under a freshly minted token before every
# fraudulent checkout: the score came out human, the token was valid, and
# /api/decision said "allow". No ML evasion was needed at all.
#
# The handler, in order:
#
#   0. A flush with NO timestamped events (only hesitation gaps, which the SDK
#      emits once the user has been idle past its 10 s window) is neither
#      checked nor stored, and answers the session's stored score as
#      provisional. It carries no behaviour -- and it used to be rejected as a
#      replay: with nothing to rebase, its fingerprint is just the gap list,
#      an idle browser (a background tab above all) produces gaps of ~2000 ms,
#      so two idle users, or two idle flushes of one user, hashed identically
#      and got 422. It deliberately does not refresh last_seen_at: freshness
#      without behaviour would let a stolen token keep a victim's old human
#      score current for /api/decision.
#   1. Clock. When the SDK sends client_sent_at (its Date.now() at send time),
#      the client's clock is only compared with itself:
#        a. Event age on the sender's clock: client_sent_at minus the newest
#           event must lie in [-MAX_FUTURE_EVENT_MS, MAX_CLOCK_SKEW_MS]. A
#           window much older than its own send is a stored window sent late.
#        b. Offset stability: server clock minus client_sent_at is stored on
#           the session at its first stored flush, and later flushes may not
#           move it by more than MAX_OFFSET_DRIFT_MS. A clock may be wrong; it
#           has to be wrong consistently.
#      Without client_sent_at (an SDK build from before the field) the newest
#      event must be within MAX_CLOCK_SKEW_MS of the SERVER clock, as before.
#      That absolute check is what 1a/1b replace, because it failed real
#      customers: measured with a client clock 20 minutes slow, every flush
#      was 422, so no session row was ever written, /api/decision answered
#      unknown_session and the customer could not pay at all.
#   2. Time must move forward within a session: the newest event of each
#      flush must not be older than the previous flush's newest event.
#   3. A clock-independent fingerprint of the telemetry (timestamps rebased to
#      the flush's first event before hashing) must not already exist in the
#      database, in ANY session.
#
# What the clock checks do NOT stop, plainly: every number they read is
# supplied by the client. A recording whose events and client_sent_at are
# both rewritten to "now" passes 1 and 2 -- as re-timed events always passed
# the absolute check, which only ever stopped replays too naive to re-time.
# 1a/1b force that rewrite to be consistent for a whole session; they do not
# stop an UNTOUCHED recording posted as a fresh session, whose first flush
# simply defines its offset. On a first flush, "this clock is an hour slow"
# and "this recording is an hour old" are indistinguishable from client
# numbers; telling them apart would take a server-issued nonce bound into each
# flush. 1b also does not notice one token shared by two NTP-synced machines,
# whose clocks agree. Check 3 is the replay defence; the clock checks are
# hygiene around it.
#
# A recording that is perturbed as well as re-timed gets past the hash. That
# is the point where replay stops being a transport problem and becomes a
# model problem (is the perturbed behaviour still human-shaped?), which the
# real-session evaluation is there to answer.
#
# 15 s: the SDK prunes every buffer to its last 10 s, so an honest window's
# newest event is at most ~10 s older than its send, plus scheduling slack.
MAX_CLOCK_SKEW_MS = 15_000

# An event stamped AFTER the flush that carried it is impossible on one clock,
# unless the wall clock was stepped backwards between the event and the send
# (an NTP correction). Not zero for that reason; small because such a step
# inside a ten-second window is rare, and a send time seconds before its own
# events was not read from the clock that stamped them.
MAX_FUTURE_EVENT_MS = 2_000

# How far one flush's (server clock - client_sent_at) may sit from the
# session's stored offset. The offset moves with request latency, not only
# with the clock: RFC 6298's 1 s initial retransmission timeout doubles on
# each loss, so a flush that has to open a new connection and loses a packet
# three times arrives 1+2+4 = 7 s late, and a mobile radio waking from idle
# adds to that. 10 s is that case with margin. A single late flush is rejected
# on its own and the next one passes; the lasting cost is a real clock step
# larger than this mid-session (or a first flush delayed by more than this),
# after which the session's flushes keep failing, it goes stale, and
# /api/decision asks for step-up -- never allow, never block.
MAX_OFFSET_DRIFT_MS = 10_000

# --- Evidence and freshness for /api/decision -------------------------------
#
# One 2-second flush is not enough behaviour to trust: a script can produce
# a single plausible window far more easily than it can sustain one. Three
# flushes is six seconds of observed behaviour and also the point at which
# the 5-flush median smoothing starts to mean something.
# A floor UNDER the sequential test, not a replacement for it.
#
# Setting this to 1 when the sequential test arrived was a regression, and the
# arithmetic shows why: SPRT_LOWER is -2.2925, so a single flush scoring 9.17
# or less already crosses into "human" and is approved.
# Telemetry is attacker-supplied, and one fabricated window is the cheapest
# thing an attacker can produce -- the whole point of the original rule was
# that sustaining six seconds of plausible behaviour is harder than minting
# one snapshot. The sequential test decides WHEN there is enough evidence;
# this decides how little evidence can ever be enough.
MIN_FLUSHES_FOR_DECISION = 3

# --- Sequential evidence ------------------------------------------------------
#
# "Three flushes" was a number chosen by judgement. It was replaced by a
# stopping rule in the SHAPE of Wald's sequential probability ratio test: a
# running sum of per-flush log-odds, log(p / (1 - p)) with p the flush's risk,
# compared against two bounds. Flushes that agree cross a bound quickly;
# flushes that disagree keep the session collecting instead of letting it
# through the moment a counter hits three. Crossing the upper bound means "the
# accumulated evidence points at a bot", crossing the lower "at a person", and
# between the two the answer is step-up verification. Neither crossing is a
# verdict on its own: the upper one makes the answer at least verify whatever
# the smoothed score says, the lower one hands the session to the 40/60/80
# ladder, which can still verify or block it (see _decide_on_evidence).
#
# It does NOT carry Wald's guarantees, and the bounds are not error rates.
# Wald's bounds give their alpha and beta for a sum of log-LIKELIHOOD RATIOS
# of INDEPENDENT observations, and neither premise holds here:
#
#  1. The flushes overlap. The SDK sends a 10 s ROLLING window every 2 s
#     (SDK_ROLLING_WINDOW_MS, SDK_FLUSH_INTERVAL_MS below), so consecutive
#     flushes share about 80% of their events and the sum counts each event
#     about five times. SPRT_EVIDENCE_FACTOR is where that would be corrected;
#     it is left at 1.0 on measurement, see there.
#  2. A flush's risk is a RandomForest vote share, not a calibrated
#     posterior, so its logit is not a likelihood ratio. A share of 0.995
#     says nearly every tree agreed, not that the behaviour is 200 times
#     likelier under "bot" than under "person".
#
# So SPRT_NOMINAL_ALPHA and SPRT_NOMINAL_BETA are only where the two numbers
# came from: Wald's formulas evaluated at 1% and 10%. What the bounds are is
# an operating point, justified by the measurement under SPRT_EVIDENCE_FACTOR
# and not by a theorem. No real customer has been measured against them.
SPRT_NOMINAL_ALPHA = 0.01
SPRT_NOMINAL_BETA = 0.10
SPRT_UPPER = math.log((1.0 - SPRT_NOMINAL_BETA) / SPRT_NOMINAL_ALPHA)  # +4.4998
SPRT_LOWER = math.log(SPRT_NOMINAL_BETA / (1.0 - SPRT_NOMINAL_ALPHA))  # -2.2925

# A per-flush score of exactly 0 or 100 would make the logit infinite and let
# one flush dominate every other. Clamped to the resolution the score actually
# carries.
SPRT_P_CLAMP = 0.005

# Mirrors of sdk/deepcheck.js DEFAULT_INTERVAL_MS and ROLLING_WINDOW_MS
# (test_sdk_window_constants_are_mirrored keeps them equal). Each flush adds
# SDK_FLUSH_INTERVAL_MS of new behaviour to a window SDK_ROLLING_WINDOW_MS
# long, so a statistic that counted every event once would weight each flush
# by their ratio, 0.2.
SDK_FLUSH_INTERVAL_MS = 2_000
SDK_ROLLING_WINDOW_MS = 10_000
SDK_NEW_EVIDENCE_PER_FLUSH = SDK_FLUSH_INTERVAL_MS / SDK_ROLLING_WINDOW_MS

# The weight of each flush's log-odds in the statistic. Compared once at 1.0
# and at SDK_NEW_EVIDENCE_PER_FLUSH (0.2), both with the upper-bound rule in
# _decide_on_evidence, the RandomForest per flush and smooth_session_score,
# deciding at the last flush of each session. SYNTHETIC sessions only -- the
# benchmark.py human slices replayed through 10 s windows every 2 s, the way
# the SDK sends them, and train_model's simulator personas, whose windows are
# drawn independently and do not overlap:
#
#                                      factor 1.0         factor 0.2
#     typical human, 300 form fills    allow 92.0%        allow 12.3%
#     keyboard only, 300               allow 95.0%        allow 69.7%
#     slow typist, 300                 allow 75.7%        allow 40.7%
#     bot, bot_sophisticated, 300 each block 100%         block 100%
#     drift_to_bot, 300                never allowed      never allowed
#     drift_to_human, 300              never allowed      never allowed
#
# ("never allowed" is at the last flush; the bot personas were not approvable
# at ANY flush. drift_to_bot is human for its first five flushes and, like any
# human, approvable from flush 3 at either factor. drift_to_human is five bot
# flushes then five human ones, the handover the upper-bound rule exists for:
# without that rule 24 of its 300 sessions, 8.0%, were allowed at factor 1.0.)
#
# The 14 held-out browser-lab runs agreed (real Chromium through the real SDK,
# but every run scripted: the H1/H2 "human" scenarios are generated motion,
# not people): all 6 human-scenario runs allowed and none of the 8 attack runs
# allowed, at either factor. 0.2 bought nothing against a bot and left seven
# in eight typical form fills without a decision at the moment of payment,
# which the Demo page answers with a "keep going" hint and then a step-up. So
# the factor stays 1.0.
#
# That comparison also shows what the factor is. Multiplying every term by 0.2
# with the bounds fixed is the same rule as keeping the terms and moving both
# bounds five times further out: counting each event once is not a correction
# to this operating point, it is a different operating point, and one that
# asks a typical customer for about three more flushes (median first
# approvable flush 4 at 1.0, 7 at 0.2). The overlap is therefore stated rather
# than "fixed": the statistic is a score-weighted count of overlapping windows
# with a stopping rule, and the bounds are where that count was measured to
# work -- on synthetic data. How real customers fare against them is
# unmeasured.
SPRT_EVIDENCE_FACTOR = 1.0

# The statistic reads only the newest SPRT_MAX_FLUSHES flushes. That is also
# the point at which "not enough yet" becomes "enough, and still
# inconclusive": between the bounds with this many flushes the internal
# reason is "ambiguous" rather than "insufficient_evidence", and it is still
# never charged without step-up.
#
# What the window means for a session that changes hands: evidence older than
# the newest ten flushes no longer counts. Seven flushes at 95 followed by up
# to six at 10 are never charged without step-up; followed by seven, the
# three automated flushes left in the window are outweighed and the session is
# approved on the smoothed score (both pinned by
# test_automated_evidence_ages_out_only_with_the_window). Stated, not
# engineered around: whatever is paying at that point has looked human for the
# last 14 s, and a script that can produce seven human-looking flushes on
# demand has defeated the per-flush model itself, which no rule layered on top
# of it can repair.
SPRT_MAX_FLUSHES = 10

# Which flushes a decision is allowed to rest on.
#
# A flush that measured none of the six STRUCTURAL features (scorer.
# BUCKET_FEATURES -- the ones that describe the generator rather than the
# sample) is not evidence in either direction. Its score is the forest reading
# neutral fallbacks plus whatever marginal features happened to be computable,
# and it was wrong both ways:
#
#   * Upward. An empty window scores 99.1, and one such flush contributes
#     log(0.991/0.009) = 4.70 > SPRT_UPPER on its own: the opening two seconds
#     of a checkout could force a step-up on a customer who had done nothing.
#   * Downward. The marginal features and the client-supplied
#     hesitation_intervals can be steered: 3000 crafted unobserved payloads
#     scored a median of 29.7, and hill-climbed ones 3.5. A session made only
#     of such flushes was charged before this gate existed -- 1600 of 1600
#     decision points (S2), and 547 of 2030 for an SDK-faithful burst script.
#
# So: the sequential statistic reads only observed flushes, and a decision
# with fewer than MIN_FLUSHES_FOR_DECISION of them is held at
# verify("insufficient_evidence") -- neither approved nor blocked (the branch
# in _decide_on_evidence says why each of those is wrong).
#
# Measured through this exact function (scratchpad gate_verify/, two rounds of
# adversarial review, 2026-09-26; OLD = every flush counted, as before this
# gate). Passing = allow or warn, i.e. charged.
#
#   SDK-faithful scripts (an emulation of deepcheck.js's buffer), passing points
#     trickle: pointer move + scroll every 2-6 s, 1 click   0/2340 ->    0/2340
#     burst: <=5 events per channel per 10 s, 1 click     547/2030 ->    0/2030
#     pointer move every 2-6 s, 1 click, 300 schedules   0 at the click -> 0
#     pointer + keydown every 2-6 s, 300 schedules       0/2331 ->    1/2331
#   composed from pre-scored payloads, passing points
#     S1 all unobserved, crafted, random knobs             883/1600 ->    0/1600
#     S2 all unobserved, crafted, hill-climbed            1600/1600 ->    0/1600
#     S6 2 observed human + unobserved automation          111/1360 ->    0/1360
#   (S1 and S2 are hand-built payloads: sendable over HTTP, not what the SDK
#   emits. The trickle and burst rows are what the SDK emits.)
#   simulator personas, 64 sessions each, prefixes 3..10: bot_sophisticated,
#   drift_to_bot and drift_to_human unchanged (0 of 640 windows unobserved);
#   bot has 60 of 640 unobserved windows, and 22 of 512 decision points go
#   from block to verify -- never to a charge.
#
#   legitimate, benchmark.py slices, synthetic, 60 seeds x prefixes 3..10:
#     no thin opening flushes                          identical in every slice
#     2-4 thin openings, blocks per 480                3-8 -> 0
#     prefix-3 checkout, 2 thin + 1 real flush: the ladder blocked 69 of 3000
#     under OLD, 75 when the gate merely skipped the test; 0 under this rule.
#     cost: a checkout decided before three flushes have observed anything
#     gets "a few more seconds" instead of a charge -- in these slices one
#     decision point per thin opening, 60 per 480 per opening, because each
#     slice body is observed from its first flush. The SDK ships a 10 s
#     rolling window, so real activity is observed for ~5 flushes once it
#     starts; the hint costs about two seconds.
#
# What this does NOT close, stated rather than engineered around: unobserved
# automation followed by, or interleaved with, THREE OR MORE human-looking
# observed flushes (S4: OLD 1333 -> 1920 of 2560 passing; S5a, alternating:
# 1 -> 300 of 800). It is not a new
# capability: the same attacker, emitting nothing during the automated part
# (the SDK sends no flush for an empty window, so there is no row), passes the
# old rule 40 of 40 at every M >= 3. Producing human-looking observed flushes
# on demand is defeating the per-flush model itself, which no rule layered on
# top of it can repair -- the same limit stated at SPRT_MAX_FLUSHES.
#
# And the cheapest way to do exactly that is not motor mimicry. Typing the card
# with randomised gaps between keydowns (time.sleep(random.uniform(...)), no
# pointer at all) produces observed flushes -- the two key-channel structural
# features -- that the forest scores as human: a 23-keydown card entry was
# charged 119 of 120 times, under OLD and under this rule alike (constant
# timing is caught, 0 of 120). That is a hole in the model, not in this gate,
# and requiring observed flushes does not raise its price. Documented in
# docs/evaluation.md; nothing here claims to close it.
@functools.cache
def _structural_bits() -> int:
    """measured_mask bits of the structural features, computed on first use.

    Not a module constant: main's import-time surface from scorer is kept to
    SMOOTHING_WINDOW, which is what the boot tests stub (test_profiles
    _BOOT_SCRIPT) so that configuration can be exercised without loading a
    model bundle.
    """
    return sum(1 << FEATURE_NAMES.index(name) for name in scorer.BUCKET_FEATURES)


def _observed_a_generator(measured_mask: int | None) -> bool:
    """Did this flush measure at least one structural feature?

    A NULL mask is a row written before the column existed. It is kept: "we
    do not know what this flush measured" must not silently become "it
    measured nothing", which would quietly drop old evidence.
    """
    if measured_mask is None:
        return True
    return bool(measured_mask & _structural_bits())

# --- Cross-session clustering -----------------------------------------------
#
# The measured hole in per-session scoring is that competent mimicry is
# human-shaped by construction. What mimicry cannot avoid is repeating itself:
# a script run many times produces many nearly identical behavioural
# signatures, and people do not. Counting how many DISTINCT sessions share a
# behaviour bucket inside a short window turns that repetition into a signal
# the model never sees.
#
# It can only escalate, never approve. A cluster is evidence of automation, but
# the absence of one is not evidence of a person.
#
# MEASURED LIMITATION, stated because it decides how much weight this deserves.
# Against the adversarial harness this catches bots that repeat themselves and
# does NOT catch a bot that randomises its own parameters between runs: 25
# sessions of an independently written humanised generator produced 30 distinct
# buckets, the same spread as 25 human sessions. Behavioural quantisation
# identifies a *kind of motion*, not a *particular script*. What the industry
# actually clusters on is identity -- device fingerprint, TLS signature, IP --
# which is far more stable across runs than behaviour is.
#
# So the threshold is set where it does not fire on the measured human
# population, and the mechanism is here for the non-randomising farms it does
# catch, and as the place identity signals would attach later. It is not the
# answer to mimicry.
CLUSTER_WINDOW_S = 900
CLUSTER_MIN_SESSIONS = 6

# OFF by default, on the evidence. Measured against the adversarial harness at
# every threshold tried, the escalation cost more legitimate sessions than it
# caught bots: at 4 peers it challenged 40% of humans to catch 60% of mimics,
# and at 6 peers it challenged 8% of humans to catch 4%. A control that flags
# more customers than attackers is worse than no control, and challenging a
# real customer costs a sale while missing one bot costs one attempt.
#
# The bucket is still computed and stored, because it costs almost nothing, it
# is the natural attach point for identity signals (device, TLS, IP) which are
# what actually cluster, and because leaving the measurement in place is how
# the decision gets revisited when there is real traffic to revisit it with.
CLUSTER_ESCALATION_ENABLED = os.getenv("CLUSTER_ESCALATION", "0").strip() == "1"

# A verdict is about the behaviour that produced it, and that behaviour must
# be current. Without this, a token lifted from a shared machine (or via XSS
# on the merchant page) could be cashed in an hour later on the strength of
# the real customer's earlier browsing.
DECISION_MAX_AGE_S = 30

# How long a successful step-up verification keeps upgrading "verify" to
# "allow". Long enough to finish the checkout, short enough not to become a
# standing bypass.
VERIFICATION_VALID_S = 300

# Fixed demo step-up code. The real integration replaces /api/demo/verify with
# the merchant's SMS / 3-D Secure provider; the demo shows the *pattern*
# (verification recorded on the server, never asserted by the browser) and
# prints this code in the modal so a jury can see it is a deliberate demo
# value, not an "any six digits" bypass.
DEMO_VERIFY_CODE = os.getenv("DEMO_VERIFY_CODE", "482913").strip()

# --- Runtime attestation -----------------------------------------------------
#
# Everything the detector scores is a summary statistic of numbers the client
# supplies, and an adversarial harness that never opened a browser -- it signed
# its own tokens and posted JSON -- scored 10.5 against a human 11.3. No amount
# of work on the features answers that, because the features are computed from
# whatever the client chose to send.
#
# Attestation does NOT answer that, and an earlier version of this comment
# claimed it did ("attestation closes the post-JSON-directly path"). What the
# two checks below establish, and nothing more:
#
#   * some client performed a proof of work for THIS session id within
#     POW_CHALLENGE_TTL_S of its challenge being minted, and
#   * the two runtime values that client REPORTED lie inside ranges a browser
#     can produce.
#
# What they do not establish: that the client is a browser, that it executed
# sdk/deepcheck.js, or that a person is present. A script that has read the SDK
# passes both. Measured 2026-09-19 on the development laptop, against the real
# handlers through TestClient: a plain Python client -- hashlib for the work,
# the hard-coded values clock_resolution_us=100 and timer_lag_ms=4, no browser
# and no SDK -- obtained a valid token in 50 of 50 attempts, median 16 ms end
# to end, of which the proof of work took 6.5 ms (the suite's
# test_token_requires_proof_of_work_and_plausible_timers does the same with
# hashlib). So the harness quoted above is not stopped here; it pays a few
# milliseconds per session.
#
# PROOF OF WORK. The server issues a signed challenge and the client must find
# a nonce whose SHA-256 has POW_DIFFICULTY_BITS leading zero bits. That shows
# work was done for this session, not WHICH code did it: SHA-256 exists in
# every language. What it imposes is a per-session cost -- 2^12 = 4096 hashes
# expected at the default -- linear in the number of sessions minted, which is
# a rate and not a barrier, and it taxes the customer more than the script:
# TECHNICAL_GUIDE.md records about 75 ms in Chromium (not re-measured here)
# against the 6.5 ms above.
#
# RUNTIME MEASUREMENTS. Browsers clamp performance.now() (Chrome to 100
# microseconds, measured below; Firefox and Safari document a coarser clamp)
# and setTimeout(0) never fires in zero milliseconds. The server receives
# these as two SELF-REPORTED numbers. The bounds reject values no browser
# produces -- an unclamped clock, a zero scheduling lag -- which catches a
# client that did not think about them at all, and nothing else: numbers
# copied from a real browser, or read off the bounds below, pass.
#
# And a bot driving a real browser (Playwright) produces a real proof of work
# and real timer values, so it passes for the most honest of reasons. The
# mechanism is kept because it is cheap and forces a scripted client to
# implement the protocol, not because it separates scripts from browsers.
POW_DIFFICULTY_BITS = int(os.getenv("POW_DIFFICULTY_BITS", "12"))

# How long a challenge stays solvable. Long enough for a slow phone, short
# enough that a solved challenge cannot be stockpiled. It also bounds how long
# a session can be issued tokens at all: re-attesting needs the challenge.
POW_CHALLENGE_TTL_S = 180

# Plausible ranges for the runtime measurements. Deliberately wide: the point
# is to reject values that no browser produces, not to fingerprint which
# browser this is. A clamp finer than half a microsecond means the client is
# not subject to any clamp at all.
MIN_CLOCK_RESOLUTION_US = 0.5
MAX_CLOCK_RESOLUTION_US = 5000.0
# Measured in Chromium on an idle loop: a median of 0.1 ms, and the clock clamp
# came back as exactly 100.0 microseconds, which is Chrome's documented value.
# The floor sits an order of magnitude below the observed lag, because the job
# is to reject a fabricated zero rather than to insist on a particular
# scheduler -- a tight bound would fail real users on a fast machine, and a
# false rejection here costs a customer.
MIN_TIMER_LAG_MS = 0.01
MAX_TIMER_LAG_MS = 250.0

# The gate is token issuance itself: /api/session hands out a challenge and
# nothing else, so a client that has not passed the two checks above never
# obtains the token that /api/analyze requires. No separate flag and no stored
# state. Holding a valid token therefore means exactly what the checks mean --
# some client did the work for this session and reported plausible numbers --
# and nothing more.
#
# The obvious caveat, said out loud: in DEBUG the signing secret is a published
# constant, so anything that reads the source can mint its own token and skip
# all of this. That is true of every token check in the system and is why
# DEBUG=0 refuses to start without a real secret.

# --- Session tokens ----------------------------------------------------------
#
# A token is "<issued_s>.<64 hex>": the second it was issued, and an HMAC over
# the session id AND that second, so a holder can neither move it to another
# session nor extend it. It is valid for SESSION_TOKEN_TTL_S.
#
# It used to be HMAC(session_id) and nothing else, which never expired: one
# attestation, or one token read out of a log, a HAR file or a shared machine,
# posted telemetry and requested decisions for that session id for as long as
# the signing secret lived. Because /api/analyze creates the session row on the
# first flush, such a token could even recreate a row the retention sweep had
# deleted. Now a session id can be used at all -- telemetry, decisions,
# step-up -- for at most POW_CHALLENGE_TTL_S + SESSION_TOKEN_TTL_S after it was
# minted (33 minutes): a new token needs the session's challenge, which stops
# being accepted after POW_CHALLENGE_TTL_S.
#
# 30 minutes is CHOSEN, not measured -- there are no real checkouts to measure.
# Expiring too early has a bounded but real cost: the SDK answers a 401 from
# /api/analyze by registering a NEW session once and resending the window
# (sdk/deepcheck.js, reregisterAndResend), and Demo.jsx awaits
# DeepCheck.flush() before it charges, so the charge normally goes out under
# the new session -- but that session starts with no evidence (the decision
# waits for MIN_FLUSHES_FOR_DECISION flushes) and without any step-up recorded
# on the old one. So the TTL sits well past one checkout sitting: six times
# VERIFICATION_VALID_S, the window a passed step-up is honoured for. Expiring
# too late costs only the window a leaked token stays usable, and 33 minutes is
# far below the default ROW_RETENTION_HOURS (24 h), so a token cannot outlive
# its session's row. A deployment whose checkouts routinely take longer raises
# this; the SDK handles either value without change.
SESSION_TOKEN_TTL_S = 30 * 60

# A token whose issue second lies in the future was minted by a server whose
# clock is ahead of this one (several replicas), or before this clock stepped
# back. Tolerated up to this much so small skew between replicas does not 401
# every fresh token; beyond it the token is refused, so a mis-set clock cannot
# mint tokens that outlive SESSION_TOKEN_TTL_S by more than this. Chosen, not
# measured.
SESSION_TOKEN_CLOCK_LEEWAY_S = 60

# Domain tags for the one signing key. See _mac.
_TOKEN_DOMAIN = "token"
_CHALLENGE_DOMAIN = "challenge"

# Checked before any cryptography, so a malformed header -- including one with
# non-ASCII characters, on which compare_digest(str, str) raises TypeError -- is
# a 401 and never a 500. Any other spelling of an issue second (a leading zero)
# needs no rule of its own: the MAC covers the digits as written.
_TOKEN_RE = re.compile(r"([0-9]{1,12})\.([0-9a-f]{64})")


def _mac(domain: str, *fields: str) -> str:
    """HMAC-SHA256 under SECRET over one domain-tagged, length-prefixed message.

    SECRET signs two kinds of object, session tokens and proof-of-work
    challenges. They used to be HMACs with the same key over "<session_id>" and
    "<session_id>.<issued_ms>", so what kept a challenge signature from being
    accepted as the token of the session id "<session_id>.<issued_ms>" was only
    that one was truncated to 32 hex characters and the other was not -- an
    accident of formatting, not a rule. Every message now starts with the kind
    of object it signs and gives each field's length, so no token message can
    equal a challenge message, or another token's, whatever a session id
    contains.
    """
    message = domain + "".join(f"|{len(field)}:{field}" for field in fields)
    return hmac.new(SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_challenge(session_id: str) -> str:
    """A challenge the server can verify without storing anything.

    Carries the session it belongs to and the moment it was minted, signed, so
    a solution cannot be moved to another session or replayed after it expires.
    The format is unchanged and the SDK treats it as opaque; only the signed
    message is domain-separated (see _mac).
    """
    issued_ms = int(time.time() * 1000)
    signature = _mac(_CHALLENGE_DOMAIN, session_id, str(issued_ms))[:32]
    return f"{session_id}.{issued_ms}.{signature}"


def _check_challenge(session_id: str, challenge: str) -> None:
    parts = challenge.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Gecersiz dogrulama sorusu")
    challenge_session, issued_raw, signature = parts
    expected = _mac(_CHALLENGE_DOMAIN, challenge_session, issued_raw)[:32]
    # Bytes, like require_merchant: compare_digest on a str with a non-ASCII
    # character raises TypeError, a 500 for a malformed request body.
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8")):
        raise HTTPException(status_code=400, detail="Dogrulama sorusu imzasi gecersiz")
    if challenge_session != session_id:
        raise HTTPException(status_code=400, detail="Dogrulama sorusu bu oturuma ait degil")
    try:
        issued_ms = int(issued_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Gecersiz dogrulama sorusu") from None
    if abs(int(time.time() * 1000) - issued_ms) > POW_CHALLENGE_TTL_S * 1000:
        raise HTTPException(status_code=400, detail="Dogrulama sorusunun suresi doldu")


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def _check_proof_of_work(challenge: str, nonce: str) -> None:
    digest = hashlib.sha256(f"{challenge}.{nonce}".encode()).digest()
    if _leading_zero_bits(digest) < POW_DIFFICULTY_BITS:
        raise HTTPException(status_code=400, detail="Is kaniti gecersiz")


def _check_runtime(runtime: "RuntimeMeasurements") -> None:
    """Reject reported values no browser engine produces.

    Wide bounds on purpose. This is not a browser fingerprint, and it is not
    evidence of a browser either: the values are self-reported, so it only
    checks that they COULD have come from a clock that is actually clamped and
    an event loop that actually costs something to schedule on. Any client
    that sends numbers inside these bounds passes.
    """
    if not (MIN_CLOCK_RESOLUTION_US <= runtime.clock_resolution_us <= MAX_CLOCK_RESOLUTION_US):
        raise HTTPException(
            status_code=400,
            detail="Calisma zamani olcumleri bir tarayiciyla tutarsiz (saat cozunurlugu)",
        )
    if not (MIN_TIMER_LAG_MS <= runtime.timer_lag_ms <= MAX_TIMER_LAG_MS):
        raise HTTPException(
            status_code=400,
            detail="Calisma zamani olcumleri bir tarayiciyla tutarsiz (zamanlayici gecikmesi)",
        )

# The /api/demo/* endpoints are a demonstration of the merchant-side pattern,
# not a payment integration. The step-up code is a fixed constant that the demo
# page prints on screen, so anything the server answers `verify` for can be
# upgraded to `allow` by anyone who reads it. That is the point in a demo and
# unacceptable anywhere else, so they are enabled only in DEBUG unless someone
# turns them on deliberately.
# Reads DEBUG from the environment rather than the module constant, which is
# defined further down; the default is simply "whatever DEBUG says".
DEMO_ENDPOINTS_ENABLED = (
    os.getenv("DEMO_ENDPOINTS", os.getenv("DEBUG", "0")).strip() == "1"
)

# Whether /api/analyze returns its SHAP breakdown to the client being scored.
#
# It should not. The response goes to the party under assessment, and naming
# the three features driving their score hands them a tuning signal: submit,
# read which feature convicted you, adjust, repeat. That is a supervised
# optimisation loop against the live detector, and the adversarial run used
# exactly it to build a bot that scores lower than real humans. The SOC
# dashboard still gets the full explanation from GET /api/score/{id}, which is
# behind DASHBOARD_KEY, and the explanation is still stored on every row.
#
# Default off. Set SHAP_IN_ANALYZE=1 only for a walkthrough where showing the
# reasoning live matters more than withholding it.
SHAP_IN_ANALYZE = os.getenv("SHAP_IN_ANALYZE", "0").strip() == "1"


def _require_demo_endpoints() -> None:
    if not DEMO_ENDPOINTS_ENABLED:
        raise HTTPException(
            status_code=404, detail="Demo uc noktalari bu dagitimda kapali"
        )

# --- Rate limiting -----------------------------------------------------------
#
# Every /api/analyze call is ~50 ms of CPU in a threadpool, so a single client
# looping on it saturates every worker; unlimited /api/session minting fills
# the sessions table for free. Both were unbounded.
#
# Deliberately a small in-process sliding window rather than a library: the
# usual choices (slowapi and friends) also keep their counters in process
# memory unless a Redis backend is configured, so they would buy a pinned
# dependency and the same semantics. Two consequences are stated rather than
# hidden:
#   * Counters are PER WORKER. entrypoint.sh runs UVICORN_WORKERS (default 2;
#     it was 4, and the aggregates below were worked out for 4), so the
#     effective limit across the service is up to that many times what is
#     configured here. The limits below are chosen so that is still a useful
#     ceiling.
#   * Counters are lost on restart. That is acceptable for abuse control; it
#     would not be for billing or quota.
# A shared Redis backend is the upgrade path when there is more than one host.
#
# The /api/analyze limit is keyed by SESSION ID, not by IP: a demo stand or an
# office puts many genuine users behind one address, and an IP limit there
# would blind the detector for everyone. Minting is what is keyed by IP, so
# the two compose -- an attacker needs a new session per 60 flushes and is
# limited in how fast new sessions can be created. "IP" is the address
# _client_ip resolves: behind the bundled nginx proxy that is nginx's own
# address for every browser, i.e. one shared minting bucket, unless
# FORWARDED_ALLOW_IPS names the proxy (see _client_ip).
# Read these as PER WORKER: with UVICORN_WORKERS=4 the aggregate ceiling is
# four times each number, because a request lands on whichever worker accepts
# it. Measured on the running stack (then at 4 workers): 30 consecutive mints
# from one address all returned 201, which is the arithmetic working as
# described, not the limiter failing. The numbers below were chosen for the
# AGGREGATE they produce at 4 workers; at today's default of 2 it is half.
RATE_LIMITS = {
    # bucket: (max requests per worker, window seconds)   -> aggregate at 4 workers
    "session": (10, 60),  # page loads per IP             -> 40/min
    "analyze": (60, 60),  # SDK sends 30/min per session  -> generous headroom
    "decision": (20, 60),  # checkout attempts per session -> 80/min
    # Profile-layer decisions naming ONE CUSTOMER, keyed by that customer's
    # profile id (merchant-namespaced). Exhausting it never refuses the
    # decision: the layer stops reading the profile and, when enforcing, asks
    # for step-up (_load_profile_context). What it bounds is probing one
    # customer's behavioural envelope -- after this many answers in an hour
    # every further answer about that customer is "verify", which says
    # nothing. 60 an hour per worker is judgement, not measurement: no real
    # customer checks out that often.
    #
    # It used to be keyed by MERCHANT (300/h), and exhausting it answered 429
    # for the whole decision, Random Forest verdict included: one logged-in
    # user pressing pay 600 times -- 15 minutes inside the per-session bucket
    # at the default 2 workers -- left every profiled checkout of that
    # merchant without a DeepCheck decision for up to an hour, and a busy
    # merchant hit it with no attacker at all. What it was meant to bound --
    # probing which references exist -- needs attacker-chosen references, and
    # those come only from a merchant server holding the merchant key.
    "profile": (60, 3600),
    # Consent, erase and outcome, keyed by MERCHANT. Every one of these calls is
    # already authenticated, so this is not abuse control: it is a ceiling on
    # what a runaway merchant integration (a retry loop) can do to the
    # database. Judgement, not measurement -- 2400/min aggregate at 4 workers,
    # which a consent backfill paced below 40/s never meets.
    "profile_admin": (600, 60),
}

# Bound the limiter's own memory: an attacker rotating keys must not be able
# to grow this dictionary without limit. Past the cap, entries whose window has
# fully expired are dropped, and if that frees nothing the oldest are.
_RATE_KEY_CAP = 20_000
_rate_hits: dict[tuple[str, str], deque[float]] = {}


def _rate_limit(bucket: str, key: str) -> None:
    """Sliding-window limiter. Raises 429 when the window is full."""
    retry_after = _rate_take(bucket, key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Cok fazla istek gonderildi, lutfen biraz bekleyin",
            headers={"Retry-After": str(retry_after)},
        )


def _rate_allows(bucket: str, key: str) -> bool:
    """The same limiter for a caller that degrades instead of refusing."""
    return _rate_take(bucket, key) is None


def _rate_take(bucket: str, key: str) -> int | None:
    """Records one hit and returns None, or returns the Retry-After seconds
    when the window is full (and records nothing)."""
    limit, window = RATE_LIMITS[bucket]
    now = time.monotonic()
    cutoff = now - window

    hits = _rate_hits.get((bucket, key))
    if hits is None:
        if len(_rate_hits) >= _RATE_KEY_CAP:
            _evict_rate_keys(now)
        hits = _rate_hits.setdefault((bucket, key), deque())

    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        return max(1, int(hits[0] + window - now) + 1)
    hits.append(now)
    return None


def _evict_rate_keys(now: float) -> None:
    dead = [k for k, hits in _rate_hits.items() if not hits or hits[-1] < now - RATE_LIMITS[k[0]][1]]
    for k in dead:
        _rate_hits.pop(k, None)
    if len(_rate_hits) >= _RATE_KEY_CAP:
        # Nothing had expired: drop the least recently touched half rather
        # than growing without bound or refusing all traffic.
        oldest = sorted(_rate_hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
        for k, _ in oldest[: len(oldest) // 2]:
            _rate_hits.pop(k, None)


def _client_ip(request: Request) -> str:
    """The peer address, deliberately NOT X-Forwarded-For.

    That header is attacker-controlled unless a trusted proxy is known to
    rewrite it, and honouring it blindly turns a per-IP limit into no limit
    at all. Trust is uvicorn's job, not this function's: its proxy-headers
    middleware is on by default and replaces request.client with the
    X-Forwarded-For address only when the connecting peer is listed in
    FORWARDED_ALLOW_IPS (default 127.0.0.1).

    In the bundled deployment every browser request arrives through the
    frontend's nginx (/api/ is proxied, frontend/nginx.conf), so the peer is
    the nginx container. Unless FORWARDED_ALLOW_IPS is set to that
    container's address, this returns nginx's address for every visitor and
    the per-IP minting limit is ONE bucket shared by all of them. nginx
    overwrites X-Forwarded-For with the address it saw, so trusting it does
    not let a client choose its own key. uvicorn 0.30.1 (the pinned version)
    matches exact addresses only -- no CIDR -- and "*" must never be used: it
    takes the leftmost entry, which the caller writes. On Docker Desktop the
    address nginx sees may itself be a gateway for every LAN client; that has
    not been checked.
    """
    return request.client.host if request.client else "unknown"


# --- Retention ---------------------------------------------------------------
#
# Every flush stores its full raw telemetry, up to 2000 mouse points, and
# nothing was ever deleted: a stand running all day grew without limit, and
# behavioural recordings of real people accumulated indefinitely, which is a
# privacy question before it is a disk question. The features and the score
# are what analysis needs; the raw JSON is only needed long enough for
# record_session.py to freeze a labelled session.
RAW_TELEMETRY_RETENTION_HOURS = float(os.getenv("RAW_RETENTION_HOURS", "1"))
ROW_RETENTION_HOURS = float(os.getenv("ROW_RETENTION_HOURS", "24"))
RETENTION_SWEEP_S = 600

# Only one worker should sweep. Advisory lock, same mechanism init_db uses.
_RETENTION_LOCK_KEY = 728_302

# Per-customer profile retention, on clocks of its own. None of these rows is
# raw telemetry, and each has a different reason to exist for longer than 24h:
#   * a profile and its <=20 vectors ARE the statistic, and a customer who
#     pays once a month would otherwise never reach maturity. Deleted after
#     this many days with no decision (last_seen_at), vectors individually
#     after the same age. An objection tombstone is never swept -- it is what
#     stops a refused profile from being recreated.
#   * a decision audit row is the evidence a human reviewer needs to answer a
#     contested decision (KVKK 11(1)(g), GDPR Art. 22(3)) after the telemetry
#     behind it is long gone.
#   * an access audit row records who read special-category data; it is the
#     accountability trail, so it outlives what it audits.
#   * EXCEPT in the demo namespace (merchant "demo", /api/demo/charge): what
#     a demo visitor leaves there -- a vector learned from their session, the
#     implicit demo profile their typed reference created, the decision audit
#     rows -- is deleted on the SESSION's clock, ROW_RETENTION_HOURS. Nobody
#     consented to anything on the demo page and there is no erasure route
#     into the reserved namespace (require_merchant never returns "demo"), so
#     nothing there may outlive the session it came from. The seeded SYNTHETIC
#     customers (demo_seed.py) are not a visitor's data and keep their
#     history; demo_seed.py --reset manages them.
# These are policy defaults to be confirmed in the DPIA, not measurements.
PROFILE_IDLE_RETENTION_DAYS = float(os.getenv("PROFILE_RETENTION_DAYS", "180"))
DECISION_AUDIT_RETENTION_DAYS = float(os.getenv("DECISION_AUDIT_RETENTION_DAYS", "90"))
PROFILE_ACCESS_AUDIT_RETENTION_DAYS = float(os.getenv("PROFILE_ACCESS_RETENTION_DAYS", "365"))
# A lock of its own, so a slow profile pass never makes a worker skip the
# telemetry sweep (or the other way round).
_PROFILE_RETENTION_LOCK_KEY = 728_303
# Idle profiles are released in batches: an IN list is one bind parameter per
# id, and asyncpg refuses a statement with more than 32767 parameters.
_PROFILE_SWEEP_BATCH = 1000

logger = logging.getLogger("deepcheck")
# Nothing in the project configured logging, so Python's last-resort handler
# printed WARNING and above and every INFO line of the "deepcheck*" loggers
# was dropped -- the profile escalation lines section 11.1 requires, the
# retention sweep's counts, the conformal guard's state when it actually
# works. uvicorn configures only its own loggers. One handler here, only if
# none is attached yet (a host application or a test may have its own).
# INFO lines name a session id at most; profile ids and customer references
# never reach any log line (section 11.1).
if not logger.handlers:
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_log_handler)
    logger.setLevel(logging.INFO)

# DEBUG=1 is the local `docker-compose up` / laptop-demo mode. It is the ONLY
# mode in which the process is allowed to fall back to a hard-coded signing
# secret, and it says so loudly in the log on every boot.
DEBUG = os.getenv("DEBUG", "0").strip() == "1"

# Deliberately fixed rather than randomly generated per process: entrypoint.sh
# runs 4 uvicorn workers, each with its own interpreter. A per-process random
# secret would mean a token minted by worker 1 fails verification on worker 2,
# i.e. random 401s under exactly the concurrency a demo produces.
_DEV_SECRET = "deepcheck-dev-secret-yalnizca-yerel-kullanim"
# Rotated when the key stopped being compiled into the frontend bundle: every
# build published before that shipped the old value to anyone who opened the
# dashboard, so it has to be treated as burned.
_DEV_DASHBOARD_KEY = "deepcheck-dev-pano-anahtari-2026"


def _load_secret(env_name: str, dev_fallback: str, purpose: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    if DEBUG:
        logger.warning(
            "UYARI: %s tanimlanmamis, DEBUG modunda sabit gelistirme degeri "
            "kullaniliyor (%s). Uretimde bu deger MUTLAKA ayarlanmalidir.",
            env_name,
            purpose,
        )
        return dev_fallback
    # Refusing to start is the point: a missing secret must never degrade into
    # "authentication is effectively off", which is what a silent default
    # would do. The process dies here rather than serving unsigned sessions.
    raise RuntimeError(
        f"{env_name} ortam degiskeni tanimli degil. Uretimde zorunludur "
        f"({purpose}). Yerel demo icin DEBUG=1 ayarlayin."
    )


SECRET = _load_secret("DEEPCHECK_SECRET", _DEV_SECRET, "oturum jetonu imzalama")
DASHBOARD_KEY = _load_secret("DASHBOARD_KEY", _DEV_DASHBOARD_KEY, "SOC panosu erisimi")


# --- Per-customer profile: merchant credential, pseudonym key, flags ---------
#
# Everything below defaults to OFF, and unlike the two secrets above none of it
# has a development fallback in any mode. The local demo needs none of it: with
# these variables unset every endpoint behaves exactly as it did before the
# layer existed.

# A session token proves "this browser ran the SDK"; it says nothing about who
# the customer is. A customer reference is asserted by the MERCHANT's backend,
# so it is only accepted alongside a credential that identifies that merchant --
# otherwise anyone holding a session token could name any customer and probe
# their profile.
#
# Per-merchant credentials, "id:key" pairs, comma separated:
#   DEEPCHECK_MERCHANT_KEYS="acme:AbC...32+,globex:XyZ...32+"
# id:  ^[a-z0-9][a-z0-9_-]{0,31}$      key: >= 32 printable ASCII, no spaces
_MERCHANT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_MERCHANT_KEY_RE = re.compile(r"[\x21-\x7e]{32,}")
# The demo page's profiles live in this namespace (spec 5.7), so no configured
# merchant may claim it: a merchant called "demo" would share every demo
# customer's pseudonym.
DEMO_MERCHANT_ID = "demo"


def _load_merchant_keys(env_name: str, *, reserved_ids=(DEMO_MERCHANT_ID,), other_secrets=()) -> dict[str, str]:
    """Parse an "id:key,id:key" credential list at boot -- the merchant list,
    and the review-operator list, which uses the same format.

    Raises on any malformed entry IN EVERY MODE, DEBUG included. A typo here
    must not quietly become "this merchant does not exist" -- that is a
    configuration error that would surface as 401s at a merchant's checkout
    instead of at deploy time. An EMPTY variable is not an error: it means no
    merchants, which means the profile layer is off.

    Error messages name the entry's POSITION, never its text: the most common
    malformed entry is a key pasted in the wrong place, and a boot error is
    copied into every log aggregator the deployment has.
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return {}
    keys: dict[str, str] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        merchant_id, separator, key = entry.strip().partition(":")
        if not separator or not _MERCHANT_ID_RE.fullmatch(merchant_id):
            raise RuntimeError(
                f"{env_name}: {position}. kayit gecersiz. Bicim 'kimlik:anahtar' olmali; "
                "kimlik kucuk harf veya rakamla baslar, en fazla 32 karakterdir "
                "(a-z, 0-9, '_', '-')."
            )
        if not _MERCHANT_KEY_RE.fullmatch(key):
            raise RuntimeError(
                f"{env_name}: {position}. kaydin anahtari en az 32 karakter olmali ve "
                "yalnizca bosluksuz yazdirilabilir ASCII karakterlerden olusmalidir."
            )
        if merchant_id in reserved_ids:
            raise RuntimeError(
                f"{env_name}: '{merchant_id}' kimligi demo sayfasi icin ayrilmistir."
            )
        if merchant_id in keys:
            raise RuntimeError(f"{env_name}: '{merchant_id}' kimligi birden fazla kez tanimli.")
        # A key shared with another merchant lets one act as the other; a key
        # equal to a signing secret or the dashboard key hands whoever holds
        # that secret the merchant's powers too (erasing customers' profiles).
        if key in keys.values() or key in (SECRET, DASHBOARD_KEY, _DEV_SECRET, _DEV_DASHBOARD_KEY, *other_secrets):
            raise RuntimeError(
                f"{env_name}: {position}. kaydin anahtari ayni listedeki baska bir anahtarla "
                "veya baska bir sirla ayni olamaz."
            )
        keys[merchant_id] = key
    return keys


MERCHANT_KEYS: dict[str, str] = _load_merchant_keys("DEEPCHECK_MERCHANT_KEYS")


def _load_profile_key() -> bytes | None:
    """The HMAC key that turns (merchant, customer reference) into profile_id.

    Deliberately NOT _load_secret, and there is no _DEV_SECRET branch: an unset
    key means the layer stays off, in DEBUG as much as anywhere else. In DEBUG
    the signing secret is a published constant, so a demo built on it would let
    anyone recompute profile_id from a raw reference -- pseudonymisation worth
    nothing.

    The honest trade, written down because it is not free: an HMAC pseudonym
    cannot be re-keyed without the raw references, which this system
    deliberately never stores. This key is therefore long-lived BY DESIGN, and
    its compromise re-identifies the whole table. That is exactly why it must
    not also be the token key, the dashboard key or a merchant's key.
    """
    value = os.getenv("DEEPCHECK_PROFILE_KEY", "").strip()
    if not value:
        return None
    if value in (_DEV_SECRET, _DEV_DASHBOARD_KEY, SECRET, DASHBOARD_KEY) or value in MERCHANT_KEYS.values():
        raise RuntimeError("DEEPCHECK_PROFILE_KEY baska bir sirla ayni olamaz")
    return value.encode("utf-8")


def _load_profile_key_version() -> int:
    """Written to customer_profiles.key_version. A rotation re-derives every
    profile_id and orphans every profile (see .env.example); the version exists
    so a future rotation can dual-write for a window, and a profile stamped
    with a different version is never compared."""
    raw = os.getenv("DEEPCHECK_PROFILE_KEY_VERSION", "").strip() or "1"
    try:
        version = int(raw)
    except ValueError:
        raise RuntimeError("DEEPCHECK_PROFILE_KEY_VERSION bir tam sayi olmalidir") from None
    # SmallInteger column.
    if not 1 <= version <= 32767:
        raise RuntimeError("DEEPCHECK_PROFILE_KEY_VERSION 1 ile 32767 arasinda olmalidir")
    return version


PROFILE_KEY: bytes | None = _load_profile_key()
PROFILE_KEY_VERSION: int = _load_profile_key_version()

# Per-operator credentials for GET /api/profile/review/{session_id}, the human
# review surface (GDPR Art. 22(3), KVKK 11(1)(g)). Same "id:key,id:key" format
# as the merchant list. Deliberately NOT the dashboard key: that key is a
# shared password for a triage screen, and twenty behavioural vectors per
# customer are a template, not a triage summary. Every read is written to
# profile_access_audit under the operator id, which a shared key could not
# attribute to anyone. Empty means no one can review; it does not disable the
# layer. Not gated on PROFILE_LAYER either: a contested decision must stay
# reviewable after the layer is switched off.
PROFILE_REVIEW_KEYS: dict[str, str] = _load_merchant_keys(
    "PROFILE_REVIEW_KEYS",
    reserved_ids=(),
    other_secrets=(*MERCHANT_KEYS.values(), *((PROFILE_KEY.decode("utf-8"),) if PROFILE_KEY else ())),
)

# PROFILE_LAYER is the master switch (compute, store, audit, learn).
# PROFILE_ESCALATION turns the computed opinion into an action, and is ignored
# unless PROFILE_LAYER is on. Both default OFF, following CLUSTER_ESCALATION --
# and for a stronger reason: the cluster rule had adversarial measurements
# behind it, and this layer has no measurement on real customers at all.
PROFILE_LAYER = os.getenv("PROFILE_LAYER", "0").strip() == "1"
PROFILE_ESCALATION = os.getenv("PROFILE_ESCALATION", "0").strip() == "1"
PROFILE_ENABLED = PROFILE_LAYER and PROFILE_KEY is not None and bool(MERCHANT_KEYS)

if PROFILE_LAYER and not PROFILE_ENABLED:
    # Variable names only. Loud, but not fatal: a misconfigured layer must not
    # take a merchant's checkout down with it, and "off" is the safe state of
    # an escalation-only control.
    logger.warning(
        "UYARI: PROFILE_LAYER=1 fakat %s tanimli degil; musteri profili katmani KAPALI.",
        " ve ".join(
            name
            for name, present in (
                ("DEEPCHECK_PROFILE_KEY", PROFILE_KEY is not None),
                ("DEEPCHECK_MERCHANT_KEYS", bool(MERCHANT_KEYS)),
            )
            if not present
        ),
    )
if PROFILE_ESCALATION and not PROFILE_LAYER:
    logger.warning("UYARI: PROFILE_ESCALATION=1 yok sayiliyor, cunku PROFILE_LAYER kapali.")


def sign_session(session_id: str, issued_s: int | None = None) -> str:
    """A token for this session id, valid for SESSION_TOKEN_TTL_S from
    `issued_s` (default: now). The client can hold it but can never mint one
    for an id it was not given -- which is what stops a bot from posting
    telemetry under another customer's session id -- nor extend the one it
    holds, because the issue second is under the HMAC too. `issued_s` exists
    so the tests can mint an expired token; the server only ever issues now."""
    if issued_s is None:
        issued_s = int(time.time())
    return f"{issued_s}.{_mac(_TOKEN_DOMAIN, session_id, str(issued_s))}"


def _require_session_token(session_id: str, token: str | None) -> None:
    """Raises 401 unless `token` was issued for `session_id` by this server
    within SESSION_TOKEN_TTL_S. Every failure is a 401, which is what the SDK
    answers by registering a new session once (sdk/deepcheck.js)."""
    match = _TOKEN_RE.fullmatch(token or "")
    if match is None:
        raise HTTPException(status_code=401, detail="Gecersiz oturum jetonu")
    issued_raw, presented = match.groups()
    # compare_digest, not ==: a plain comparison short-circuits on the first
    # differing byte and leaks the prefix length through timing. The MAC is
    # checked before the age, so nothing about an unauthenticated issue time
    # is ever acted on.
    if not hmac.compare_digest(_mac(_TOKEN_DOMAIN, session_id, issued_raw), presented):
        raise HTTPException(status_code=401, detail="Gecersiz oturum jetonu")
    age_s = int(time.time()) - int(issued_raw)
    if not -SESSION_TOKEN_CLOCK_LEEWAY_S <= age_s <= SESSION_TOKEN_TTL_S:
        raise HTTPException(status_code=401, detail="Oturum jetonunun suresi doldu")


async def require_dashboard_key(
    x_dashboard_key: Annotated[str | None, Header()] = None,
) -> None:
    """Guards the SOC endpoints. Without this, `GET /api/sessions` let anyone
    on the network read every customer's live risk score and session id."""
    if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key, DASHBOARD_KEY):
        raise HTTPException(status_code=401, detail="Yetkisiz erisim")


# Compared against when the claimed merchant id is unknown, so an unknown id
# costs the same comparison as a known one with a wrong key.
_UNKNOWN_MERCHANT_KEY = b"\x00" * 32


def require_merchant(x_merchant_id: str | None, x_merchant_key: str | None) -> str:
    """Returns the authenticated merchant id, or raises 401.

    A plain function rather than a FastAPI dependency: each endpoint decides
    where in its own order of checks authentication belongs (a disabled layer
    answers 503 first; a decision checks the token first).

    compare_digest on BYTES: on str it raises TypeError for any non-ASCII
    character, which would turn a garbage header into a 500 instead of a 401.
    """
    expected = MERCHANT_KEYS.get(x_merchant_id or "")
    presented = (x_merchant_key or "").encode("utf-8")
    if expected is None:
        hmac.compare_digest(presented, _UNKNOWN_MERCHANT_KEY)
        raise HTTPException(status_code=401, detail="Yetkisiz satici")
    if not x_merchant_key or not hmac.compare_digest(presented, expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Yetkisiz satici")
    return x_merchant_id


def require_review_operator(x_review_operator: str | None, x_review_key: str | None) -> str:
    """Returns the authenticated review operator's id, or raises 401. The same
    constant-time shape as require_merchant. The dashboard key is not a review
    credential and is never consulted here."""
    expected = PROFILE_REVIEW_KEYS.get(x_review_operator or "")
    presented = (x_review_key or "").encode("utf-8")
    if expected is None:
        hmac.compare_digest(presented, _UNKNOWN_MERCHANT_KEY)
        raise HTTPException(status_code=401, detail="Yetkisiz inceleme erisimi")
    if not x_review_key or not hmac.compare_digest(presented, expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Yetkisiz inceleme erisimi")
    return x_review_operator


# A customer reference is whatever the merchant uses to name a customer: an
# account number, a phone number, an e-mail address. Validated by hand rather
# than with a Pydantic constraint, because a constraint failure is a 422 whose
# body echoes the offending value -- the raw personal identifier this feature
# exists to avoid storing -- and the detail below never contains it.
_CUSTOMER_REF_RE = re.compile(r"[\x20-\x7e]{1,200}")


def _require_customer_ref(customer_ref: str) -> str:
    if not _CUSTOMER_REF_RE.fullmatch(customer_ref):
        raise HTTPException(status_code=400, detail="Gecersiz musteri referansi")
    return customer_ref


# The single place the 40/60/80 ladder turns into an enforcement decision.
# It used to live in Demo.jsx, i.e. inside the attacker's own browser.
ACTION_LADDER = (
    (40, "allow"),
    (60, "warn"),
    (80, "verify"),
    (101, "block"),
)

# Turkish labels for the four actions, so the client does not have to map them.
ACTION_MESSAGES = {
    "allow": "Islem onaylandi",
    "warn": "Davranisiniz normal disi gorunuyor, lutfen dikkatli devam edin",
    "verify": "Ek dogrulama gerekli",
    "block": "Islem Reddedildi - Supheli Davranis Tespit Edildi",
}

# Why a decision came out the way it did. `reason` lets the host page tell
# "not enough behaviour yet, try again in a moment" apart from "the score
# itself is high" without the ladder leaving the server.
REASON_MESSAGES = {
    "score": None,  # ACTION_MESSAGES[action] already says it
    "unknown_session": "Oturum bulunamadi - davranis analizi yapilamadi",
    "insufficient_evidence": "Karar icin yeterli davranis verisi yok, lutfen birkac saniye sonra tekrar deneyin",
    "stale": "Oturumun davranis verisi guncel degil, ek dogrulama gerekli",
    "cluster": "Bu davranis kalibi kisa surede cok sayida oturumda tekrarlandi",
    "ambiguous": "Davranis yeterince uzun sure izlendi ancak kesin bir sonuca varilamadi, ek dogrulama gerekli",
    # Internal: the accumulated evidence crossed the bot bound while the
    # smoothed score sits below 60. See _decide_on_evidence.
    "sequential": "Oturum boyunca biriken davranis kaniti otomasyona isaret ediyor, guncel skor dusuk olsa da ek dogrulama gerekli",
    "conformal": "Skor yuksek olsa da gercek kullanici dagilimina uyuyor, ek dogrulama uygulaniyor",
    "verified": "Ek dogrulama basariyla tamamlandi, islem onaylandi",
    # Internal: the decision would have rested on windows in which none of the
    # six structural features was measurable -- fewer than three observed
    # flushes in the window, or a block whose smoothed score is carried by
    # unobserved flushes. See _structural_bits. Told to the client as
    # "insufficient_evidence" (PUBLIC_REASONS), which is also what it is.
    "unobserved": "Karar icin gozlenmis davranis kaniti yetersiz (yapisal ozellikleri olculemeyen pencereler), ek davranis bekleniyor",
    # Internal, for the audit table and the SOC panel only: see PUBLIC_REASONS.
    "profile_deviation": "Bu oturumun davranisi musterinin kendi gecmisinden belirgin sekilde ayriliyor",
    # Internal, likewise: too many decisions named this customer within the
    # hour, so the profile was not read and step-up is asked for instead
    # (RATE_LIMITS["profile"]).
    "profile_rate_limited": "Bu musteri icin kisa surede cok sayida odeme karari istendi, ek dogrulama gerekli",
    # What the scored client is told instead of any of the reasons in
    # PUBLIC_REASONS below.
    "step_up": "Islemi tamamlamak icin ek dogrulama gerekiyor",
}

# What the SCORED CLIENT is told. Six internal reasons collapse to one, because
# each of them names the evidence that convicted the caller, and that is a
# tuning signal: submit, read which check fired, adjust, repeat. "sequential"
# in particular would tell a script that its early flushes still count against
# it and exactly when they have aged out of the window. A dedicated
# "profile_deviation" reason would additionally confirm that this customer
# reference exists and is mature, and would let an attacker binary-search the
# victim's behavioural envelope at 20 decisions/minute. Same argument as
# SHAP_IN_ANALYZE. The reasons that describe the STATE OF THE TELEMETRY rather
# than the verdict (unknown_session, insufficient_evidence, stale, verified,
# score) are left alone: the host page needs them to tell a customer "keep
# going, we need a few more seconds" instead of showing an OTP box, and they say
# nothing about which check convicted anyone.
#
# What this does NOT hide, stated because it is the residual oracle: the action
# itself. An attacker can still tell allow from verify. What bounds that is that
# a profile MATCH buys nothing -- the layer never approves and never lowers a
# score -- so the most it teaches is how to avoid one extra challenge.
PUBLIC_REASONS = {
    "cluster": "step_up",
    "conformal": "step_up",
    "ambiguous": "step_up",
    "sequential": "step_up",
    "profile_deviation": "step_up",
    # Saying "too many decisions for this customer" would confirm that the
    # probing is being counted per customer, and when its window resets.
    "profile_rate_limited": "step_up",
    # Not collapsed to step_up: the page must be able to say "a few more
    # seconds" to a customer whose first flushes were thin, instead of opening
    # an OTP box in the opening seconds of every checkout. What it reveals --
    # "fewer than three of your flushes were observed" -- is a one-bit signal
    # an adversarial review judged low-value: acting on it means producing
    # observed flushes, which is exactly what puts a session in front of the
    # model. Accepted, and stated here rather than left implicit.
    "unobserved": "insufficient_evidence",
}


def _public_verdict(verdict: "DecisionResponse") -> "DecisionResponse":
    """The verdict as the scored client may see it. Applied by the two
    endpoints that answer the client (/api/decision and /api/demo/charge) and
    nowhere else: internal callers, the audit row and the SOC panel keep the
    real reason. Only reason and message change -- never action, risk_score or
    label."""
    public = PUBLIC_REASONS.get(verdict.reason)
    if public is None:
        return verdict
    return verdict.model_copy(update={"reason": public, "message": REASON_MESSAGES[public]})


def get_action(risk_score: float | None) -> str:
    """Fail closed. A missing or non-finite score is not evidence of
    innocence -- it is the absence of evidence, which is exactly what a client
    that never ran the SDK produces. Those sessions go to step-up
    verification, never straight through."""
    if risk_score is None or not math.isfinite(risk_score):
        return "verify"
    for threshold, action in ACTION_LADDER:
        if risk_score < threshold:
            return action
    return ACTION_LADDER[-1][1]


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes for timestamptz columns; a stub or
    an old row may hand back a naive one. Treat naive as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


_STAMPED_CHANNELS = ("mouse_trajectory", "click_timing", "scroll_events", "key_events")


def _all_timestamps(raw: dict) -> list[float]:
    stamps = [e["t"] for key in _STAMPED_CHANNELS for e in raw[key]]
    stamps.extend(raw["focus_changes"])
    return stamps


def _newest_event_ms(raw: dict) -> int | None:
    """The most recent timestamp anywhere in a flush, or None if the flush
    carries no timestamped events at all (a hesitation-only window after the
    user has been idle long enough for everything else to roll out)."""
    stamps = _all_timestamps(raw)
    return int(max(stamps)) if stamps else None


def _payload_fingerprint(raw: dict) -> str:
    """Clock-independent SHA-256 of a flush's telemetry.

    Every timestamp is rebased to the flush's earliest event before hashing,
    so the same recording replayed with its clock shifted to "now" produces
    the same fingerprint. Coordinates are rounded to 0.1 px so float
    formatting differences between clients do not defeat the match.
    """
    stamps = _all_timestamps(raw)
    base = min(stamps) if stamps else 0
    # float() before round(): an integer 300 and a float 300.0 must hash the
    # same, and JSON renders them differently otherwise.
    canonical = {
        "m": [[round(float(p["x"]), 1), round(float(p["y"]), 1), int(p["t"] - base)] for p in raw["mouse_trajectory"]],
        "c": [[round(float(c["x"]), 1), round(float(c["y"]), 1), int(c["t"] - base)] for c in raw["click_timing"]],
        "s": [[round(float(e["scrollY"]), 1), int(e["t"] - base)] for e in raw["scroll_events"]],
        "k": [int(k["t"] - base) for k in raw["key_events"]],
        "f": [int(round(f - base)) for f in raw["focus_changes"]],
        "h": [int(round(h)) for h in raw["hesitation_intervals"]],
    }
    encoded = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utcnow() -> datetime:
    """Timezone-aware UTC.

    The columns are DateTime(timezone=True) and their server_default is
    Postgres' now(), so writing a naive datetime.utcnow() mixed an
    offset-less application clock into a tz-aware column. datetime.utcnow()
    is also deprecated from Python 3.12 on.
    """
    return datetime.now(timezone.utc)


# One dummy scoring pass at boot. get_bundle() alone only unpickles the
# models; the first *real* call still pays sklearn's and torch's lazy
# per-operation warm-up (measured ~3x the steady-state latency). Doing it here
# means the first genuine flush of a demo is not the slow one.
_WARMUP_PAYLOAD = {
    "mouse_trajectory": [
        {"x": 100.0 + i, "y": 100.0 + i, "t": 1_700_000_000_000 + i * 90} for i in range(6)
    ],
    "click_timing": [{"x": 120.0, "y": 140.0, "t": 1_700_000_000_600}],
    "scroll_events": [{"scrollY": 50.0 * i, "t": 1_700_000_000_000 + i * 120} for i in range(4)],
    "hesitation_intervals": [420.0, 610.0],
    "focus_changes": [],
    "key_events": [{"t": 1_700_000_000_000 + i * 160} for i in range(5)],
}


async def _sweep_once() -> tuple[int, int]:
    """One retention pass. Returns (raw blanked, rows deleted)."""
    now = utcnow()
    raw_cutoff = now - timedelta(hours=RAW_TELEMETRY_RETENTION_HOURS)
    row_cutoff = now - timedelta(hours=ROW_RETENTION_HOURS)

    async with get_sessionmaker()() as db:
        # Non-blocking advisory lock: with 4 workers, only the one that gets
        # it sweeps and the rest return immediately instead of queueing up
        # behind the same DELETE.
        #
        # TRANSACTION-scoped, as database.py's schema lock already is. The
        # session-scoped form leaked: an AsyncSession hands its connection
        # back to the pool on commit, so the unlock that used to sit in a
        # finally ran on whatever connection came back next. Measured on
        # Postgres 16 -- lock taken on backend 2480, unlock ran on 2481 and
        # returned false, and the lock stayed on 2480 for that connection's
        # life (pool_recycle is -1, so: the worker's life). Every later pass
        # that drew any other connection then returned (0, 0) and deleted
        # nothing, silently, because _retention_pass only logs non-zero
        # counts. Reproduced end to end: with the leak, _sweep_once() -> (0, 0)
        # and the 48h-old row survived; with this form, (2, 2) and it was gone.
        # Postgres releases an xact lock on the COMMIT or ROLLBACK that ends
        # this transaction, on the connection that holds it, whatever the pool
        # does afterwards -- so there is no unlock statement to misroute.
        got_lock = await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _RETENTION_LOCK_KEY}
        )
        if not got_lock:
            return (0, 0)
        try:
            # Blank the raw telemetry first. The six features and the score
            # stay, so the dashboard chart and any analysis keep working on
            # rows whose recording has aged out.
            blanked = await db.execute(
                update(BehaviorData)
                .where(BehaviorData.created_at < raw_cutoff)
                .where(BehaviorData.raw_purged.is_(False))
                .values(
                    raw_purged=True,
                    mouse_trajectory=[],
                    click_timing=[],
                    scroll_rhythm=[],
                    hesitation_intervals=[],
                    focus_changes=[],
                    key_events=[],
                )
            )
            deleted = await db.execute(
                delete(BehaviorData).where(BehaviorData.created_at < row_cutoff)
            )
            # Sessions whose every flush has now been deleted carry no
            # evidence and cannot produce a decision, so they are noise on the
            # dashboard.
            await db.execute(
                delete(Session)
                .where(Session.last_seen_at < row_cutoff)
                .where(~select(BehaviorData.id).where(BehaviorData.session_id == Session.id).exists())
            )
            await db.commit()
            return (blanked.rowcount or 0, deleted.rowcount or 0)
        except BaseException:
            # Explicit, although closing the session would roll back anyway:
            # this rollback is what releases the lock on the failure path, and
            # a reader should be able to see the release without knowing what
            # AsyncSession.close() does.
            await db.rollback()
            raise


def _unlink_sessions_from_profiles(condition):
    """UPDATE sessions SET profile_id = NULL WHERE <condition> -- and nothing
    else.

    Session.last_seen_at carries onupdate=func.now(), and SQLAlchemy applies
    that to a Core UPDATE as well: written plainly, this statement compiles to
    `SET last_seen_at=now(), profile_id=NULL` (checked by compiling it).
    last_seen_at is what /api/decision's freshness rule reads
    (DECISION_MAX_AGE_S), so unlinking a profile would make every stale
    session of that customer look current again for 30 seconds, and restart
    each one's 24h retention clock. Assigning the column to itself suppresses
    the onupdate.
    """
    return (
        update(Session)
        .where(condition)
        .values(profile_id=None, last_seen_at=Session.last_seen_at)
    )


def _unlink_access_audit_from_profiles(condition):
    """UPDATE profile_access_audit SET profile_id = NULL WHERE <condition>.

    Every deletion of a profile -- erasure, objection, the idle sweep -- runs
    this beside the sessions and decision_audit unlinks. The review endpoint
    writes the profile id into this table, which is kept for
    PROFILE_ACCESS_AUDIT_RETENTION_DAYS (365): left behind, the pseudonym
    re-linked the deleted customer to every decision_audit row of each
    reviewed session through the session id, for a year. The row itself --
    operator, endpoint, session, time -- is the accountability record and
    stays.
    """
    return update(ProfileAccessAudit).where(condition).values(profile_id=None)


async def _sweep_profiles_once() -> tuple[int, int, int, int]:
    """One per-customer-profile retention pass.

    Returns (vectors, profiles, decision audit rows, access audit rows)
    deleted.

    Runs whether or not the profile layer is switched on: turning the layer
    off must not turn off the deletion of what it already stored.
    """
    now = utcnow()
    profile_cutoff = now - timedelta(days=PROFILE_IDLE_RETENTION_DAYS)
    decision_cutoff = now - timedelta(days=DECISION_AUDIT_RETENTION_DAYS)
    access_cutoff = now - timedelta(days=PROFILE_ACCESS_AUDIT_RETENTION_DAYS)
    # The demo namespace lives on the session's clock (see the retention
    # constants above). One statement per step all the same, each with an OR,
    # so the order below is still the order of section 8.
    demo_cutoff = now - timedelta(hours=ROW_RETENTION_HOURS)
    demo_visitor_profiles = (
        select(CustomerProfile.profile_id)
        .where(CustomerProfile.is_demo.is_(True))
        .where(CustomerProfile.is_synthetic.is_(False))
    )

    async with get_sessionmaker()() as db:
        # Transaction-scoped, for the reason spelled out in _sweep_once: the
        # session-scoped form's unlock ran on a pooled connection that was no
        # longer the one holding the lock.
        got_lock = await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _PROFILE_RETENTION_LOCK_KEY}
        )
        if not got_lock:
            return (0, 0, 0, 0)
        try:
            # 1. Vectors past the retention age, whatever their profile -- and
            # a demo visitor's learned vectors past the session's.
            vectors = await db.execute(
                delete(CustomerProfileVector).where(
                    or_(
                        CustomerProfileVector.created_at < profile_cutoff,
                        and_(
                            CustomerProfileVector.created_at < demo_cutoff,
                            CustomerProfileVector.profile_id.in_(demo_visitor_profiles),
                        ),
                    )
                )
            )

            # 2-4. Idle profiles. An objection tombstone is never selected:
            # deleting it would let the next consent call recreate a profile
            # the customer refused.
            #
            # FOR UPDATE SKIP LOCKED makes "select idle, then delete" one step
            # as far as a concurrent decision is concerned: a profile being
            # touched right now is skipped (it is not idle), and a decision
            # arriving after the select waits for this commit instead of
            # refreshing a row that is about to vanish.
            profiles_deleted = 0
            while True:
                idle_ids = (
                    (
                        await db.execute(
                            select(CustomerProfile.profile_id)
                            .where(
                                or_(
                                    CustomerProfile.last_seen_at < profile_cutoff,
                                    # A demo visitor's implicit profile, on the
                                    # session's clock; never a seeded one.
                                    and_(
                                        CustomerProfile.is_demo.is_(True),
                                        CustomerProfile.is_synthetic.is_(False),
                                        CustomerProfile.last_seen_at < demo_cutoff,
                                    ),
                                )
                            )
                            .where(CustomerProfile.consent_basis != "objected")
                            .limit(_PROFILE_SWEEP_BATCH)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not idle_ids:
                    break
                # The links go first and are NULLed, not cascaded: there is
                # no foreign key to cascade through (see models.py), and the
                # audit row itself must survive the profile.
                await db.execute(_unlink_sessions_from_profiles(Session.profile_id.in_(idle_ids)))
                await db.execute(
                    update(DecisionAudit)
                    .where(DecisionAudit.profile_id.in_(idle_ids))
                    .values(profile_id=None, candidate_vec=None)
                )
                await db.execute(_unlink_access_audit_from_profiles(ProfileAccessAudit.profile_id.in_(idle_ids)))
                removed = await db.execute(
                    delete(CustomerProfile).where(CustomerProfile.profile_id.in_(idle_ids))
                )
                profiles_deleted += removed.rowcount or 0
                if len(idle_ids) < _PROFILE_SWEEP_BATCH:
                    break

            # 5-6. The two audit tables, each on its own clock.
            decisions = await db.execute(
                delete(DecisionAudit).where(
                    or_(
                        DecisionAudit.decided_at < decision_cutoff,
                        # Every decision made in the demo namespace, the
                        # synthetic customers' included: a juror paying as
                        # Ayşe is a real person, and the row may hold the
                        # session vector their payment was compared on.
                        and_(DecisionAudit.merchant_id == DEMO_MERCHANT_ID, DecisionAudit.decided_at < demo_cutoff),
                    )
                )
            )
            accesses = await db.execute(
                delete(ProfileAccessAudit).where(ProfileAccessAudit.accessed_at < access_cutoff)
            )
            await db.commit()
            return (
                vectors.rowcount or 0,
                profiles_deleted,
                decisions.rowcount or 0,
                accesses.rowcount or 0,
            )
        except BaseException:
            # Explicit, for the same reason as in _sweep_once: this is the
            # statement that releases the xact lock on the failure path, and a
            # failed statement also leaves the transaction aborted, so the
            # caller must not be handed a session in that state.
            await db.rollback()
            raise


async def _retention_pass() -> None:
    """Both sweeps, each isolated from the other's failure."""
    try:
        blanked, deleted = await _sweep_once()
        if blanked or deleted:
            logger.info(
                "Saklama temizligi: %d satirin ham telemetrisi silindi, %d satir tamamen silindi",
                blanked,
                deleted,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        # A failed sweep must never take the API down with it; the next
        # one will retry.
        logger.exception("Saklama temizligi basarisiz oldu")

    # Deliberately a second transaction rather than more work inside
    # _sweep_once. A failure in the profile pass must not roll back the
    # raw-telemetry blanking: the retention loop swallows exceptions into one
    # Turkish log line and sleeps 600s, so a shared transaction would mean a
    # single profile-delete error silently stopping the blanking of every
    # recorded mouse path in the database. The reverse holds too: a telemetry
    # failure does not stop profiles and audit rows from ageing out.
    try:
        vectors, profiles_deleted, decisions, accesses = await _sweep_profiles_once()
        if vectors or profiles_deleted or decisions or accesses:
            logger.info(
                "Profil temizligi: %d vektor, %d profil, %d karar kaydi, %d erisim kaydi silindi",
                vectors,
                profiles_deleted,
                decisions,
                accesses,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The exception CLASS only, never logger.exception: a database error
        # renders the failed statement's bound parameters into its message,
        # and here those parameters are profile ids, which never reach an
        # application log (not even truncated).
        logger.error("Profil temizligi basarisiz oldu (%s)", type(exc).__name__)


async def _retention_loop() -> None:
    while True:
        await asyncio.sleep(RETENTION_SWEEP_S)
        await _retention_pass()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        scorer.get_bundle()
        await run_in_threadpool(scorer.compute_risk, _WARMUP_PAYLOAD)
        logger.info("Model yuklendi ve isitildi.")
    except FileNotFoundError as exc:
        logger.warning("UYARI: %s", exc)
    except Exception:
        # A failed warm-up must not stop the app from serving; the real
        # request path has its own error handling.
        logger.exception("Model isitma denemesi basarisiz oldu")

    sweeper = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass


app = FastAPI(title="DeepCheck API", lifespan=lifespan)

# Comma-separated origin allowlist, e.g.
# CORS_ORIGINS="https://demo.example.com,https://soc.example.com".
# Defaults to "*" so the local docker-compose demo keeps working untouched.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # Wildcard origin and credentials are mutually exclusive under the CORS
    # spec: a browser refuses a credentialed response carrying
    # `Access-Control-Allow-Origin: *`. The previous combination happened to
    # be harmless only because nothing sends cookies yet -- it would have
    # broken silently the day auth was added. Credentials are enabled only
    # once a real origin allowlist is configured.
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """422 without the offending value.

    FastAPI's default handler returns each error's `input` -- the value that
    failed -- to the caller, and `ctx` can carry it too. For a customer_ref that
    is a phone number or an e-mail address, sent back in the response body and
    into whatever logs response bodies. The handlers validate customer_ref by
    hand so its own failures never reach here; this closes the path for every
    OTHER field of a body that happens to contain one (a mistyped risk_context
    next to a valid reference, a reference sent as a number).

    Applies to every endpoint: no client of this API reads `input` back, and
    the telemetry bodies are behavioural recordings that have no business
    being echoed either.
    """
    detail = [{k: v for k, v in err.items() if k not in ("input", "ctx")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})


# Behavioral telemetry is attacker-controlled input and must be typed and
# bounded at the boundary, not deep inside NumPy math.
#
# `list[dict]` accepted literally anything, which produced two distinct
# failures. Hard crashes: {"t": "abc"} reached a subtraction and raised
# TypeError -> unhandled 500. And silent corruption: JSON's NaN/Infinity
# literals (which Python's json module accepts even though RFC 8259 forbids
# them) flowed into np.mean -> np.clip -> a NaN risk_score, which /api/analyze
# COMMITTED to Postgres before failing to serialize the response -- leaving a
# row that broke every later GET /api/sessions for every user, permanently.
# Physically impossible values (negative durations, 1e300 hesitations,
# infinite coordinates) were likewise accepted and used to steer the score.
#
# allow_inf_nan=False is what rejects the NaN/Infinity literals; the ge/le
# bounds reject the physically impossible ones; max_length caps the CPU and
# storage cost of a single request (a 200k-point trajectory measured 254ms of
# blocking feature extraction).
COORD_LIMIT = 1e5
MAX_TIMESTAMP_MS = 4_102_444_800_000  # year 2100, in epoch ms
MAX_HESITATION_MS = 3_600_000  # 1 hour

Timestamp = Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_MS)]
Coordinate = Annotated[float, Field(ge=-COORD_LIMIT, le=COORD_LIMIT, allow_inf_nan=False)]
HesitationMs = Annotated[float, Field(ge=0, le=MAX_HESITATION_MS, allow_inf_nan=False)]
FocusTimestamp = Annotated[float, Field(ge=0, le=MAX_TIMESTAMP_MS, allow_inf_nan=False)]


class _TelemetryEvent(BaseModel):
    # "ignore" rather than "forbid": a browser may hold a cached older SDK
    # build that sends an extra field, and rejecting the whole flush over it
    # would blind us to that session entirely. Unknown keys are dropped; the
    # keys we actually read are all strictly typed below.
    model_config = ConfigDict(extra="ignore")


class MousePoint(_TelemetryEvent):
    x: Coordinate
    y: Coordinate
    t: Timestamp


class ClickEvent(_TelemetryEvent):
    x: Coordinate
    y: Coordinate
    t: Timestamp


class ScrollEvent(_TelemetryEvent):
    scrollY: Annotated[float, Field(ge=-1e7, le=1e7, allow_inf_nan=False)]
    t: Timestamp


class KeyEvent(_TelemetryEvent):
    t: Timestamp


class ClientSignals(_TelemetryEvent):
    """Provenance the browser reports about itself.

    RECORDED ONLY. None of these reach the feature vector, the model, or the
    risk score today, and the columns exist so their value can be measured
    against the real-session evaluation set before anyone relies on them.

    Worth stating plainly, because a jury will ask: `isTrusted` is false for
    events synthesised by page JavaScript, but a browser driven by Playwright
    or Puppeteer produces TRUSTED events, so this catches injected clicks and
    not driven browsers. `navigator.webdriver` is the reverse -- it flags the
    driven browser and is trivially patched out. Neither is evidence on its
    own, and both are self-reported by the client being judged.
    """

    untrusted_events: Annotated[int, Field(ge=0, le=100_000)] = 0
    webdriver: bool = False
    pointer_mouse: Annotated[int, Field(ge=0, le=100_000)] = 0
    pointer_pen: Annotated[int, Field(ge=0, le=100_000)] = 0
    pointer_touch: Annotated[int, Field(ge=0, le=100_000)] = 0


class AnalyzeRequest(BaseModel):
    # No longer optional and no longer minted server-side when missing: an id
    # only becomes usable once POST /api/session has signed it, so there is
    # nothing sensible to do with a flush that carries no id.
    session_id: str = Field(min_length=1, max_length=128)
    mouse_trajectory: list[MousePoint] = Field(default_factory=list, max_length=2000)
    click_timing: list[ClickEvent] = Field(default_factory=list, max_length=500)
    scroll_events: list[ScrollEvent] = Field(default_factory=list, max_length=1000)
    hesitation_intervals: list[HesitationMs] = Field(default_factory=list, max_length=500)
    focus_changes: list[FocusTimestamp] = Field(default_factory=list, max_length=200)
    key_events: list[KeyEvent] = Field(default_factory=list, max_length=1000)
    client_signals: ClientSignals = Field(default_factory=ClientSignals)
    # The sender's Date.now() at the moment this flush was sent. Optional so an
    # SDK build from before the field keeps working, under the older absolute
    # clock check. See the replay-protection note at the top of this file.
    client_sent_at: Timestamp | None = None


class AnalyzeResponse(BaseModel):
    session_id: str
    risk_score: float
    label: str
    confidence: float
    # How many of the twelve features this flush actually measured, and whether
    # that was too few for the score to be worth displaying. A host page should
    # show "still measuring" rather than a risk colour while this is true --
    # the opening seconds of a real session carry almost no signal, and telling
    # a customer they look like a bot because they have only just arrived is a
    # false accusation the data does not support.
    measured_features: int
    provisional: bool
    shap_explanation: list[dict]
    response_time_ms: float


class SessionCreateResponse(BaseModel):
    """Challenge only. The token is issued by /api/session/attest."""

    session_id: str
    challenge: str
    difficulty_bits: int


class RuntimeMeasurements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Smallest non-zero gap the client observed between consecutive
    # performance.now() readings, in microseconds. Every engine clamps this.
    clock_resolution_us: Annotated[float, Field(ge=0, le=1e6, allow_inf_nan=False)]
    # Median observed delay of setTimeout(..., 0), in milliseconds. Never zero
    # on a real event loop.
    timer_lag_ms: Annotated[float, Field(ge=0, le=1e4, allow_inf_nan=False)]


class SessionAttestRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    challenge: str = Field(min_length=1, max_length=256)
    nonce: str = Field(min_length=1, max_length=64)
    runtime: RuntimeMeasurements


class SessionAttestResponse(BaseModel):
    session_id: str
    token: str
    attested: bool


class RiskContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # RECORDED ONLY in v1. Stored on the decision audit row so a "deviation AND
    # meaningful context" gate can be MEASURED on real traffic before anything
    # is enforced on it: gating on a band nobody has measured is inventing a
    # threshold.
    amount_band: Literal["low", "medium", "high"] | None = None
    new_beneficiary: bool | None = None


class DecisionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    # Plain str, no constraints, validated by hand: see _require_customer_ref.
    # Only accepted together with X-Merchant-Id / X-Merchant-Key.
    customer_ref: str | None = None
    risk_context: RiskContext | None = None


class DecisionResponse(BaseModel):
    action: str
    risk_score: float | None
    label: str
    message: str
    reason: str


class VerifyRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=16)


class VerifyResponse(BaseModel):
    verified: bool
    message: str


class ChargeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    # Demo shortcut only: in a real integration the MERCHANT's server names the
    # customer, never the browser. Plain str, validated by hand, for the same
    # reason as DecisionRequest.customer_ref. Used under the reserved "demo"
    # merchant namespace (DEMO_MERCHANT_ID), which no real merchant can claim.
    customer_ref: str | None = None


class ChargeResponse(BaseModel):
    status: str  # "charged" | "declined"
    charge_id: str | None
    amount: float
    decision: DecisionResponse


# The three merchant-authenticated profile bodies. Every field that could fail
# validation next to a customer_ref is a plain str checked by hand, so a typo
# in `basis` or `mode` is a 400 with a fixed Turkish detail rather than a 422
# naming the body it came from.
class ProfileConsentRequest(BaseModel):
    customer_ref: str
    basis: str


class ProfileEraseRequest(BaseModel):
    customer_ref: str
    mode: str


class OutcomeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    outcome: str


# The lawful bases a merchant may record. Legitimate interest is deliberately
# absent: a persistent identity-keyed record of how a person moves a pointer
# is biometric data (GDPR Art. 4(14), KVKK Art. 6), for which it is not
# available. "demo" is set only by the demo page and "objected" only by an
# objection, so neither can be claimed here.
#
# The same argument is open against "contract_necessity" and is NOT settled
# here: performance of a contract is a basis for ordinary personal data
# (GDPR Art. 6(1)(b)), and neither GDPR Art. 9(2) nor KVKK Art. 6 lists it
# among the exceptions for the special-category kind. It is left accepted
# rather than removed because that is a legal call, not an engineering one --
# but it is disclosed, not hidden: docs/kvkk-aydinlatma.md tells a customer
# the value is accepted and must not be used, and docs/dpia.md makes deciding
# it a precondition of any pilot. Use "explicit_consent".
PROFILE_CONSENT_BASES = ("explicit_consent", "contract_necessity")
PROFILE_ERASE_MODES = ("erase", "object")
PROFILE_OUTCOMES = ("settled", "disputed")


@app.post("/api/session", response_model=SessionCreateResponse, status_code=201)
async def create_session(request: Request):
    """Mints a session id and its signing token.

    The id is generated here, never accepted from the caller: if a client
    could name its own id, anyone who learned a victim's id could request a
    token for it and then post telemetry -- or ask for a decision -- under it.

    Deliberately writes NO database row. The row is created by the first
    /api/analyze flush instead, so a page that is opened and never used
    leaves nothing behind: React's StrictMode alone double-mounts the demo in
    development, and a row per mint would fill the SOC dashboard with empty
    ghost sessions. A minted-but-unused id is also exactly the case
    /api/decision must answer with "verify", which it does by finding no row.
    """
    _rate_limit("session", _client_ip(request))
    session_id = str(uuid.uuid4())
    return SessionCreateResponse(
        session_id=session_id,
        challenge=_issue_challenge(session_id),
        difficulty_bits=POW_DIFFICULTY_BITS,
    )


@app.post("/api/session/attest", response_model=SessionAttestResponse, status_code=201)
async def attest_session(payload: SessionAttestRequest, request: Request):
    """Exchanges a solved challenge for the session token.

    The token is what /api/analyze requires, so telemetry cannot be posted at
    all until some client has done a proof of work for this session and
    reported runtime values inside browser-plausible bounds. That is a
    statement about work done and numbers reported -- not that the client is
    a browser or ran the SDK (a plain script passes; see the attestation
    comment at POW_DIFFICULTY_BITS), and not that a human is present.

    A new token is issued on every call while the challenge is still inside
    POW_CHALLENGE_TTL_S, each with its own SESSION_TOKEN_TTL_S; after that the
    session can be issued no more tokens.
    """
    # Deliberately NOT rate limited. Minting the challenge already consumed a
    # slot, and charging a second one for the answer halves the real budget: a
    # page load costs two calls, so a 10-per-minute bucket became five page
    # loads per minute and a customer reloading twice got a 429. A solved
    # challenge is worthless without the challenge, and that is what the limit
    # protects.
    _check_challenge(payload.session_id, payload.challenge)
    _check_proof_of_work(payload.challenge, payload.nonce)
    _check_runtime(payload.runtime)
    return SessionAttestResponse(
        session_id=payload.session_id,
        token=sign_session(payload.session_id),
        attested=True,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    session_id = payload.session_id
    _require_session_token(session_id, x_deepcheck_token)
    _rate_limit("analyze", session_id)
    started = time.perf_counter()
    # Read once, before any await: the offset check compares it with
    # client_sent_at, and time spent waiting on the database is not drift in
    # the client's clock.
    server_now_ms = int(time.time() * 1000)

    # Back to plain dicts: scorer.extract_features() reads these with .get(),
    # and keeping that dict interface means train_model.py can keep feeding it
    # simulated payloads directly (the shared extraction path that keeps
    # training and serving in sync).
    raw = {
        "mouse_trajectory": [p.model_dump() for p in payload.mouse_trajectory],
        "click_timing": [c.model_dump() for c in payload.click_timing],
        "scroll_events": [s.model_dump() for s in payload.scroll_events],
        "hesitation_intervals": payload.hesitation_intervals,
        "focus_changes": payload.focus_changes,
        "key_events": [k.model_dump() for k in payload.key_events],
    }

    # Replay protection, in the order of the note at the top of this file.
    # 0: no timestamped events means no behaviour. Nothing is checked, scored
    # or written.
    newest_event_at = _newest_event_ms(raw)
    if newest_event_at is None:
        return await _answer_without_behaviour(db, session_id, started)

    # 1, the stateless half: needs no database, so it rejects before any read.
    clock_offset_ms = None
    if payload.client_sent_at is not None:
        event_age_ms = payload.client_sent_at - newest_event_at
        if not -MAX_FUTURE_EVENT_MS <= event_age_ms <= MAX_CLOCK_SKEW_MS:
            logger.warning("replay/event-age rejected for session %s: age=%dms", session_id, event_age_ms)
            raise HTTPException(
                status_code=422,
                detail="Telemetri olaylari gonderim zamaniyla uyumsuz (tekrar oynatma suphesi)",
            )
        clock_offset_ms = server_now_ms - payload.client_sent_at
    else:
        skew_ms = server_now_ms - newest_event_at
        if abs(skew_ms) > MAX_CLOCK_SKEW_MS:
            logger.warning("replay/skew rejected for session %s: skew=%dms", session_id, skew_ms)
            raise HTTPException(
                status_code=422,
                detail="Telemetri zaman damgasi sunucu saatiyle uyumsuz (tekrar oynatma suphesi)",
            )

    # Read this session's recent flushes BEFORE scoring: the smoothing below
    # needs their scores, and the replay checks need the previous flush's
    # newest timestamp. Newest first.
    recent_result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(HISTORY_FETCH_ROWS)
    )
    recent_rows = list(recent_result.scalars().all())

    # 1b: the clock may be wrong, but not differently wrong than it was.
    if clock_offset_ms is not None:
        existing = await db.get(Session, session_id)
        stored_offset = existing.clock_offset_ms if existing is not None else None
        if stored_offset is not None and abs(clock_offset_ms - stored_offset) > MAX_OFFSET_DRIFT_MS:
            logger.warning(
                "replay/offset-drift rejected for session %s: offset=%dms stored=%dms",
                session_id,
                clock_offset_ms,
                stored_offset,
            )
            raise HTTPException(
                status_code=422,
                detail="Istemci saati oturum icinde tutarsiz sekilde degisti (tekrar oynatma suphesi)",
            )

    # 2
    previous_newest = getattr(recent_rows[0], "newest_event_at", None) if recent_rows else None
    if previous_newest is not None and newest_event_at < previous_newest:
        logger.warning("replay/backwards-time rejected for session %s", session_id)
        raise HTTPException(
            status_code=422,
            detail="Telemetri zamani geriye gidiyor (tekrar oynatma suphesi)",
        )

    # 3
    payload_hash = _payload_fingerprint(raw)
    duplicate = await db.execute(
        select(BehaviorData.id).where(BehaviorData.payload_hash == payload_hash).limit(1)
    )
    if duplicate.scalars().first() is not None:
        logger.warning("replay/duplicate rejected for session %s: hash=%s", session_id, payload_hash[:12])
        raise HTTPException(
            status_code=422,
            detail="Bu davranis penceresi daha once gonderilmis (tekrar oynatma suphesi)",
        )

    try:
        # compute_risk is tens of ms of pure CPU (sklearn + SHAP). Called
        # directly in this async handler it blocks the single event-loop
        # thread, stalling every other in-flight request including
        # /api/health -- measured event-loop stalls up to 1.5s at 20
        # concurrent flushes, capping a worker at ~13 req/s. Running it in the
        # threadpool keeps the loop free to accept and finish other work.
        result = await run_in_threadpool(scorer.compute_risk, raw)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        # Never surface a traceback to an unauthenticated caller: it is both an
        # error-log flood and a fingerprinting oracle. Log server-side instead.
        logger.exception("compute_risk failed for session %s", session_id)
        raise HTTPException(
            status_code=500, detail="Davranış analizi tamamlanamadı"
        ) from None

    # Atomic get-or-create. The previous read-then-add pattern raised an
    # unhandled IntegrityError (-> HTTP 500) whenever two flushes for the same
    # brand-new session arrived concurrently: both SELECTs returned None, both
    # INSERTs ran, the second violated the primary key. That is not
    # hypothetical -- the SDK fires every 2s and the first request pays model
    # warm-up, so overlap at session start is the common case, not the rare one.
    await db.execute(
        pg_insert(Session).values(id=session_id).on_conflict_do_nothing(index_elements=["id"])
    )
    session = await db.get(Session, session_id)
    if clock_offset_ms is not None and session.clock_offset_ms is None:
        session.clock_offset_ms = clock_offset_ms

    # Oldest first. Non-finite rows (which can only pre-date the boundary
    # validation, but may exist in a running database) are skipped inside.
    smoothed_score = scorer.smooth_session_score(
        [row.risk_score for row in reversed(recent_rows)],
        result["risk_score"],
        result["observed_structure"],
    )
    smoothed_label = scorer.get_label(smoothed_score)

    session.risk_score = smoothed_score
    session.label = smoothed_label
    session.confidence = result["confidence"]
    session.shap_explanation = result["shap_explanation"]
    session.response_time_ms = result["response_time_ms"]
    session.last_seen_at = utcnow()

    features = result["features"]
    behavior_row = BehaviorData(
        session_id=session_id,
        mouse_trajectory=raw["mouse_trajectory"],
        click_timing=raw["click_timing"],
        scroll_rhythm=raw["scroll_events"],
        hesitation_intervals=raw["hesitation_intervals"],
        focus_changes=raw["focus_changes"],
        key_events=raw["key_events"],
        # Built from FEATURE_NAMES rather than listed by hand, so adding a
        # feature does not silently stop persisting it.
        **{name: features[name] for name in FEATURE_NAMES},
        # Which of those values were measured rather than defaulted. Without
        # it a stored 0.3 is ambiguous forever: see models.py.
        measured_mask=result["measured_mask"],
        risk_score=result["risk_score"],
        payload_hash=payload_hash,
        behavior_bucket=result["behavior_bucket"],
        newest_event_at=newest_event_at,
        client_signals=payload.client_signals.model_dump(),
    )
    db.add(behavior_row)

    try:
        await db.commit()
    except IntegrityError:
        # The duplicate pre-check above is a read before a write, so identical
        # flushes posted concurrently all pass it. The unique index on
        # payload_hash is what actually settles that race; losing it means this
        # window was already recorded, which is the same answer the pre-check
        # gives.
        await db.rollback()
        logger.warning("replay/duplicate race lost for session %s", session_id)
        raise HTTPException(
            status_code=422,
            detail="Bu davranis penceresi daha once gonderilmis (tekrar oynatma suphesi)",
        ) from None

    return AnalyzeResponse(
        session_id=session_id,
        risk_score=smoothed_score,
        label=smoothed_label,
        confidence=result["confidence"],
        measured_features=result["measured_features"],
        provisional=result["provisional"],
        # Empty unless SHAP_IN_ANALYZE is set: see the note there. The row
        # keeps the full explanation, and the dashboard reads it from
        # /api/score/{id} behind the dashboard key.
        shap_explanation=result["shap_explanation"] if SHAP_IN_ANALYZE else [],
        response_time_ms=result["response_time_ms"],
    )


async def _answer_without_behaviour(db: AsyncSession, session_id: str, started: float) -> AnalyzeResponse:
    """The reply to a flush with no timestamped events: a read, never a write.

    No BehaviorData row, no fingerprint, no session upsert and no last_seen_at
    refresh (see step 0 of the replay-protection note). The score is whatever
    the session already stored -- 0.0 if it has no row -- and it is marked
    provisional with nothing measured, because this flush measured nothing.
    """
    session = await db.get(Session, session_id)
    stored = session.risk_score if session is not None else None
    # A non-finite stored score can only pre-date boundary validation, but it
    # would fail JSON serialisation here; this reply is display-only anyway.
    score = stored if stored is not None and math.isfinite(stored) else 0.0
    return AnalyzeResponse(
        session_id=session_id,
        risk_score=score,
        label=scorer.get_label(score),
        confidence=0.0,
        measured_features=0,
        provisional=True,
        shap_explanation=[],
        response_time_ms=round((time.perf_counter() - started) * 1000, 1),
    )


def _verify_response(reason: str, risk_score: float | None = None, label: str = "Degerlendirilemedi") -> DecisionResponse:
    return DecisionResponse(
        action="verify",
        risk_score=risk_score,
        label=label,
        message=REASON_MESSAGES[reason] or ACTION_MESSAGES["verify"],
        reason=reason,
    )


def _sprt_statistic(scores: list[float], factor: float | None = None) -> float:
    """Weighted sum of per-flush log-odds, log(p / (1 - p)) of each score.

    Not a sum of log-likelihood ratios: the scores are uncalibrated forest vote
    shares from overlapping windows (see the block above SPRT_NOMINAL_ALPHA).
    `factor` defaults to SPRT_EVIDENCE_FACTOR, read at call time so there is
    one knob; the argument exists so a measurement can compare factors on the
    same flushes through this exact function.
    """
    weight = SPRT_EVIDENCE_FACTOR if factor is None else factor
    total = 0.0
    for score in scores:
        p = min(max(score / 100.0, SPRT_P_CLAMP), 1.0 - SPRT_P_CLAMP)
        total += weight * math.log(p / (1.0 - p))
    return total


async def _cluster_size(db: AsyncSession, session_id: str, bucket: str | None) -> int:
    """Distinct OTHER sessions sharing this behaviour bucket recently."""
    if not bucket:
        return 0
    cutoff = utcnow() - timedelta(seconds=CLUSTER_WINDOW_S)
    result = await db.scalar(
        select(func.count(func.distinct(BehaviorData.session_id)))
        .where(BehaviorData.behavior_bucket == bucket)
        .where(BehaviorData.created_at >= cutoff)
        .where(BehaviorData.session_id != session_id)
    )
    return int(result or 0)


async def _decide(
    db: AsyncSession,
    session_id: str,
    *,
    merchant_id: str | None = None,
    customer_ref: str | None = None,
    risk_context: RiskContext | None = None,
) -> DecisionResponse:
    """The enforcement logic, shared by /api/decision and /api/demo/charge.

    Every failure mode resolves to step-up verification rather than to
    "allow": an unknown session, too little observed behaviour, behaviour
    that is not current, or a broken score. The 40/60/80 ladder is applied
    here and nowhere else. A fresh step-up recorded on the server is what
    turns any of those "verify" outcomes into "allow" -- see _apply_step_up.

    merchant_id and customer_ref are non-None only when the profile layer is
    enabled and the caller is entitled to name a customer: a merchant whose
    credential checked out (_profile_request), or the demo page under the
    reserved DEMO_MERCHANT_ID. The per-customer profile can then turn an
    allow or a warn into a verify, and nothing else (see the profile block in
    _decide_on_evidence). The verdict returned here carries the INTERNAL
    reason; the endpoints collapse it with _public_verdict.
    """
    session = await db.get(Session, session_id)
    if session is None:
        # No row means no flush was ever recorded, so there is nothing a
        # step-up could be attached to (/api/demo/verify answers 404 here).
        # This is the one verify outcome a verification never upgrades.
        return _verify_response("unknown_session")

    profile_ctx = await _load_profile_context(db, session_id, merchant_id, customer_ref)
    verdict = await _decide_on_evidence(db, session, session_id, profile_ctx=profile_ctx)
    # In-process only, never on DecisionResponse: telling the scored client
    # "you were challenged because you deviated from your stored profile" is
    # the tuning oracle PUBLIC_REASONS exists to close. The data subject's
    # transparency right is met by the aydinlatma metni and the human-review
    # channel (GET /api/profile/review), not by the API response -- which is
    # why this is not concealment.
    pre_step_up_reason = verdict.reason
    unlifted = verdict
    verdict = _apply_step_up(session, verdict)
    if verdict is not unlifted and not await _consume_step_up(db, session, session_id):
        # Another decision spent this verification first (two concurrent
        # checkouts on one step-up). The verdict stays what the evidence said.
        verdict = unlifted
    await _learn_and_audit(db, session, session_id, verdict, pre_step_up_reason, profile_ctx, merchant_id, risk_context)
    return verdict


async def _consume_step_up(db: AsyncSession, session: Session, session_id: str) -> bool:
    """Spend a verification: one step-up authorises ONE approval.

    It used to authorise every checkout on the session for VERIFICATION_VALID_S
    -- verified_at was written once and never cleared, so after a single
    step-up every decision that came back "verify" was upgraded to "allow" for
    five minutes. An adversarial review measured five consecutive charges on an
    all-unobserved session after one verification, all approved, and bounded
    it only by the decision rate limit (~400 per step-up). A step-up is proof
    that someone completed ONE challenge, the way a 3-D Secure authentication
    covers one transaction.

    Compare-and-set on the timestamp that was read, so two concurrent
    decisions cannot both spend the same verification: exactly one UPDATE
    matches, and the other gets rowcount 0 and keeps its unlifted verdict.
    """
    seen = session.verified_at
    # last_seen_at is assigned to itself to suppress its onupdate=now(): it is
    # the freshness clock DECISION_MAX_AGE_S reads, and spending a verification
    # is not behaviour (see _unlink_sessions_from_profiles).
    result = await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.verified_at == seen)
        .values(verified_at=None, last_seen_at=Session.last_seen_at)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    spent = (result.rowcount or 0) == 1
    if spent:
        session.verified_at = None
    return spent


def _step_up_is_fresh(session: Session) -> bool:
    verified_at = _as_utc(getattr(session, "verified_at", None))
    return verified_at is not None and utcnow() - verified_at <= timedelta(seconds=VERIFICATION_VALID_S)


def _apply_step_up(session: Session, verdict: DecisionResponse) -> DecisionResponse:
    """A completed step-up upgrades "verify" to "allow" while it is fresh.

    Applied to the finished verdict, never inside the checks that produce it.
    It used to sit at the end of the ladder, after insufficient_evidence,
    stale, ambiguous, cluster and conformal had already returned, so for all
    of those /api/demo/verify succeeded and the next charge was declined with
    the same reason -- the demo modal looped forever. Measured before this
    change with a verification seconds old: 2 flushes at 12, 3 at 72, 10 at
    50 or 55, a stale session, a cluster escalation and a conformal softening
    were all declined again; only the 60-80 ladder verify was unlocked. Step-up
    is the answer to every kind of uncertainty those checks express, so it has
    to see all of them.

    It never touches "block": verification is for uncertainty, not for
    overriding a confident bot verdict. And it changes the action only --
    risk_score and label still say what the evidence says, so a session with
    too few flushes does not start reporting the column default of 0.0.

    The per-customer profile layer is one more thing step-up must be able to
    unlock; see the profile block in _decide_on_evidence for why the check sits
    before this function rather than after it.
    """
    if verdict.action != "verify":
        return verdict
    if not _step_up_is_fresh(session):
        return verdict
    return DecisionResponse(
        action="allow",
        risk_score=verdict.risk_score,
        label=verdict.label,
        message=REASON_MESSAGES["verified"],
        reason="verified",
    )


async def _decide_on_evidence(
    db: AsyncSession, session: Session, session_id: str, *, profile_ctx: "ProfileContext | None" = None
) -> DecisionResponse:
    """The verdict the observed behaviour supports, before any step-up.

    Returns early freely: _apply_step_up runs on whatever comes back, so a new
    verify outcome added here is unlockable by verification without anyone
    having to remember that.
    """
    # A row can exist with the default risk_score of 0.0 -- which would read
    # as "Gercek Kullanici" for a client that never sent usable telemetry.
    # And one flush is not enough: require MIN_FLUSHES_FOR_DECISION analyzed
    # windows before any score is trusted.
    rows = (
        await db.execute(
            select(
                BehaviorData.risk_score,
                BehaviorData.behavior_bucket,
                BehaviorData.measured_mask,
            )
            .where(BehaviorData.session_id == session_id)
            .order_by(BehaviorData.created_at.desc())
            .limit(SPRT_MAX_FLUSHES)
        )
    ).all()
    if len(rows) < MIN_FLUSHES_FOR_DECISION:
        return _verify_response("insufficient_evidence")

    now = utcnow()
    last_seen = _as_utc(session.last_seen_at)
    if last_seen is None or now - last_seen > timedelta(seconds=DECISION_MAX_AGE_S):
        return _verify_response("stale", session.risk_score, session.label or "Degerlendirilemedi")

    # Sequential test over the per-flush scores -- but only over the flushes
    # that actually observed a generator (see _structural_bits). A window in
    # which nothing was measurable scores 99.1 on neutral fallbacks alone, and
    # one such flush outweighs the upper bound by itself.
    per_flush = [
        r[0]
        for r in rows
        if r[0] is not None and math.isfinite(r[0]) and _observed_a_generator(r[2])
    ]
    # Fewer than MIN_FLUSHES_FOR_DECISION flushes observed a generator: there is
    # no behavioural evidence to decide on in EITHER direction, so the answer is
    # step-up -- never an approval, and never a block.
    #
    # Not "skip the test and let the ladder decide", which is what this briefly
    # was, and an adversarial review measured why that is wrong (scratchpad
    # gate_verify/attack, SDK-faithful emulation of deepcheck.js's buffer):
    # a script that sends only unobserved flushes -- a pointer move every few
    # seconds, fields set by value, one click -- produces per-flush scores that
    # swing between ~30 and ~100, because the six MARGINAL features are still
    # partly measured and the client supplies hesitation_intervals itself. The
    # ladder's five-flush median then lands in allow or warn. At the flush
    # holding the click that was 88 of 300 random schedules charged, against 0
    # of 300 when those flushes were still summed into the statistic;
    # re-checked independently at 17 of 60 against 0 of 60.
    #
    # Nor a block: the ladder's median blocks a legitimate customer whose
    # window holds two thin opening flushes and one real one -- 75 of 3000
    # synthetic prefix-3 checkouts when this branch deferred to the ladder, 69
    # under the rule before any gate -- and _apply_step_up never lifts a
    # block. A verdict this severe has to rest on something that was observed
    # (see also the guard before the conformal check, for sessions that do
    # have three observed flushes).
    #
    # "insufficient_evidence" is a telemetry-state reason, returned as is: the
    # page tells the customer "a few more seconds", and a second hint becomes a
    # step-up (Demo.jsx). A real checkout -- typing a card, or moving the
    # pointer to a stored card and the button -- is observed for about five
    # flushes once it starts (the SDK ships a 10 s rolling window).
    if len(per_flush) < MIN_FLUSHES_FOR_DECISION:
        logger.info(
            "observed-flush gate held session %s at verify: %d of %d flushes observed a generator",
            session_id, len(per_flush), len(rows),
        )
        return _verify_response("unobserved", session.risk_score, session.label or "Degerlendirilemedi")

    statistic = _sprt_statistic(per_flush)
    if SPRT_LOWER < statistic < SPRT_UPPER:
        # Inconclusive, and it stays inconclusive: the flush cap changes what
        # the customer is told, never whether the payment goes through.
        #
        # It used to fall through to the ladder here, which meant a session
        # parked in the 40-60 band was charged with a warning banner once it
        # had produced ten flushes. Twenty seconds of deliberately ambiguous
        # behaviour was therefore a way to be approved. Ambiguity at a payment
        # gate is a reason to ask for more proof, not a reason to accept.
        # Counted on ROWS, not on observed flushes: "ambiguous" means the whole
        # window has been watched and is still inconclusive. Counting observed
        # flushes made it unreachable whenever one thin flush was in the window.
        reason = "insufficient_evidence" if len(rows) < SPRT_MAX_FLUSHES else "ambiguous"
        return _verify_response(reason, session.risk_score, session.label or "Degerlendirilemedi")

    risk_score = session.risk_score
    action = get_action(risk_score)
    label = session.label or scorer.get_label(risk_score)

    # The upper bound is a floor under the ladder, not a hint to it. Crossing
    # it means the flushes still in the window, taken together, point at
    # automation, while the ladder reads the smoothed score -- a median of the
    # newest five, which can already have turned. This used to fall through to
    # the ladder, so seven flushes at 95 followed by three at 10 were charged:
    # the median was 10 and the automated evidence still in the window was
    # ignored. Now the session is at least verify. The ladder can still raise
    # it to block, and the conformal guard below only ever lowers a block to
    # verify, so "at least verify" holds on every path. Crossing the LOWER
    # bound grants nothing comparable: it hands the session to the ladder,
    # which can still verify or block it (a human-to-bot handover crosses the
    # lower bound and is still escalated, on the level-shifted score).
    # Escalation only, in both directions.
    #
    # Measured cost, on SYNTHETIC sessions only (the comparison described at
    # SPRT_EVIDENCE_FACTOR, factor 1.0, decision at the last flush, without
    # and with this rule): 5 of 300 slow-typist sessions (1.7%) went from
    # allow or warn to verify; the typical-human and keyboard-only slices, the
    # bot, bot_sophisticated and drift_to_bot personas and the 14 held-out
    # browser-lab runs did not change. What it bought: of 300 simulated
    # bot-then-human handovers (drift_to_human), 24 (8.0%) were allowed at the
    # last flush without it and none at any flush with it. It is a step-up,
    # never a block, and a fresh step-up unlocks it.
    if statistic >= SPRT_UPPER and action in ("allow", "warn"):
        return DecisionResponse(
            action="verify",
            risk_score=risk_score,  # unchanged: the score still says what the newest flushes say
            label=label,
            message=REASON_MESSAGES["sequential"],
            reason="sequential",
        )

    # Cross-session clustering. Escalation only: many sessions behaving
    # identically is evidence of automation, while the absence of a cluster is
    # not evidence of a person.
    if CLUSTER_ESCALATION_ENABLED and action in ("allow", "warn"):
        bucket = rows[0][1] if rows else None
        peers = await _cluster_size(db, session_id, bucket)
        if peers >= CLUSTER_MIN_SESSIONS:
            logger.info(
                "cluster escalation for session %s: %d peers in bucket %s", session_id, peers, bucket
            )
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,
                label=label,
                message=REASON_MESSAGES["cluster"],
                reason="cluster",
            )

    # Conformal guard. De-escalation only: if this score is unremarkable among
    # the bundle's held-out calibration humans, refuse to block on it and ask
    # for verification instead. Costs a challenge rather than a customer --
    # WHEN it fires, and with the served bundles it never does: their 28
    # calibration values come from the lab's scripted Playwright personas and
    # peak at 33.2, so every score >= 80 gets p = 1/29 < CONFORMAL_ALPHA. It
    # protects no real user until it is calibrated on real ones (scorer.py's
    # conformal section; ModelBundle logs the current state at load).
    # A block is irreversible -- _apply_step_up never lifts it -- so it has to
    # rest on observed behaviour. The gate above guarantees three observed
    # flushes in the window, but the ladder reads the SMOOTHED score, the
    # median of the newest five per-flush scores, and that median still counts
    # unobserved flushes. Three thin, high flushes at the end of a session
    # therefore decided the block on their own: measured on synthetic sessions
    # (benchmark.py slice bodies with thin tails, 60 seeds x 5 bodies, prefixes
    # 3..10), four- and five-flush thin tails were blocked 10 and 15 times in
    # 2400 where the rule before any gate blocked 7 and 9. When fewer than
    # three of the flushes that median reads were observed, the block is held
    # at step-up instead. This only ever turns a block into a verify, so it
    # cannot approve anything; a real-bot session keeps its block as long as
    # the automation is what was observed.
    if action == "block":
        newest = rows[: scorer.SMOOTHING_WINDOW]
        observed_newest = sum(1 for r in newest if _observed_a_generator(r[2]))
        if observed_newest < MIN_FLUSHES_FOR_DECISION:
            logger.info(
                "block for session %s held at verify: its smoothed score rests on %d of %d observed flushes",
                session_id, observed_newest, len(newest),
            )
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,
                label=label,
                message=REASON_MESSAGES["unobserved"],
                reason="unobserved",
            )

    if action == "block":
        p_value = scorer.conformal_p_value(risk_score, scorer.get_human_calibration())
        if p_value is not None and p_value > scorer.CONFORMAL_ALPHA:
            logger.info(
                "conformal guard softened a block for session %s (p=%.3f)", session_id, p_value
            )
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,
                label=label,
                message=REASON_MESSAGES["conformal"],
                reason="conformal",
            )

    # --- Per-customer profile: escalation only -------------------------------
    # Placed here, inside _decide_on_evidence and not after _apply_step_up, on
    # purpose. _apply_step_up runs on whatever this function returns, so a
    # customer who has already passed step-up in this session is not asked
    # again. Putting the check after it would rebuild the measured verify loop
    # the _apply_step_up docstring describes: challenge, correct code, challenge.
    # The cost is stated plainly: an attacker who has already phished an OTP is
    # not stopped by this layer. This layer converts fraud prevention into
    # step-up strength; it does not exceed it.
    #
    # Escalation only, and only from allow/warn. It can never block, never lower
    # a score, never approve, and a MATCHING profile changes nothing: a bot
    # replaying the victim's own recorded behaviour matches the victim's profile
    # perfectly, so trust-on-match would hand the attacker the win. Every
    # earlier check has already returned by the time this runs -- the flush
    # floor, staleness, an inconclusive sequential test and a crossed upper
    # bound are verify already, cluster escalates independently, and the
    # conformal guard only ever touches block.
    #
    # A customer whose profile bucket is exhausted is answered "verify" without
    # reading anything when enforcing (_load_profile_context): the same
    # escalation-only step, for probing rather than for deviation. In shadow
    # mode it is recorded and changes nothing, like every shadow opinion.
    if (
        profile_ctx is not None
        and action in ("allow", "warn")
        and profile_ctx.verdict.state == profiles.STATE_RATE_LIMITED
        and PROFILE_ESCALATION
    ):
        return DecisionResponse(
            action="verify",
            risk_score=risk_score,  # UNCHANGED
            label=label,  # UNCHANGED
            message=REASON_MESSAGES["profile_rate_limited"],
            reason="profile_rate_limited",
        )
    if profile_ctx is not None and action in ("allow", "warn") and profile_ctx.verdict.escalate:
        if await _profile_escalates(db, session, session_id, profile_ctx):
            return DecisionResponse(
                action="verify",
                risk_score=risk_score,  # UNCHANGED
                label=label,  # UNCHANGED
                message=REASON_MESSAGES["profile_deviation"],
                reason="profile_deviation",
            )

    return DecisionResponse(
        action=action, risk_score=risk_score, label=label, message=ACTION_MESSAGES[action], reason="score"
    )


# --- Per-customer profile: the decision path ---------------------------------
#
# Everything below is reached only when PROFILE_ENABLED and the caller named a
# customer it is entitled to name. With the layer off, _load_profile_context
# returns before its first statement and _learn_and_audit before its first
# write, so a default deployment issues exactly the statements it issued before
# the layer existed (asserted by test_layer_is_off_by_default).


@dataclass
class ProfileContext:
    """What the profile layer knows about one decision.

    Built once by _load_profile_context, read by the escalation check, and
    reused by learning, so learning costs no second read of the flushes.
    Never serialised into any response: profile_id lives here and reaches the
    decision_audit table and nothing else (no log, no body -- section 11.1).
    """

    merchant_id: str
    verdict: profiles.ProfileVerdict
    # Set only for an ACTIVE profile (consented, not erased, not objected). A
    # decision about a customer who never consented, or who objected, must not
    # leave that customer's pseudonym in an audit row: no consent means no
    # personal data, and a tombstone is not consent.
    profile_id: str | None = None
    escalation_count: int = 0
    escalation_window_start: datetime | None = None
    # The profile may be taught by this session: active, and stamped with the
    # current feature schema and key version. The per-decision conditions (the
    # final action, sessions.profile_learned, the daily cap) are checked at
    # learning time.
    learnable: bool = False
    modality: str | None = None
    # profiles.session_vector() of this session, or None when it was too thin.
    vector: dict | None = None
    # Probation vectors stored for this modality when the references were read:
    # reported beside the verdict's reference_n, never part of it. None when the
    # vectors were not read at all.
    probation_n: int | None = None
    # A SYNTHETIC demo customer (customer_profiles.is_synthetic, seeded by
    # backend/demo_seed.py): compared against, never taught, and written into
    # the decision audit row so the SOC panel and a reviewer can say so.
    synthetic: bool = False


_PROFILE_ROW_COLUMNS = (
    CustomerProfile.profiling_enabled,
    CustomerProfile.consent_basis,
    CustomerProfile.erased_at,
    CustomerProfile.key_version,
    CustomerProfile.feature_schema_version,
    CustomerProfile.escalation_count,
    CustomerProfile.escalation_window_start,
    CustomerProfile.is_synthetic,
)

# The SPRT read in _decide_on_evidence, widened by what a session vector needs:
# the twelve features, which of them were measured, and the pointer mix that
# decides the modality.
_PROFILE_FLUSH_COLUMNS = (
    BehaviorData.measured_mask,
    BehaviorData.client_signals,
    *(getattr(BehaviorData, name) for name in FEATURE_NAMES),
)

# The per-day learning cap is counted on the Europe/Istanbul calendar day, named
# explicitly because a cap that resets at 03:00 local time (UTC midnight) is a
# surprise in a support ticket. The boundary is computed here and the COUNT runs
# in SQL -- never in the in-process _rate_hits dictionary, which is per worker
# and lost on restart. tzdata ships with pandas, which requirements.txt pins.
_PROFILE_DAY_ZONE = ZoneInfo("Europe/Istanbul")


def _columns_as_dict(columns, row) -> dict:
    return {column.key: value for column, value in zip(columns, row)}


async def _read_profile_row(db: AsyncSession, profile_id: str) -> dict | None:
    row = (
        await db.execute(select(*_PROFILE_ROW_COLUMNS).where(CustomerProfile.profile_id == profile_id))
    ).first()
    return None if row is None else _columns_as_dict(_PROFILE_ROW_COLUMNS, row)


async def _load_profile_context(
    db: AsyncSession, session_id: str, merchant_id: str | None, customer_ref: str | None
) -> ProfileContext | None:
    """The profile layer's opinion about this session, or None.

    None, with NO statement issued, when the layer is off or no customer was
    named. Otherwise at most three reads: the profile row; this session's
    recent flushes; that profile's reference vectors for this session's
    modality. A missing, suppressed, objected or version-mismatched profile
    stops after the first.

    A database failure is logged as the exception CLASS and answered 503: its
    message renders the statement's bound parameters, and here those are the
    profile id, which never reaches a log (section 11.1).
    """
    if not PROFILE_ENABLED or merchant_id is None or customer_ref is None:
        return None
    profile_id = profiles.derive_profile_id(merchant_id, customer_ref, PROFILE_KEY)
    if not _rate_allows("profile", profile_id):
        # Too many decisions about this one customer this hour. Nothing is
        # read -- no answer about this customer may depend on the profile any
        # more -- and when enforcing, _decide_on_evidence asks for step-up
        # instead of approving. Never a 429, which failed the whole checkout
        # (see RATE_LIMITS["profile"]), and never an abstention: an attacker
        # in a victim's account who could exhaust the bucket and then be
        # judged without the profile would have turned retries into an
        # approval, exactly the hole the challenge budget had. No profile id
        # on the context either: consent was not checked.
        return ProfileContext(
            merchant_id=merchant_id, verdict=profiles.ProfileVerdict(state=profiles.STATE_RATE_LIMITED)
        )
    try:
        return await _read_profile_context(db, session_id, merchant_id, profile_id)
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("Musteri profili okunamadi (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Profil islemi su anda tamamlanamadi, lutfen tekrar deneyin"
        ) from None


async def _read_profile_context(
    db: AsyncSession, session_id: str, merchant_id: str, profile_id: str
) -> ProfileContext:
    row = await _read_profile_row(db, profile_id)
    if row is None and merchant_id == DEMO_MERCHANT_ID:
        # The ONLY implicit profile creation in the system (spec 5.7): the demo
        # page's reserved namespace, which require_merchant can never return,
        # so it cannot collide with a merchant's customer. is_demo rows are
        # excluded from every reported measurement.
        now = utcnow()
        await db.execute(
            pg_insert(CustomerProfile)
            .values(
                profile_id=profile_id,
                merchant_id=DEMO_MERCHANT_ID,
                is_demo=True,
                profiling_enabled=True,
                consent_basis="demo",
                consent_recorded_at=now,
                key_version=PROFILE_KEY_VERSION,
                feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(index_elements=["profile_id"])
        )
        row = await _read_profile_row(db, profile_id)

    if row is None:
        return ProfileContext(merchant_id=merchant_id, verdict=profiles.ProfileVerdict(state=profiles.STATE_NO_PROFILE))
    if not row["profiling_enabled"] or row["erased_at"] is not None or row["consent_basis"] in ("objected", "none"):
        return ProfileContext(merchant_id=merchant_id, verdict=profiles.ProfileVerdict(state=profiles.STATE_SUPPRESSED))

    ctx = ProfileContext(
        merchant_id=merchant_id,
        verdict=profiles.ProfileVerdict(state=profiles.STATE_IMMATURE),
        profile_id=profile_id,
        escalation_count=int(row["escalation_count"] or 0),
        escalation_window_start=_as_utc(row["escalation_window_start"]),
        synthetic=bool(row["is_synthetic"]),
    )
    if (
        row["feature_schema_version"] != profiles.FEATURE_SCHEMA_VERSION
        or row["key_version"] != PROFILE_KEY_VERSION
    ):
        # Stored vectors were computed under another feature definition (or
        # the profile belongs to another key generation). Comparing across
        # that line would make every profiled customer deviate on the same
        # afternoon, so the profile is immature: no comparison, no learning.
        return ctx

    flush_rows = (
        await db.execute(
            select(*_PROFILE_FLUSH_COLUMNS)
            .where(BehaviorData.session_id == session_id)
            .order_by(BehaviorData.created_at.desc())
            .limit(SPRT_MAX_FLUSHES)
        )
    ).all()
    flushes = [_columns_as_dict(_PROFILE_FLUSH_COLUMNS, r) for r in flush_rows]
    ctx.modality = profiles.session_modality([f["client_signals"] for f in flushes])
    ctx.vector = profiles.session_vector(flushes)
    # A synthetic demo customer is an exhibit: compared against, never taught.
    # Learning into it would store a real person's session -- a juror's, under
    # the demo consent basis nobody gave -- inside a profile every screen calls
    # synthetic, and would change the exhibit after the seed day (the per-day
    # cap only hides this on the day demo_seed.py wrote its forty rows).
    ctx.learnable = not ctx.synthetic
    if ctx.vector is None:
        ctx.verdict = profiles.ProfileVerdict(state=profiles.STATE_THIN_SESSION, modality=ctx.modality)
        return ctx

    stored = (
        await db.execute(
            select(CustomerProfileVector.vec, CustomerProfileVector.probation)
            .where(CustomerProfileVector.profile_id == profile_id)
            .where(CustomerProfileVector.modality == ctx.modality)
            .where(CustomerProfileVector.feature_schema_version == profiles.FEATURE_SCHEMA_VERSION)
            .where(CustomerProfileVector.outcome != "disputed")
            # Never this session's own learned vector. A session decided again
            # after it was learned (a second checkout once its step-up has
            # expired) would otherwise be ranked against a calibration set that
            # contains itself: a rescued deviation then sits next to its own
            # copy and its p-value moves from 1/(n+1) to 2/(n+1), past alpha,
            # so the challenge silently disappears. Under the full-conformal
            # rank the copy and the candidate are scored against the same
            # points and always tie, so without this filter a challenge that
            # rested on the session being the most extreme point is lost every
            # time (24 of 24 synthetic reference sets in test_profiles; 23 of
            # 24 under the leave-one-out rank, where the Postgres check
            # measured 0.0476 -> 0.0952). The review endpoint excludes it for
            # the same reason. (The review reads the buffer as it is when the
            # reviewer asks, which later learns may have changed; it reports
            # whether its recomputation still matches the audited numbers.)
            .where(CustomerProfileVector.session_id != session_id)
            # References first, newest first; then the probation vectors, read
            # in the same statement only to be COUNTED. However many rows
            # exist, the newest PROFILE_BUFFER_MAX references always come back
            # whole, because they sort ahead of every probation row; the count
            # is exact while the storage caps (20 + 4) hold.
            .order_by(CustomerProfileVector.probation.asc(), CustomerProfileVector.created_at.desc())
            .limit(profiles.PROFILE_BUFFER_MAX + profiles.PROFILE_PROBATION_MAX)
        )
    ).all()
    # Probation vectors are not references: excluded from the deviation, the
    # conformal rank and the maturity count until promoted (settled by the
    # merchant, or healed). One passed step-up used to buy an attacker a
    # reference that shielded every later session less extreme than his own
    # -- measured on synthetic identities, 48.5% -> 25.0% escalated with one
    # such vector on 2026-09-16, and still 47.5% -> 25.5% for a vector that
    # counts under the shipped rank; stored on probation, the attacker's rate
    # stays 47.5% however many are learned (docs/profile-evaluation.md
    # section 7). The accepted cost is that the grandchild's NEXT session in
    # the same pattern is challenged again (71.7% of such synthetic sessions
    # on mouse), until the merchant settles the earlier payment or healing
    # promotes the pattern: friction, not a block
    # (profiles.PROFILE_PROBATION_MAX).
    references = [vec for vec, probation in stored if not probation][: profiles.PROFILE_BUFFER_MAX]
    ctx.probation_n = sum(1 for _, probation in stored if probation)
    ctx.verdict = profiles.evaluate_profile(ctx.vector["vec"], references, modality=ctx.modality)
    return ctx


# Per worker, like every in-process counter in this file. Holds the last count
# of enforced profile escalations and when it was read, plus the breaker window
# in which this worker last logged, so a tripped breaker logs once per window
# rather than once per checkout.
_profile_breaker = {"checked_at": None, "count": 0, "warned_window": None}


async def _profile_breaker_count(db: AsyncSession) -> int:
    """CUSTOMERS given an enforced profile challenge deployment-wide in the
    breaker window, cached per worker for PROFILE_BREAKER_CACHE_S. Shadow rows
    do not count: they challenged nobody.

    Distinct profiles, not audit rows. Every press of pay on a challenged
    session writes a profile_deviation row, so counting rows let one attacker
    in one session trip the deployment-wide breaker by pressing pay 50 times --
    switching this layer off for every customer for up to an hour, the step
    before an account-takeover campaign. Counted per profile, one account adds
    at most one however often it retries; tripping it takes 50 different
    mature profiles deviating within the hour, which is the population event
    the breaker exists to notice (thresholds that misfire on real traffic).
    An erased profile's rows have profile_id NULL and drop out, which errs
    towards leaving the breaker closed."""
    now = time.monotonic()
    checked_at = _profile_breaker["checked_at"]
    if checked_at is not None and now - checked_at < profiles.PROFILE_BREAKER_CACHE_S:
        return _profile_breaker["count"]
    cutoff = utcnow() - timedelta(seconds=profiles.PROFILE_BREAKER_WINDOW_S)
    try:
        count = await db.scalar(
            select(func.count(distinct(DecisionAudit.profile_id)))
            .select_from(DecisionAudit)
            .where(DecisionAudit.reason == "profile_deviation")
            .where(DecisionAudit.shadow.is_(False))
            .where(DecisionAudit.decided_at >= cutoff)
        )
    except Exception as exc:
        # Cannot tell whether the ceiling is reached, so behave as if it were:
        # the failure mode of an escalation-only control is to say nothing.
        # Not cached, so the next decision asks again.
        logger.error("Profil devre kesici sayilamadi (%s)", type(exc).__name__)
        return profiles.PROFILE_BREAKER_MAX
    _profile_breaker.update(checked_at=now, count=int(count or 0))
    return _profile_breaker["count"]


async def _profile_escalates(db: AsyncSession, session: Session, session_id: str, ctx: ProfileContext) -> bool:
    """Whether a profile deviation becomes a verify on THIS decision.

    Called only for an allow/warn whose profile verdict says escalate. May
    replace ctx.verdict with an abstention state (budget_exhausted, breaker),
    which is what the audit row and the SOC panel then show.
    """
    pv = ctx.verdict
    if not PROFILE_ESCALATION:
        # Shadow mode: computed, stored in the audit row, acted on never.
        logger.info(
            "profile shadow escalation for session %s (p=%.3f, n=%d, modality=%s)",
            session_id, pv.p_value, pv.reference_n, pv.modality,
        )
        return False

    # A session whose step-up is still fresh is not going to be challenged:
    # _apply_step_up turns this verify straight into allow/verified. The budget
    # and the breaker bound challenges actually put in front of a person, so
    # neither is consulted -- and the rescued session is then learned on
    # probation, which is what records the pass (spending the budget) and
    # drives self-healing. Consulting the budget here could only turn a pass
    # into one that is never recorded.
    if not _step_up_is_fresh(session):
        if not profiles.budget_allows(ctx.escalation_count, ctx.escalation_window_start, utcnow()):
            ctx.verdict = replace(pv, state=profiles.STATE_BUDGET_EXHAUSTED, escalate=False)
            return False
        escalations = await _profile_breaker_count(db)
        if not profiles.breaker_allows(escalations):
            ctx.verdict = replace(pv, state=profiles.STATE_BREAKER, escalate=False)
            window = int(time.time() // profiles.PROFILE_BREAKER_WINDOW_S)
            if _profile_breaker["warned_window"] != window:
                _profile_breaker["warned_window"] = window
                # No identifiers: the breaker is a population statement.
                logger.warning(
                    "UYARI: Profil devre kesici acildi: son %d saniyede en az %d farkli musteriye "
                    "zorunlu profil yukseltmesi; profil yukseltmeleri gecici olarak durduruldu.",
                    profiles.PROFILE_BREAKER_WINDOW_S,
                    escalations,
                )
            return False

    logger.info(
        "profile escalation for session %s (p=%.3f, n=%d, modality=%s)",
        session_id, pv.p_value, pv.reference_n, pv.modality,
    )
    return True


async def _learn_and_audit(
    db: AsyncSession,
    session: Session,
    session_id: str,
    verdict: DecisionResponse,
    pre_step_up_reason: str,
    ctx: ProfileContext | None,
    merchant_id: str | None,
    risk_context: RiskContext | None,
) -> None:
    """Everything the profile layer writes for one decision, in one transaction.

    Nothing at all while the layer is off. Otherwise an audit row whenever the
    action is not allow or the layer had any opinion (shadow and abstentions
    included) -- not on a plain allow with no customer named, so the common
    path stays a read. The session is learned when the final answer is allow
    (section 6.5), and the challenge budget is spent by the learn that records
    a PASSED profile challenge -- never when a challenge is issued (see below).

    A write failure never changes the verdict, which is already decided: it is
    rolled back and logged as the exception class only, because a database
    error message carries the bound parameters and here those include the
    profile id.
    """
    if not PROFILE_ENABLED or (ctx is None and verdict.action == "allow"):
        return
    try:
        if ctx is not None and ctx.profile_id is not None:
            # Probation: learned only because step-up rescued a profile
            # escalation.
            probation = pre_step_up_reason == "profile_deviation" and verdict.reason == "verified"
            # A deviation that step-up did NOT rescue is never learned: not in
            # shadow mode, and not when the budget or the breaker suppressed
            # the challenge. Learned as a clean vector it would bypass the
            # PROFILE_PROBATION_MAX cap -- whoever pays while the budget is
            # spent or the breaker is open would be widening the profile with
            # clean slots at PROFILE_LEARN_PER_DAY. (A stricter reading of spec
            # 6.5 than its literal list of conditions, for the guarantee 6.5
            # states.)
            deviated = ctx.verdict.escalate or ctx.verdict.state in (
                profiles.STATE_BUDGET_EXHAUSTED,
                profiles.STATE_BREAKER,
            )
            if (
                # warn never teaches: it is the approval the ladder was least
                # sure about.
                verdict.action == "allow"
                # Nor does any other verify that a step-up rescued: a 60-80
                # score, an ambiguous or stale session, a crossed bot bound
                # (sequential), a softened block, a cluster. The customer
                # proved who they are for THIS payment; the behaviour itself
                # was never something the evidence was confident in, and a
                # profile is built only from sessions the evidence alone
                # approved ("score") -- or, on probation, from a rescued
                # PROFILE deviation, which is the one verify whose cause is
                # this customer's own history. A rescued sequential session
                # used to be learned as a clean reference.
                and (probation or verdict.reason == "score")
                and ctx.learnable
                and ctx.vector is not None
                and (probation or not deviated)
                and not getattr(session, "profile_learned", False)
            ):
                learned = await _learn_session(db, session_id, ctx, probation=probation)
                # The challenge budget counts challenges the customer PASSED,
                # and this learn is where a pass is recorded -- once per
                # session, by the unique (profile_id, session_id) index. It
                # used to be spent whenever a profile challenge was ISSUED, so
                # an account-takeover attacker without the OTP -- the one
                # person this layer exists to stop -- only had to press pay
                # again: three unanswered challenges exhausted the budget and
                # the fourth press was charged, for that session and for every
                # new one for 30 days (reproduced over HTTP: verify, verify,
                # verify, allow). A budget an unanswered challenge can spend is
                # an off switch. Counting passes keeps what the budget is for --
                # a customer who keeps proving it is them (the grandchild, the
                # tremor, the new laptop) is challenged at most
                # PROFILE_MAX_ESCALATIONS times a window -- and nothing an
                # attacker who cannot answer does moves it.
                #
                # The price, stated: a pass that is not learned is not counted
                # either (the per-day learning cap, a session that already
                # taught the profile, a synthetic demo customer, which is never
                # taught). And DeepCheck only hears of a pass where it records
                # the step-up itself -- /api/demo/verify; a merchant running
                # its own OTP has no endpoint to report one yet -- so outside
                # the demo neither this bound nor self-healing is reached.
                if learned and probation:
                    await _spend_profile_budget(db, ctx)
                row_locked = learned
            else:
                row_locked = False
            if not row_locked and not await _profile_still_active(db, ctx.profile_id):
                # Erased or objected while this decision was being made: its
                # audit row must not carry the pseudonym the erasure just
                # removed everywhere else (see _profile_still_active).
                ctx.profile_id = None
        db.add(
            _decision_audit_row(
                session_id,
                verdict,
                ctx,
                merchant_id,
                risk_context,
                # getattr: sessions.is_synthetic is set only by demo_seed.py
                # --simulate, and the test suite's stub sessions predate it.
                session_synthetic=bool(getattr(session, "is_synthetic", False)),
            )
        )
        await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("Profil karar kaydi yazilamadi (%s)", type(exc).__name__)


def _decision_audit_row(
    session_id: str,
    verdict: DecisionResponse,
    ctx: ProfileContext | None,
    merchant_id: str | None,
    risk_context: RiskContext | None,
    *,
    session_synthetic: bool = False,
) -> DecisionAudit:
    pv = ctx.verdict if ctx is not None else None
    score = verdict.risk_score
    return DecisionAudit(
        session_id=session_id,
        profile_id=ctx.profile_id if ctx is not None else None,
        merchant_id=ctx.merchant_id if ctx is not None else merchant_id,
        action=verdict.action,
        # The internal reason, which the client was not told...
        reason=verdict.reason,
        # ...and what it was told instead.
        public_reason=_public_verdict(verdict).reason,
        risk_score=score if score is not None and math.isfinite(score) else None,
        profile_state=pv.state if pv is not None else None,
        deviation=pv.deviation if pv is not None else None,
        p_value=pv.p_value if pv is not None else None,
        p_value_low=pv.p_value_low if pv is not None else None,
        top_features=list(pv.top_features) if pv is not None else None,
        modality=pv.modality if pv is not None else None,
        reference_n=pv.reference_n if pv is not None else None,
        probation_n=ctx.probation_n if ctx is not None else None,
        feature_schema_version=profiles.FEATURE_SCHEMA_VERSION if pv is not None else None,
        shadow=pv is not None and not PROFILE_ESCALATION,
        # What was compared, only when the comparison found a deviation and
        # the row still names an active profile (see
        # models.DecisionAudit.candidate_vec): the evidence a human reviewer
        # needs once the flushes are gone.
        candidate_vec=(
            ctx.vector["vec"]
            if ctx is not None
            and ctx.profile_id is not None
            and ctx.vector is not None
            and pv is not None
            and (pv.escalate or pv.state in (profiles.STATE_BUDGET_EXHAUSTED, profiles.STATE_BREAKER))
            else None
        ),
        amount_band=risk_context.amount_band if risk_context is not None else None,
        new_beneficiary=risk_context.new_beneficiary if risk_context is not None else None,
        # Stored, not looked up later: the session is deleted at 24 h and
        # demo_seed.py --reset deletes the profile, but a decision made against
        # a simulator must still say so to the SOC panel for as long as the row
        # is kept.
        is_synthetic=session_synthetic or (ctx is not None and ctx.synthetic),
    )


async def _profile_still_active(db: AsyncSession, profile_id: str) -> bool:
    """Whether the profile is still active, holding a FOR SHARE lock on its row
    until this decision commits.

    The audit row names the profile it was decided against, but the profile
    was read at the start of the decision, and an erasure can commit in
    between. Its `UPDATE decision_audit SET profile_id = NULL` cannot see a row
    inserted after it, so the decision's row kept the erased pseudonym for
    DECISION_AUDIT_RETENTION_DAYS -- reproduced on Postgres 16 (read the
    context, commit an erasure, write the audit row: one row still held the
    id, with its deviation and top features). The share lock orders the two:
    the erasure's FOR UPDATE on the same row either finished first, and this
    finds no active row, or waits for this commit, and its unlink then sees
    this audit row. A learn has already locked the row with its UPDATE, so
    this read is skipped after one."""
    found = await db.scalar(
        select(CustomerProfile.profile_id)
        .where(CustomerProfile.profile_id == profile_id)
        .where(CustomerProfile.profiling_enabled.is_(True))
        .where(CustomerProfile.erased_at.is_(None))
        .where(CustomerProfile.consent_basis != "objected")
        .with_for_update(read=True)
    )
    return found is not None


async def _spend_profile_budget(db: AsyncSession, ctx: ProfileContext) -> None:
    """One PASSED challenge from this customer's PROFILE_MAX_ESCALATIONS per
    window (called only by the probation learn, see _learn_and_audit). The
    increment is done by the database, so two concurrent passes for one
    customer both count."""
    now = utcnow()
    if profiles.budget_window_expired(ctx.escalation_window_start, now):
        values = {"escalation_count": 1, "escalation_window_start": now}
    else:
        values = {"escalation_count": CustomerProfile.escalation_count + 1}
    await db.execute(update(CustomerProfile).where(CustomerProfile.profile_id == ctx.profile_id).values(**values))


async def _learn_session(db: AsyncSession, session_id: str, ctx: ProfileContext, *, probation: bool) -> bool:
    """Teach the profile this session's vector. Returns whether it was learned.

    The profile row is locked FIRST, by the UPDATE that refreshes its activity
    clock and re-checks that it is still active. The erasure handler locks the
    same row before deleting vectors, so a learn and an erasure serialise: a
    vector can never be inserted into a profile that is being erased and then
    outlive the erasure. (It also means a learn that turns out to be a double
    learn still refreshes last_seen_at, which is activity either way.)
    """
    now = utcnow()
    locked = (
        await db.execute(
            update(CustomerProfile)
            .where(CustomerProfile.profile_id == ctx.profile_id)
            .where(CustomerProfile.profiling_enabled.is_(True))
            .where(CustomerProfile.erased_at.is_(None))
            .where(CustomerProfile.consent_basis != "objected")
            .where(CustomerProfile.feature_schema_version == profiles.FEATURE_SCHEMA_VERSION)
            .where(CustomerProfile.key_version == PROFILE_KEY_VERSION)
            .values(last_seen_at=now, updated_at=now)
            .returning(CustomerProfile.consecutive_passed_escalations)
        )
    ).first()
    if locked is None:
        # Erased, objected or re-versioned since this decision read it.
        return False

    day_start = now.astimezone(_PROFILE_DAY_ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    learned_today = await db.scalar(
        select(func.count())
        .select_from(CustomerProfileVector)
        .where(CustomerProfileVector.profile_id == ctx.profile_id)
        .where(CustomerProfileVector.created_at >= day_start)
    )
    if int(learned_today or 0) >= profiles.PROFILE_LEARN_PER_DAY:
        return False

    # THE learn-once mechanism: the unique (profile_id, session_id) index. A
    # second learn of the same session inserts nothing, whichever worker runs it.
    inserted = (
        await db.execute(
            pg_insert(CustomerProfileVector)
            .values(
                profile_id=ctx.profile_id,
                session_id=session_id,
                modality=ctx.modality,
                feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                vec=ctx.vector["vec"],
                disp=ctx.vector["disp"],
                flush_count=ctx.vector["flush_count"],
                probation=probation,
                outcome="pending",
            )
            .on_conflict_do_nothing(index_elements=["profile_id", "session_id"])
            .returning(CustomerProfileVector.id, CustomerProfileVector.created_at)
        )
    ).first()
    if inserted is None:
        return False
    new_id, created_at = inserted

    buffer = [
        {"id": r[0], "created_at": _as_utc(r[1]), "probation": r[2]}
        for r in (
            await db.execute(
                select(CustomerProfileVector.id, CustomerProfileVector.created_at, CustomerProfileVector.probation)
                .where(CustomerProfileVector.profile_id == ctx.profile_id)
                .where(CustomerProfileVector.modality == ctx.modality)
                .where(CustomerProfileVector.feature_schema_version == profiles.FEATURE_SCHEMA_VERSION)
                .where(CustomerProfileVector.id != new_id)
            )
        ).all()
    ]
    # References and probation vectors are capped separately, so a probation
    # vector evicts only the oldest probation vector and never a reference
    # (profiles.enforce_buffer_caps).
    kept, evicted = profiles.admit_vector(
        buffer, {"id": new_id, "created_at": _as_utc(created_at), "probation": probation}
    )

    passed = int(locked[0] or 0) + 1 if probation else 0
    profile_values: dict = {}
    if passed != int(locked[0] or 0):
        profile_values["consecutive_passed_escalations"] = passed
    # Empty unless this learn completes PROFILE_HEAL_AFTER passed challenges in
    # a row AND that many probation vectors of this modality still stand in a
    # row: a single passed step-up is never promoted, whatever the counter says.
    promoted = profiles.promotable_run(kept, passed) if probation else []
    if promoted:
        # Three challenges in a row, each answered correctly by the customer,
        # is evidence that the PROFILE is wrong -- not that the customer is a
        # fraudster. It is the same rule for the grandchild paying on
        # grandmother's behalf, the new laptop, the traveller, the hand tremor
        # and the switch to assistive input. A fraud control that challenges
        # disabled or elderly users indefinitely needs an explicit bound, not
        # an apology.
        #
        # Probation vectors are not references, so the rebuild has to PROMOTE
        # the run of passed challenges -- otherwise a customer whose behaviour
        # really changed would be challenged, pass, and be challenged again for
        # as long as no merchant settles anything. Why this does not reopen
        # the poisoning hole: profiles.promotable_run.
        for entry in promoted:
            entry["probation"] = False
        kept, dropped = profiles.heal_buffer(kept)
        evicted.extend(dropped)
        profile_values = {"consecutive_passed_escalations": 0, "stats_rebuilt_at": now}
        logger.info(
            "profile rebuilt for session %s after %d passed challenges, %d promoted (modality=%s)",
            session_id, passed, len(promoted), ctx.modality,
        )
    evicted_ids = {e["id"] for e in evicted}
    if evicted:
        await db.execute(delete(CustomerProfileVector).where(CustomerProfileVector.id.in_(sorted(evicted_ids))))
    promoted_ids = sorted(e["id"] for e in promoted if e["id"] not in evicted_ids)
    if promoted_ids:
        await db.execute(
            update(CustomerProfileVector)
            .where(CustomerProfileVector.profile_id == ctx.profile_id)
            .where(CustomerProfileVector.id.in_(promoted_ids))
            .values(probation=False)
        )
    if profile_values:
        await db.execute(
            update(CustomerProfile).where(CustomerProfile.profile_id == ctx.profile_id).values(**profile_values)
        )
    # A session teaches a profile at most once. last_seen_at is assigned to
    # itself to suppress its onupdate=now(): it is /api/decision's freshness
    # clock (see _unlink_sessions_from_profiles).
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(profile_learned=True, profile_id=ctx.profile_id, last_seen_at=Session.last_seen_at)
    )
    return True


def _profile_request(
    customer_ref: str | None, x_merchant_id: str | None, x_merchant_key: str | None
) -> tuple[str | None, str | None]:
    """The (merchant_id, customer_ref) a decision may use, or (None, None).

    The asymmetry is deliberate. A MISSING or WRONG credential next to a
    reference is loud (400 / 401): an integration that sends references
    without authenticating is broken, and a silent pass would let anyone with
    a session token name any customer. A DISABLED layer is silent: the
    reference is ignored entirely, nothing is read or written, and the
    decision is exactly what it would be without it -- a misconfiguration must
    not break a merchant's checkout. The per-customer "profile" bucket is part
    of the layer, so it is not charged while the layer is off either (it is
    charged in _load_profile_context, and never refuses the decision).

    Merchant headers are consulted only when a reference is present; without
    one the request is the same request it was before references existed.
    """
    if customer_ref is None:
        return None, None
    if not x_merchant_id or not x_merchant_key:
        raise HTTPException(
            status_code=400, detail="Musteri referansi icin satici kimlik dogrulamasi gerekli"
        )
    merchant_id = require_merchant(x_merchant_id, x_merchant_key)
    customer_ref = _require_customer_ref(customer_ref)
    if not PROFILE_ENABLED:
        return None, None
    return merchant_id, customer_ref


@app.post("/api/decision", response_model=DecisionResponse)
async def decision(
    payload: DecisionRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    x_merchant_id: Annotated[str | None, Header()] = None,
    x_merchant_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The enforcement point. A merchant backend calls this at checkout and
    obeys `action`. The previous design put this decision in the browser,
    where anyone could edit it away."""
    _require_session_token(payload.session_id, x_deepcheck_token)
    _rate_limit("decision", payload.session_id)
    merchant_id, customer_ref = _profile_request(payload.customer_ref, x_merchant_id, x_merchant_key)
    verdict = await _decide(
        db,
        payload.session_id,
        merchant_id=merchant_id,
        customer_ref=customer_ref,
        risk_context=payload.risk_context,
    )
    return _public_verdict(verdict)


async def _profile_transaction(db: AsyncSession, what: str, work) -> object:
    """Runs `work` and commits, as one transaction, or answers 503.

    The failure is logged as the exception CLASS only, never the exception:
    a database error renders the failed statement's bound parameters into its
    message, and on these endpoints those parameters are profile ids -- which
    never reach an application log, not even truncated. For the same reason
    the HTTPException is raised `from None`.
    """
    try:
        result = await work()
        await db.commit()
        return result
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("%s kaydedilemedi (%s)", what, type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Profil islemi su anda tamamlanamadi, lutfen tekrar deneyin"
        ) from None


@app.post("/api/profile/consent", status_code=201)
async def profile_consent(
    payload: ProfileConsentRequest,
    x_merchant_id: Annotated[str | None, Header()] = None,
    x_merchant_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Records a customer's lawful basis and turns profiling on.

    The ONLY thing in the system that creates a profile row. /api/decision
    never does: no consent means no row, which means no personal data and no
    escalation -- and a customer who refuses costs nothing, because there is
    nothing to challenge them against.

    One atomic upsert. The WHERE on the conflict branch is what makes an
    objection final: a tombstone (consent_basis='objected') is never
    overwritten, and a concurrent objection is re-checked against the row as
    it stands after the objection commits.
    """
    if not PROFILE_ENABLED:
        raise HTTPException(status_code=503, detail="Musteri profili katmani bu dagitimda kapali")
    merchant_id = require_merchant(x_merchant_id, x_merchant_key)
    _rate_limit("profile_admin", merchant_id)
    customer_ref = _require_customer_ref(payload.customer_ref)
    if payload.basis not in PROFILE_CONSENT_BASES:
        raise HTTPException(status_code=400, detail="Gecersiz hukuki dayanak")

    profile_id = profiles.derive_profile_id(merchant_id, customer_ref, PROFILE_KEY)
    now = utcnow()
    consent = {
        "profiling_enabled": True,
        "consent_basis": payload.basis,
        "consent_recorded_at": now,
        "key_version": PROFILE_KEY_VERSION,
        "feature_schema_version": profiles.FEATURE_SCHEMA_VERSION,
        "erased_at": None,
        # Consent is activity: the idle-retention clock starts again.
        "last_seen_at": now,
    }
    statement = (
        pg_insert(CustomerProfile)
        .values(profile_id=profile_id, merchant_id=merchant_id, **consent)
        .on_conflict_do_update(
            index_elements=["profile_id"],
            # ON CONFLICT DO UPDATE does not apply Column.onupdate, so the
            # timestamp is set by hand.
            set_={**consent, "updated_at": now},
            where=CustomerProfile.consent_basis != "objected",
        )
        .returning(CustomerProfile.profile_id)
    )

    async def work():
        return (await db.execute(statement)).first()

    row = await _profile_transaction(db, "Profil onayi", work)
    if row is None:
        # The conflict branch's WHERE refused: this reference carries an
        # objection tombstone. Nothing was written.
        raise HTTPException(status_code=409, detail="Bu musteri profillemeye itiraz etti")
    return {"status": "enabled"}


@app.post("/api/profile/erase", status_code=204, response_class=Response)
async def profile_erase(
    payload: ProfileEraseRequest,
    x_merchant_id: Annotated[str | None, Header()] = None,
    x_merchant_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Erasure (`erase`) or objection (`object`) for one customer.

    POST, not DELETE: a DELETE body is legal but proxies and clients drop it,
    and the reference must never move into the URL -- a URL lands in access
    logs, proxy logs and browser history, the one place an erasure endpoint
    must not put the thing it is erasing.

    204 always, with an identical response whether or not a profile existed:
    no existence oracle over the merchant's customer list.

    The two modes are different requests on purpose. `erase` deletes
    everything, and a later consent may start a profile from scratch.
    `object` deletes the same data but KEEPS the profile row as a tombstone,
    creating one if none existed, because deleting data does not stop
    profiling: the next consent call would simply recreate what the customer
    refused. A tombstone also survives a later `erase`.

    Deliberately NOT gated on PROFILE_LAYER: switching the layer off must not
    switch off a data subject's erasure or objection. It needs only the
    merchant credential and the profile key, without which no profile id can
    be derived -- and then it says so (503) rather than confirming an erasure
    it could not perform.
    """
    merchant_id = require_merchant(x_merchant_id, x_merchant_key)
    _rate_limit("profile_admin", merchant_id)
    customer_ref = _require_customer_ref(payload.customer_ref)
    if payload.mode not in PROFILE_ERASE_MODES:
        raise HTTPException(status_code=400, detail="Gecersiz silme turu")
    if PROFILE_KEY is None:
        raise HTTPException(
            status_code=503, detail="Profil anahtari tanimli olmadigi icin silme islemi yapilamiyor"
        )

    profile_id = profiles.derive_profile_id(merchant_id, customer_ref, PROFILE_KEY)
    now = utcnow()

    async def work():
        # Lock the profile row BEFORE touching its vectors. Without it, a
        # vector learned between the vector delete and the profile delete
        # would outlive the erasure. With it, a learner that takes the same
        # row lock BEFORE inserting its vector either commits first -- and its
        # vector is deleted here -- or waits and then finds no row to learn
        # into. Both orders checked on Postgres 16 against a learner written
        # that way. The learning path must therefore lock (or UPDATE) the
        # profile row before its vector INSERT, not after it.
        await db.execute(
            select(CustomerProfile.profile_id)
            .where(CustomerProfile.profile_id == profile_id)
            .with_for_update()
        )
        await db.execute(
            delete(CustomerProfileVector).where(CustomerProfileVector.profile_id == profile_id)
        )
        if payload.mode == "erase":
            await db.execute(
                delete(CustomerProfile)
                .where(CustomerProfile.profile_id == profile_id)
                # An objection survives erasure: it is the record that stops
                # the profile from being recreated.
                .where(CustomerProfile.consent_basis != "objected")
            )
        else:
            tombstone = {
                "profiling_enabled": False,
                "consent_basis": "objected",
                "erased_at": now,
                "escalation_count": 0,
                "escalation_window_start": None,
                "consecutive_passed_escalations": 0,
                "stats_rebuilt_at": None,
            }
            await db.execute(
                pg_insert(CustomerProfile)
                .values(
                    profile_id=profile_id,
                    merchant_id=merchant_id,
                    key_version=PROFILE_KEY_VERSION,
                    feature_schema_version=profiles.FEATURE_SCHEMA_VERSION,
                    **tombstone,
                )
                .on_conflict_do_update(
                    index_elements=["profile_id"], set_={**tombstone, "updated_at": now}
                )
            )
        # NULLed, not cascaded: there is no foreign key (models.py), and the
        # session and audit rows themselves are not the customer's profile.
        await db.execute(_unlink_sessions_from_profiles(Session.profile_id == profile_id))
        await db.execute(
            update(DecisionAudit)
            .where(DecisionAudit.profile_id == profile_id)
            # The compared session vector goes with the link: it is the
            # customer's behavioural data, not part of the accountability
            # record (models.DecisionAudit.candidate_vec).
            .values(profile_id=None, candidate_vec=None)
        )
        # And the review accesses. GET /api/profile/review writes the profile
        # id into profile_access_audit, which is kept 365 days: left there,
        # it re-linked the erased customer to every decision_audit row of the
        # reviewed session (verdict, deviation, top features) through the
        # session id, and anyone holding the profile key and the reference
        # could recompute the pseudonym and find them. Operator, endpoint,
        # session and time stay: the accountability record is who looked at
        # what and when, not whose profile it was.
        await db.execute(_unlink_access_audit_from_profiles(ProfileAccessAudit.profile_id == profile_id))
        # Accountability for the request, in the access-controlled table and
        # not stdout. Without the profile id: an erasure that left the
        # pseudonym behind in an audit row for a year would not be one.
        db.add(ProfileAccessAudit(operator_id=merchant_id, endpoint="profile.erase"))

    await _profile_transaction(db, "Profil silme", work)
    return Response(status_code=204)


@app.post("/api/outcome", status_code=204, response_class=Response)
async def report_outcome(
    payload: OutcomeRequest,
    x_merchant_id: Annotated[str | None, Header()] = None,
    x_merchant_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The merchant's settlement feed: `settled` or `disputed` per session.

    Learning happens on an authorisation, not a settlement (a demo has no
    settlement feed, and a layer that never matures cannot be measured), so an
    undisputed fraud can sit in a customer's buffer until it is reported. This
    is how it is reported. `disputed` DELETES the session's vector -- an exact
    unlearn, possible because the buffer is the statistic -- and decrements
    nothing else: the profile simply has one stored vector fewer (one
    reference fewer, which may drop it below maturity, unless the vector was
    still on probation and counted for nothing), and that is the correct
    outcome. The session keeps profile_learned, so the disputed session cannot
    be learned again.

    `settled` is also the first of the two ways a PROBATION vector -- learned
    only because a step-up rescued a profile challenge -- becomes a reference
    (the other is the self-healing rebuild). A merchant should report it once
    the payment is confirmed legitimate, not at batch settlement: it is the
    merchant's statement that this session was the customer, and from then on
    the session counts toward maturity and shields later sessions like any
    other reference. A later `disputed` still deletes it. Why promotion does
    not reopen the poisoning hole: profiles.promotable_run.

    Restricted to profiles of the calling merchant. 204 always, so the answer
    says nothing about whether the session was profiled at all.

    Not gated on PROFILE_LAYER, for the same reason as erasure: removing a
    fraudulent reference must not depend on the layer being switched on.
    """
    merchant_id = require_merchant(x_merchant_id, x_merchant_key)
    _rate_limit("profile_admin", merchant_id)
    if payload.outcome not in PROFILE_OUTCOMES:
        raise HTTPException(status_code=400, detail="Gecersiz islem sonucu")

    async def work():
        # Lock the profile rows first, as learning and erasure do, for both
        # outcomes. A promotion and a concurrent learn of the same customer
        # then compute their evictions one after the other instead of from two
        # stale views of the buffer; and a dispute cannot delete a vector
        # that a concurrent learn has just counted when it chose which
        # reference to evict (which left the buffer one reference short).
        locked = (
            await db.execute(
                update(CustomerProfile)
                .where(CustomerProfile.merchant_id == merchant_id)
                .where(
                    CustomerProfile.profile_id.in_(
                        select(CustomerProfileVector.profile_id).where(
                            CustomerProfileVector.session_id == payload.session_id
                        )
                    )
                )
                .values(updated_at=utcnow())
                .returning(CustomerProfile.profile_id)
            )
        ).scalars().all()
        if not locked:
            return
        if payload.outcome == "settled":
            promoted = (
                await db.execute(
                    update(CustomerProfileVector)
                    .where(CustomerProfileVector.session_id == payload.session_id)
                    .where(CustomerProfileVector.profile_id.in_(sorted(locked)))
                    .values(outcome="settled", probation=False)
                    .returning(
                        CustomerProfileVector.profile_id,
                        CustomerProfileVector.modality,
                        CustomerProfileVector.feature_schema_version,
                    )
                )
            ).all()
            for profile_id, modality, schema_version in promoted:
                await _trim_references(db, profile_id, modality, schema_version)
        else:
            await db.execute(
                delete(CustomerProfileVector)
                .where(CustomerProfileVector.session_id == payload.session_id)
                .where(CustomerProfileVector.profile_id.in_(sorted(locked)))
            )

    await _profile_transaction(db, "Islem sonucu", work)
    return Response(status_code=204)


async def _trim_references(db: AsyncSession, profile_id: str, modality: str, schema_version: int) -> None:
    """Re-apply the storage caps to one (profile, modality) after a promotion
    turned a probation vector into a reference: at most PROFILE_BUFFER_MAX
    references, oldest evicted first -- which is the promoted vector itself
    when it is older than every reference it would join."""
    buffer = [
        {"id": r[0], "created_at": _as_utc(r[1]), "probation": r[2]}
        for r in (
            await db.execute(
                select(CustomerProfileVector.id, CustomerProfileVector.created_at, CustomerProfileVector.probation)
                .where(CustomerProfileVector.profile_id == profile_id)
                .where(CustomerProfileVector.modality == modality)
                .where(CustomerProfileVector.feature_schema_version == schema_version)
            )
        ).all()
    ]
    _, evicted = profiles.enforce_buffer_caps(buffer)
    if evicted:
        await db.execute(
            delete(CustomerProfileVector).where(CustomerProfileVector.id.in_(sorted(e["id"] for e in evicted)))
        )


@app.post("/api/demo/verify", response_model=VerifyResponse)
async def demo_verify(
    payload: VerifyRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Records a successful step-up on the SERVER.

    Stands in for the merchant's SMS / 3-D Secure provider. What matters for
    the pattern is where the result lives: the browser used to decide for
    itself that verification had succeeded and then run the payment. Now the
    only thing it can do is submit a code; whether that unlocks anything is
    decided here and read back by /api/demo/charge.
    """
    _require_demo_endpoints()
    _require_session_token(payload.session_id, x_deepcheck_token)
    # Same bucket as the checkout itself: without this the step-up code is a
    # six-digit secret an attacker may guess at unlimited speed.
    _rate_limit("decision", payload.session_id)

    if not hmac.compare_digest(payload.code.strip(), DEMO_VERIFY_CODE):
        raise HTTPException(status_code=400, detail="Dogrulama kodu hatali")

    session = await db.get(Session, payload.session_id)
    if session is None:
        # Nothing to attach the verification to: this client never sent a
        # single flush. Verifying an unobserved session would be exactly the
        # bypass the whole design exists to prevent.
        raise HTTPException(status_code=404, detail="Oturum bulunamadi")

    session.verified_at = utcnow()
    await db.commit()
    return VerifyResponse(verified=True, message=REASON_MESSAGES["verified"])


@app.post("/api/demo/charge", response_model=ChargeResponse)
async def demo_charge(
    payload: ChargeRequest,
    x_deepcheck_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The merchant side of the pattern, in miniature.

    A real merchant backend would call /api/decision and then its payment
    provider. Here both live in one endpoint so the demo proves the property
    a jury will test for: no sequence of browser actions produces a
    "charged" response for a session the server would not allow. Deleting
    every check in Demo.jsx changes nothing, because Demo.jsx has no checks.

    `customer_ref`, when present, names a customer in the reserved "demo"
    merchant namespace, where a profile is created on first use (consent
    basis "demo", is_demo=true) -- the one implicit creation the design
    allows, because no real merchant's customer can live there. A real
    integration sends the reference from the merchant's server to
    /api/decision with the merchant credential. With the layer off the
    reference is ignored exactly as /api/decision ignores it.
    """
    _require_demo_endpoints()
    _require_session_token(payload.session_id, x_deepcheck_token)
    _rate_limit("decision", payload.session_id)
    merchant_id = customer_ref = None
    if payload.customer_ref is not None:
        customer_ref = _require_customer_ref(payload.customer_ref)
        if PROFILE_ENABLED:
            # The per-customer "profile" bucket is charged in
            # _load_profile_context, as for a merchant's decision.
            merchant_id = DEMO_MERCHANT_ID
        else:
            customer_ref = None
    verdict = _public_verdict(
        await _decide(db, payload.session_id, merchant_id=merchant_id, customer_ref=customer_ref)
    )

    if verdict.action in ("allow", "warn"):
        return ChargeResponse(
            status="charged", charge_id=str(uuid.uuid4()), amount=payload.amount, decision=verdict
        )
    return ChargeResponse(status="declined", charge_id=None, amount=payload.amount, decision=verdict)


_AUDIT_BLOCK_COLUMNS = (
    DecisionAudit.profile_state,
    DecisionAudit.modality,
    DecisionAudit.reference_n,
    DecisionAudit.probation_n,
    DecisionAudit.deviation,
    DecisionAudit.p_value,
    DecisionAudit.p_value_low,
    DecisionAudit.top_features,
    DecisionAudit.reason,
    DecisionAudit.shadow,
    DecisionAudit.is_synthetic,
)


async def _profile_block(db: AsyncSession, session_id: str) -> dict:
    """The SOC panel's view of the profile layer for one session.

    ALWAYS present and ALWAYS the same keys, whatever the state: a
    dashboard-key holder must not be able to tell whether a session belongs
    to a profiled customer from whether a field exists. NEVER the profile id,
    the stored vectors, the customer reference or the merchant -- those live
    behind the per-operator review credential. The scored client never sees
    any of it: same argument as SHAP_IN_ANALYZE.

    Read from the newest decision audit row that carries a profile opinion, so
    it shows what the layer said when the decision was made, not a recomputed
    guess. With the layer off, nothing is read at all.
    """
    block = {
        "state": profiles.STATE_DISABLED,
        "modality": None,
        # The references the statistic used, and -- separately -- the probation
        # vectors stored beside them that it did not use ("Onay bekleyen").
        "reference_n": 0,
        "probation_n": 0,
        "deviation": None,
        "p_value": None,
        "p_value_low": None,
        "top_features": [],
        # The layer turned this decision into a verify ("Ek dogrulama istendi").
        "escalated": False,
        # Computed and recorded, not acted on ("Golge modu -- karar etkilenmedi").
        "shadow": False,
        # Synthetic demo data took part in the decision: the profile is a
        # seeded synthetic customer or the session was simulated
        # (decision_audit.is_synthetic; "Sentetik demo verisi").
        "synthetic": False,
    }
    if not PROFILE_ENABLED:
        return block
    row = (
        await db.execute(
            select(*_AUDIT_BLOCK_COLUMNS)
            .where(DecisionAudit.session_id == session_id)
            .where(DecisionAudit.profile_state.is_not(None))
            .order_by(DecisionAudit.decided_at.desc(), DecisionAudit.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        block["state"] = profiles.STATE_NO_PROFILE
        return block
    audit = _columns_as_dict(_AUDIT_BLOCK_COLUMNS, row)
    block.update(
        state=audit["profile_state"],
        modality=audit["modality"],
        reference_n=int(audit["reference_n"] or 0),
        probation_n=int(audit["probation_n"] or 0),
        deviation=None if audit["deviation"] is None else round(audit["deviation"], 3),
        p_value=audit["p_value"],
        p_value_low=audit["p_value_low"],
        top_features=[
            {"feature": item["feature"], "z": item["z"]}
            for item in (audit["top_features"] or [])
            if isinstance(item, dict) and "feature" in item and "z" in item
        ],
        # Either way the layer asked for step-up; the state says which.
        escalated=audit["reason"] in ("profile_deviation", "profile_rate_limited"),
        shadow=bool(audit["shadow"]),
        synthetic=bool(audit["is_synthetic"]),
    )
    return block


_REVIEW_AUDIT_COLUMNS = (
    DecisionAudit.decided_at,
    DecisionAudit.merchant_id,
    DecisionAudit.action,
    DecisionAudit.reason,
    DecisionAudit.public_reason,
    DecisionAudit.risk_score,
    DecisionAudit.profile_state,
    DecisionAudit.deviation,
    DecisionAudit.p_value,
    DecisionAudit.p_value_low,
    DecisionAudit.top_features,
    DecisionAudit.modality,
    DecisionAudit.reference_n,
    DecisionAudit.probation_n,
    DecisionAudit.feature_schema_version,
    DecisionAudit.shadow,
    DecisionAudit.amount_band,
    DecisionAudit.new_beneficiary,
    DecisionAudit.candidate_vec,
    DecisionAudit.profile_id,
)

_REVIEW_VECTOR_COLUMNS = (
    CustomerProfileVector.session_id,
    CustomerProfileVector.vec,
    CustomerProfileVector.created_at,
    CustomerProfileVector.modality,
    CustomerProfileVector.probation,
    CustomerProfileVector.outcome,
)


@app.get("/api/profile/review/{session_id}")
async def profile_review(
    session_id: str,
    x_review_operator: Annotated[str | None, Header()] = None,
    x_review_key: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
):
    """The human-review surface for a contested decision (GDPR Art. 22(3),
    KVKK 11(1)(g)): what the layer compared, against what, and what it said.

    Behind a per-operator credential (PROFILE_REVIEW_KEYS), never the
    dashboard key. Every authenticated request -- a 404 included -- writes a
    profile_access_audit row, and nothing is returned unless that row
    committed: an unrecorded read of special-category data is the thing the
    table exists to rule out.

    404 only when neither a session row nor a decision audit row exists. The
    session row is deleted after ROW_RETENTION_HOURS and the audit rows are
    kept DECISION_AUDIT_RETENTION_DAYS precisely so a complaint made days
    later can still be answered; requiring the session row would limit review
    to the first 24 hours.
    """
    operator_id = require_review_operator(x_review_operator, x_review_key)
    # A path id longer than the column cannot name anything; it is still an
    # authenticated access, and is recorded as one.
    known_id = session_id if len(session_id) <= 64 else None
    found: dict = {}

    async def read():
        session = await db.get(Session, session_id) if known_id else None
        audits = []
        if known_id:
            audits = [
                _columns_as_dict(_REVIEW_AUDIT_COLUMNS, r)
                for r in (
                    await db.execute(
                        select(*_REVIEW_AUDIT_COLUMNS)
                        .where(DecisionAudit.session_id == session_id)
                        .order_by(DecisionAudit.decided_at.asc(), DecisionAudit.id.asc())
                    )
                ).all()
            ]
        found["exists"] = session is not None or bool(audits)
        profile_id = getattr(session, "profile_id", None) if session is not None else None
        if profile_id is None:
            profile_id = next((a["profile_id"] for a in reversed(audits) if a["profile_id"]), None)
        found["profile_id"] = profile_id
        if not found["exists"]:
            return None

        candidate, candidate_source, modality = None, None, None
        if session is not None:
            flushes = [
                _columns_as_dict(_PROFILE_FLUSH_COLUMNS, r)
                for r in (
                    await db.execute(
                        select(*_PROFILE_FLUSH_COLUMNS)
                        .where(BehaviorData.session_id == session_id)
                        .order_by(BehaviorData.created_at.desc())
                        .limit(SPRT_MAX_FLUSHES)
                    )
                ).all()
            ]
            if flushes:
                modality = profiles.session_modality([f["client_signals"] for f in flushes])
                built = profiles.session_vector(flushes)
                if built is not None:
                    candidate, candidate_source = built["vec"], "flushes"
        references, probation_vectors = [], []
        if profile_id is not None:
            stored = [
                _columns_as_dict(_REVIEW_VECTOR_COLUMNS, r)
                for r in (
                    await db.execute(
                        select(*_REVIEW_VECTOR_COLUMNS)
                        .where(CustomerProfileVector.profile_id == profile_id)
                        .where(CustomerProfileVector.feature_schema_version == profiles.FEATURE_SCHEMA_VERSION)
                        .order_by(CustomerProfileVector.created_at.desc())
                    )
                ).all()
            ]
            own = next((v for v in stored if v["session_id"] == session_id), None)
            if candidate is None and own is not None:
                # The flushes are gone (24h); the learned vector is not.
                candidate, candidate_source, modality = own["vec"], "stored", own["modality"]
            if modality is None:
                modality = next((a["modality"] for a in reversed(audits) if a["modality"]), None)
            same_modality = [
                {k: v for k, v in vector.items() if k != "session_id"}
                for vector in stored
                if vector["session_id"] != session_id and vector["modality"] == modality
            ]
            # The references the decision compared against, and separately the
            # probation vectors it stored but did not use: a probation vector
            # is evidence for this review, not part of the statistic, until it
            # is settled or healed (profiles.PROFILE_PROBATION_MAX).
            references = [v for v in same_modality if profiles.is_reference(v)][: profiles.PROFILE_BUFFER_MAX]
            probation_vectors = [v for v in same_modality if v["probation"]]

        if candidate is None:
            # Neither flushes nor a learned vector: the case this surface
            # exists for -- challenged, did not pass, complains days later.
            # The deviating decision stored what it compared.
            audited = next((a for a in reversed(audits) if a["candidate_vec"]), None)
            if audited is not None:
                candidate, candidate_source = audited["candidate_vec"], "decision_audit"
                modality = modality or audited["modality"]

        reference_vecs = [r["vec"] for r in references]
        stats = profiles.feature_stats(reference_vecs)
        participating = profiles.participating_features(candidate, stats) if candidate is not None else []
        return {
            "session_id": session_id,
            "modality": modality,
            "candidate": {"source": candidate_source, "vec": candidate},
            "reference_n": len(references),
            "references": references,
            "probation_n": len(probation_vectors),
            "probation_vectors": probation_vectors,
            "feature_stats": {
                name: {"centre": stat.centre, "scale": stat.scale, "n_obs": stat.n_obs}
                for name, stat in stats.items()
            },
            "participating_features": participating,
            # The calibration set for this candidate against the references
            # stored NOW: each reference scored with itself left out, against
            # the other references plus this session (full conformal,
            # profiles.calibration_deviations). The key keeps the spec's name.
            # It is the set the decision ranked against only while the buffer
            # has not changed since -- later learns, evictions, heals,
            # settlements and disputes all change it, and the audit row keeps
            # the decision's own numbers, not its reference ids. "recomputed"
            # says whether the two still agree.
            "leave_one_out_deviations": (
                profiles.calibration_deviations(candidate, reference_vecs) if participating else []
            ),
            "recomputed": _review_recomputation(audits, candidate, candidate_source, reference_vecs, modality),
            # The pseudonym stays out of the body: the access audit row keeps
            # it, under the operator who asked.
            "decisions": [{k: v for k, v in a.items() if k != "profile_id"} for a in audits],
        }

    async def work():
        body = await read()
        db.add(
            ProfileAccessAudit(
                operator_id=operator_id,
                endpoint="profile.review",
                session_id=known_id,
                profile_id=found.get("profile_id"),
            )
        )
        return body

    body = await _profile_transaction(db, "Profil inceleme erisim kaydi", work)
    if body is None:
        raise HTTPException(status_code=404, detail="Oturum bulunamadi")
    return body


def _review_recomputation(
    audits: list[dict], candidate: dict | None, candidate_source: str | None, references: list[dict], modality
) -> dict | None:
    """The newest audited comparison of this session, recomputed against the
    references stored now -- and whether it still comes out the same.

    The review is not a replay of the decision. The audit row stores the
    decision's deviation, p-values and top features (and, for a deviating
    decision, the compared vector), but not the reference set, which keeps
    changing after the decision. So the reviewer gets the audited numbers
    (in "decisions") beside a recomputation (here), and matches_decision says
    whether they agree. False means the buffer moved on, not that either
    number is wrong; the audited ones are what the decision acted on. None
    when this session was never compared, or nothing is left to compare."""
    audited = next((a for a in reversed(audits) if a["deviation"] is not None), None)
    if audited is None:
        return None
    vec, source = candidate, candidate_source
    if audited["candidate_vec"]:
        # The vector this very decision compared, not today's flushes.
        vec, source = audited["candidate_vec"], "decision_audit"
    if vec is None:
        return None
    verdict = profiles.evaluate_profile(vec, references, modality=modality)
    return {
        "decided_at": audited["decided_at"],
        "candidate_source": source,
        "state": verdict.state,
        "reference_n": verdict.reference_n,
        "deviation": verdict.deviation,
        "p_value": verdict.p_value,
        "p_value_low": verdict.p_value_low,
        "matches_decision": (
            verdict.state == profiles.STATE_EVALUATED
            and verdict.reference_n == audited["reference_n"]
            and verdict.deviation == audited["deviation"]
            and verdict.p_value == audited["p_value"]
            and verdict.p_value_low == audited["p_value_low"]
        ),
    }


def _window_evidence(measured_mask: int | None) -> dict:
    """What the decision gate would make of one stored window, for the SOC
    panel.

    `observed` is _observed_a_generator itself, not a re-derivation: the panel
    counts these over the newest SPRT_MAX_FLUSHES rows to show the same
    "n of MIN_FLUSHES_FOR_DECISION observed" that holds a decision at verify in
    _decide_on_evidence. A NULL mask therefore reads as observed, exactly as the
    gate reads it, and `measured_features` -- how many of the twelve features
    the window measured -- is null for it: the column cannot say."""
    return {
        "observed": _observed_a_generator(measured_mask),
        "measured_features": None if measured_mask is None else int(measured_mask).bit_count(),
    }


_LAST_DECISION_COLUMNS = (
    DecisionAudit.action,
    # The INTERNAL reason. The scored client is told public_reason instead
    # (PUBLIC_REASONS); the SOC panel is where the real one is meant to be read.
    DecisionAudit.reason,
    DecisionAudit.public_reason,
    DecisionAudit.decided_at,
    DecisionAudit.risk_score,
)


async def _last_decision(db: AsyncSession, session_id: str) -> dict | None:
    """The newest recorded decision for this session, or None.

    The band beside the score is the PUBLISHED ladder entry, not what the server
    did: a session in the block band can still be answered verify (too few
    observed windows, the conformal guard), and the panel could not show that.
    This reads it back from decision_audit -- any row, not only the ones with a
    profile opinion that _profile_block reads.

    A read and nothing else: persisting a "last decision" on the session would
    put a write on the decision path, which T31 pins at the sequential-test
    read alone with the layer off. The cost is that only what the audit already
    records can be shown --
    nothing while the layer is off (the table is the layer's, and like
    _profile_block this reads none of it then), and with it on, no plain allow
    that named no customer (_learn_and_audit returns before writing). The
    dashboard says so rather than implying no decision was made.

    Never the merchant, the profile id or the compared vector: those stay
    behind the per-operator review credential (T34)."""
    if not PROFILE_ENABLED:
        return None
    row = (
        await db.execute(
            select(*_LAST_DECISION_COLUMNS)
            .where(DecisionAudit.session_id == session_id)
            .order_by(DecisionAudit.decided_at.desc(), DecisionAudit.id.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else _columns_as_dict(_LAST_DECISION_COLUMNS, row)


@app.get("/api/score/{session_id}", dependencies=[Depends(require_dashboard_key)])
async def get_score(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Oturum bulunamadı")

    # Newest HISTORY_LIMIT rows, then flipped back to chronological order for
    # the chart. Selecting ascending without a limit re-sent the session's
    # entire history on every 3s dashboard poll.
    result = await db.execute(
        select(BehaviorData)
        .where(BehaviorData.session_id == session_id)
        .order_by(BehaviorData.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = list(reversed(result.scalars().all()))

    return {
        "session_id": session.id,
        "risk_score": session.risk_score,
        "label": session.label,
        "confidence": session.confidence,
        "shap_explanation": session.shap_explanation,
        "response_time_ms": session.response_time_ms,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        # A session driven by demo_seed.py --simulate ("Sentetik demo verisi").
        "is_synthetic": bool(getattr(session, "is_synthetic", False)),
        "profile": await _profile_block(db, session_id),
        # Always present, null when nothing was recorded; see _last_decision.
        "last_decision": await _last_decision(db, session_id),
        "history": [
            {
                "timestamp": row.created_at,
                "risk_score": row.risk_score,
                **{name: getattr(row, name) for name in FEATURE_NAMES},
                # Recorded, not scored -- see ClientSignals.
                "client_signals": row.client_signals or {},
                # getattr: the test suites' stub rows predate the column.
                **_window_evidence(getattr(row, "measured_mask", None)),
            }
            for row in history
        ],
    }


@app.get("/api/sessions", dependencies=[Depends(require_dashboard_key)])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session).order_by(Session.last_seen_at.desc()).limit(SESSIONS_PAGE_LIMIT)
    )
    sessions = result.scalars().all()
    return [
        {
            "session_id": s.id,
            "risk_score": s.risk_score,
            "label": s.label,
            "confidence": s.confidence,
            "response_time_ms": s.response_time_ms,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            # The SOC list badges it and leaves it out of every metric card.
            "is_synthetic": bool(getattr(s, "is_synthetic", False)),
        }
        for s in sessions
    ]


@app.get("/api/health")
async def health():
    model_loaded = True
    try:
        scorer.get_bundle()
    except FileNotFoundError:
        model_loaded = False

    return {
        "status": "sağlıklı" if model_loaded else "model yüklenmedi",
        "model_loaded": model_loaded,
        "timestamp": utcnow().isoformat(),
    }
