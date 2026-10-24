#!/usr/bin/env python3
"""
Physics Trainer — an endless adaptive physics quiz powered by an LLM.

Every question is generated at a difficulty calibrated to your current skill
estimate (Elo-style). Answer correctly and the target difficulty creeps up;
answer wrong and it drops back slightly, so the quiz settles just above your
comfort zone. Questions you miss come back a few rounds later as variants.

Standard library only — no pip install required.

Quick start (no API key needed, uses a built-in generator):
    python3 trainer.py --provider mock

With a free API key (Groq has a free tier, no credit card):
    export GROQ_API_KEY=...
    python3 trainer.py

See README.md for details.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sqlite3
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

DEFAULT_DB = str(Path.home() / ".physics-trainer.db")

CORE_TOPICS = [
    "Heat and Gases",
    "Force and Motion",
    "Wave Motion",
    "Electricity and Magnetism",
    "Radioactivity and Nuclear Energy",
]
ELECTIVE_TOPICS = [
    "Atomic World (Elective 2)",
    "Energy and Use of Energy (Elective 3)",
]
ALL_TOPICS = CORE_TOPICS + ELECTIVE_TOPICS

# Topic outlines follow the HKDSE Physics Curriculum and Assessment Guide
# (fine-tuned curriculum). These outlines are the generation boundary: the
# question writer is explicitly instructed not to go beyond them.
SYLLABUS = {
    "Heat and Gases": [
        "temperature, heat and internal energy; thermometers; heat capacity and specific heat capacity",
        "energy transfer by conduction, convection and radiation; emission and absorption of radiation",
        "change of state; latent heat; evaporation and its cooling effect",
        "gas pressure; Boyle's law; pressure-temperature and volume-temperature relationships; general gas law pV=nRT",
        "kinetic theory of gases; assumptions of the ideal-gas model; molecular explanation of gas pressure and temperature",
    ],
    "Force and Motion": [
        "position, distance, displacement, scalars and vectors, speed, velocity, acceleration, linear motion and motion graphs",
        "Newton's laws of motion; mass and weight; addition and resolution of forces; moment of a force",
        "projectile motion and independence of horizontal and vertical motion",
        "work, kinetic and potential energy, conservation of energy, and power",
        "linear momentum, conservation of momentum, and elastic and inelastic collisions",
        "uniform circular motion; angular velocity, centripetal acceleration and centripetal force",
        "Newton's law of gravitation; gravitational field strength; satellites in circular orbits",
    ],
    "Wave Motion": [
        "wave motion; transverse and longitudinal waves; waveform, phase, amplitude, period, frequency, wavelength and wave speed",
        "reflection and refraction of waves; diffraction; superposition and interference; coherent sources; stationary transverse waves",
        "electromagnetic spectrum; reflection and refraction of light; total internal reflection",
        "image formation by a single converging or diverging thin lens; real and virtual images",
        "wave nature of light; Young's double-slit interference and transmission diffraction grating",
        "sound as a longitudinal wave; audible range and ultrasound; pitch, loudness, quality, decibel levels and noise protection",
    ],
    "Electricity and Magnetism": [
        "electric charges, electric field, electric potential energy, potential difference, and electrostatic applications",
        "electric current, electrical energy and e.m.f.; resistance; simple circuits; electrical power; domestic electricity",
        "magnetic fields and forces; magnetic effect of electric current; force on a current-carrying conductor; Hall probe",
        "electromagnetic induction; magnetic flux; Faraday's law and Lenz's law; generators; eddy currents; alternating current; transformers and power transmission",
    ],
    "Radioactivity and Nuclear Energy": [
        "X-rays as ionising electromagnetic radiation; production in a broad sense and uses",
        "origin, nature and properties of alpha, beta and gamma radiation; penetration, ionisation, fields and cloud-chamber tracks",
        "random radioactive decay; background radiation; activity, half-life, decay graphs and N=N0 exp(-kt); carbon dating",
        "detecting radiation with photographic film and a Geiger-Muller counter; radiation dose, hazards and safety",
        "atomic structure, isotopes, radioactive transmutation, mass-energy relationship, nuclear fission and fusion",
    ],
    "Atomic World (Elective 2)": [
        "Rutherford's atomic model; alpha-scattering evidence and limitations of the model",
        "photoelectric effect; evidence for light quanta; Einstein's photoelectric equation",
        "line spectra; Bohr's model of the hydrogen atom; transitions and interpretation of hydrogen line spectra; limitations of the model",
        "wave-particle duality of light and matter; de Broglie wavelength",
        "physical properties at the nanoscale; seeing at the nanoscale; applications and recent developments in nanotechnology",
    ],
    "Energy and Use of Energy (Elective 3)": [
        "energy-consuming appliances at home; electrical energy use and power ratings",
        "lighting technologies and their energy use; cooking without a flame; moving heat around",
        "energy efficiency labelling scheme and comparing appliance efficiency",
        "building materials and design for energy efficiency; electric vehicles",
        "renewable and non-renewable energy sources; solar, wind, hydroelectric and nuclear power",
        "environmental impacts of energy consumption and choices of energy source",
    ],
}

TOPIC_ALIASES = {
    "heat": "Heat and Gases", "gases": "Heat and Gases", "heat and gases": "Heat and Gases",
    "mechanics": "Force and Motion", "force": "Force and Motion", "motion": "Force and Motion",
    "force and motion": "Force and Motion",
    "waves": "Wave Motion", "wave": "Wave Motion", "wave motion": "Wave Motion",
    "em": "Electricity and Magnetism", "electricity": "Electricity and Magnetism",
    "magnetism": "Electricity and Magnetism", "electricity and magnetism": "Electricity and Magnetism",
    "radioactivity": "Radioactivity and Nuclear Energy", "nuclear": "Radioactivity and Nuclear Energy",
    "radioactivity and nuclear energy": "Radioactivity and Nuclear Energy",
    "atomic": "Atomic World (Elective 2)", "atomic world": "Atomic World (Elective 2)",
    "elective 2": "Atomic World (Elective 2)", "elective2": "Atomic World (Elective 2)",
    "e2": "Atomic World (Elective 2)",
    "energy": "Energy and Use of Energy (Elective 3)", "energy use": "Energy and Use of Energy (Elective 3)",
    "elective 3": "Energy and Use of Energy (Elective 3)", "elective3": "Energy and Use of Energy (Elective 3)",
    "e3": "Energy and Use of Energy (Elective 3)",
}

# Items the HKDSE Physics C&A Guide explicitly says are NOT required, or that
# belong to a different section. A general "stay in syllabus" instruction is not
# reliable on its own, so these are passed to the generator as hard exclusions.
SYLLABUS_EXCLUSIONS = {
    "Heat and Gases": [
        "thermodynamic processes and cycles (isothermal, isobaric, adiabatic)",
        "the first law of thermodynamics (ΔU = Q + W)",
        "Avogadro's law",
        "derivations of kinetic-theory equations and calculations using the Boltzmann constant",
        "critical temperature",
    ],
    "Force and Motion": [
        "rotational dynamics (torque–angular acceleration, moments of inertia, angular momentum) beyond the moment of a force",
        "calculus-based derivations and calculus kinematics",
        "general relativity and orbital-energy derivations beyond circular-orbit speed",
        "three-dimensional vector problems",
    ],
    "Wave Motion": [
        "derivations of the Young's double-slit and diffraction-grating equations",
        "path or phase difference beyond simple in-phase and anti-phase cases",
        "stationary longitudinal sound waves and measuring sound speed with stationary waves",
        "compound lens systems (telescopes, microscopes) and eye defects",
        "problems combining interference with diffraction",
    ],
    "Electricity and Magnetism": [
        "Maxwell's equations and calculus-based field derivations",
        "three-phase power systems",
        "detailed semiconductor device physics",
    ],
    "Radioactivity and Nuclear Energy": [
        "X-ray spectra (continuous or characteristic) and the detailed production mechanism of X-rays",
        "calculating the minimum X-ray wavelength (λ_min = hc/eV) or relating tube voltage to X-ray wavelength",
        "quantitative attenuation of radiation through matter",
        "converting measured count rate (cps) to absolute activity (Bq) or to absorbed dose using detector calibration",
        "binding energy per nucleon and mass-defect calculations (these belong to Elective 3, not this core section)",
        "detailed nuclear reactor engineering",
    ],
    "Atomic World (Elective 2)": [
        "the Schrödinger equation, wavefunctions, quantum numbers and atomic orbitals",
        "detailed band theory and semiconductor device physics",
        "detailed nanofabrication processes",
    ],
    "Energy and Use of Energy (Elective 3)": [
        "detailed HVAC/building-services engineering and full building energy modelling",
        "financial payback, ROI or investment analysis",
        "detailed nuclear reactor engineering beyond the roles of moderator, coolant and control rods",
        "life-cycle environmental impact assessment beyond qualitative discussion",
    ],
}


def syllabus_exclusions(topic: str) -> str:
    items = SYLLABUS_EXCLUSIONS.get(topic)
    return "; ".join(items) if items else "none"

# name: (base_url, api-key env var, default model)
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "deepseek-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-oss-120b:free"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "llama3.3-70b"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-small-latest"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1"),
    "mock": (None, None, "mock"),
}

KEY_HELP = {
    "deepseek": "https://platform.deepseek.com/api_keys",
    "groq": "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "cerebras": "https://cloud.cerebras.ai",
    "mistral": "https://console.mistral.ai",
    "gemini": "https://aistudio.google.com/apikey",
}

JSON_MODE_PROVIDERS = {"deepseek", "groq", "gemini", "openai", "cerebras", "mistral", "ollama"}

# --- adaptive difficulty tuning -------------------------------------------
START_RATING = 1000.0     # Elo-style skill estimate
K_MAX, K_MIN, K_RAMP = 80.0, 48.0, 80.0   # update speed, fast at first
WRONG_DAMPING = 0.35      # a wrong answer lowers the target only gently
TARGET_P = 0.72           # aim for ~72% success probability
TARGET_OFFSET = 400.0 * math.log10((1.0 - TARGET_P) / TARGET_P)  # ≈ -164 Elo
JITTER = 70.0             # random spread on the target difficulty (Elo points)
LEVEL_BASE, LEVEL_STEP = 500.0, 160.0     # seven practice bands, not grade predictions
MAX_LEVEL = 7
REASK_GAP = 8             # missed questions return after ~8 more questions
LONG_QUESTION_CHARS = 240 # verify these only after the user answers
STREAK_GAIN_FACTOR = 2.0  # solo Elo gain doubles per consecutive correct answer
STREAK_GAIN_CAP = 4.0     # ...until this cap
# Correct-answer gain multiplier by the player's current band. Climbing gets
# slower at higher bands, like a climbing-speed decay in an arcade ladder.
BAND_GAIN_FACTORS = (1.00, 0.95, 0.90, 0.80, 0.70, 0.60, 0.50)

# ---------------------------------------------------------------------------
# tiny terminal helpers
# ---------------------------------------------------------------------------

def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def bold(s: str) -> str: return _c("1", s)
def dim(s: str) -> str: return _c("2", s)
def green(s: str) -> str: return _c("32", s)
def red(s: str) -> str: return _c("31", s)
def yellow(s: str) -> str: return _c("33", s)


def warn(msg: str) -> None:
    print(yellow("! " + msg))


def rewrap(text: str, width: int = 74, indent: str = "") -> str:
    lines: list[str] = []
    for para in str(text).splitlines():
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=width) or [""])
    return "\n".join(indent + ln if ln else "" for ln in lines)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# answer parsing / comparison
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_number(s) -> float | None:
    """Pull the first number out of a string like '9.8 m/s^2' or '3.0e5'."""
    if s is None:
        return None
    t = str(s).strip()
    t = t.replace("×10^", "e").replace("x10^", "e").replace("·10^", "e")
    t = t.replace(",", "")
    m = _NUM_RE.search(t)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def normalize(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def answers_match(a, b) -> bool:
    """Lenient comparison: numeric within 2%, otherwise normalized text."""
    na, nb = parse_number(a), parse_number(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= max(0.02, 0.02 * abs(nb))
    sa, sb = normalize(a), normalize(b)
    if not sa or not sb:
        return False
    return sa == sb or sa in sb or sb in sa


def _numbers_in(text) -> list[float]:
    t = str(text).replace(" ", "").replace(",", "")
    t = t.replace("×10^", "e").replace("x10^", "e").replace("·10^", "e")
    t = t.replace("*10^", "e")
    out = []
    for match in _NUM_RE.findall(t):
        try:
            out.append(float(match))
        except ValueError:
            pass
    return out


def verification_match(part: dict, solver_answer: str) -> bool:
    """Compare a generated part with the independent solver's answer.

    The solver may include working, so numeric answers match if any number in
    the reply equals the expected value. Text parts are checked through the
    numbers they contain; purely conceptual text is accepted as long as the
    solver returned something (text equivalence is not reliable).
    """
    expected = str(part.get("answer", ""))
    if part.get("answer_format") == "numeric":
        target = parse_number(expected)
        if target is None:
            return False
        tolerance = max(0.02, 0.02 * abs(target))
        return any(abs(value - target) <= tolerance
                   for value in _numbers_in(solver_answer))

    expected_numbers = _numbers_in(expected)
    if expected_numbers:
        solver_numbers = _numbers_in(solver_answer)
        for target in expected_numbers:
            tolerance = max(0.02, 0.02 * abs(target))
            if not any(abs(value - target) <= tolerance
                       for value in solver_numbers):
                return False
        return True

    return bool(normalize(solver_answer))


# ---------------------------------------------------------------------------
# Elo-style difficulty model
# ---------------------------------------------------------------------------

def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def elo_of_level(level: int) -> float:
    return LEVEL_BASE + LEVEL_STEP * (level - 1)


def level_of_elo(d: float) -> int:
    return int(clamp(round((d - LEVEL_BASE) / LEVEL_STEP) + 1, 1, MAX_LEVEL))


def target_difficulty(rating: float, rng: random.Random) -> float:
    """Difficulty (Elo points) that gives ~TARGET_P chance of a correct answer."""
    return rating + TARGET_OFFSET + rng.uniform(-JITTER, JITTER)


def user_level(rating: float) -> int:
    """The difficulty level of the questions the user is currently being served."""
    return level_of_elo(rating + TARGET_OFFSET)


def band_gain_multiplier(rating: float) -> float:
    """Return the correct-answer gain multiplier for the player's current band.

    The multiplier is based on the player's band before the answer, not the
    question's band. That keeps hard questions attractive while making each
    successive band slower to climb.
    """
    return BAND_GAIN_FACTORS[user_level(rating) - 1]


def streak_gain_multiplier(streak: int) -> float:
    """Solo-only multiplier for a correct-answer Elo gain.

    0 correct in a row -> 1x, then 2x, 4x, capped at 4x. The current streak is
    read before the answer is recorded, so the first correct answer is 1x.
    """
    exponent = max(0, min(int(streak), 8))
    return min(STREAK_GAIN_CAP, STREAK_GAIN_FACTOR ** exponent)


def updated_rating(rating: float, level: int, correct: bool, attempts: int,
                   streak: int = 0, streak_boost: bool = False):
    """Returns (new_rating, expected_success_probability).

    The correct-answer gain is reduced at higher player bands. With
    ``streak_boost`` (solo mode), it also grows exponentially with the current
    correct-answer streak. Wrong answers still apply the normal gentle penalty
    and reset the streak.
    """
    k = K_MIN + (K_MAX - K_MIN) * max(0.0, 1.0 - attempts / K_RAMP)
    p = 1.0 / (1.0 + 10 ** ((elo_of_level(level) - rating) / 400.0))
    if correct:
        delta = k * (1.0 - p) * band_gain_multiplier(rating)
        if streak_boost:
            delta *= streak_gain_multiplier(streak)
    else:
        delta = -k * p * WRONG_DAMPING
    return rating + delta, p


def level_descriptor(level: int) -> str:
    return [
        "key idea or direct substitution",
        "single-step application",
        "routine syllabus application",
        "two-step application",
        "multi-step HKDSE-style problem",
        "demanding combination of syllabus ideas",
        "challenging HKDSE-style synthesis",
    ][int(clamp(level, 1, MAX_LEVEL)) - 1]


def dse_grade_label(level: int) -> str:
    """Reference label for the seven practice bands; not a predicted grade."""
    return ["1", "2", "3", "4", "5", "5*", "5**"][int(clamp(level, 1, MAX_LEVEL)) - 1]


def rubric_line(level: int) -> str:
    return [
        "identify a key idea or substitute into one familiar relationship",
        "apply one syllabus concept in a direct, single step",
        "solve a routine application of one syllabus concept",
        "choose a method and combine about two steps",
        "solve a multi-step HKDSE-style application",
        "combine syllabus ideas with careful interpretation and reasoning",
        "solve a challenging HKDSE-style synthesis problem, strictly within the stated syllabus",
    ][int(clamp(level, 1, MAX_LEVEL)) - 1]


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic         TEXT NOT NULL,
    subtopic      TEXT NOT NULL DEFAULT '',
    level         INTEGER NOT NULL,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    answer_format TEXT NOT NULL DEFAULT 'short_text',
    unit          TEXT,
    hint          TEXT,
    solution      TEXT,
    parts_json    TEXT,
    source        TEXT DEFAULT 'generated',
    next_ask_at   INTEGER,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id   INTEGER NOT NULL REFERENCES questions(id),
    correct       INTEGER NOT NULL,
    user_answer   TEXT,
    rating_before REAL,
    rating_after  REAL,
    created_at    TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(questions)")}
        if "subtopic" not in columns:
            self.db.execute("ALTER TABLE questions ADD COLUMN subtopic TEXT NOT NULL DEFAULT ''")
        if "parts_json" not in columns:
            self.db.execute("ALTER TABLE questions ADD COLUMN parts_json TEXT")
        self.db.commit()

    # --- key/value state --------------------------------------------------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get(key)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self.get(key)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value) -> None:
        self.db.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.db.commit()

    def get_json(self, key: str, default=None):
        value = self.get(key)
        if value is None:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    # --- ratings -----------------------------------------------------------
    def rating(self, topic: str) -> float:
        return self.get_float(f"rating:{topic}", START_RATING)

    def set_rating(self, topic: str, value: float) -> None:
        self.set(f"rating:{topic}", value)

    # --- questions -----------------------------------------------------------
    def save_question(self, q: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO questions(topic, subtopic, level, question, answer, answer_format, "
            "unit, hint, solution, parts_json, source, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (q["topic"], q.get("subtopic", ""), q["level"], q["question"], q["answer"],
             q.get("answer_format", "short_text"), q.get("unit"),
             q.get("hint"), q.get("solution"),
             json.dumps(q.get("parts") or [], ensure_ascii=False),
             q.get("source", "generated"), now_iso()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def delete_question(self, qid: int) -> None:
        self.db.execute("DELETE FROM attempts WHERE question_id = ?", (qid,))
        self.db.execute("DELETE FROM questions WHERE id = ?", (qid,))
        self.db.commit()

    def due_reask(self, upcoming_index: int, topics: list[str]):
        marks = ",".join("?" * len(topics))
        return self.db.execute(
            f"SELECT * FROM questions WHERE next_ask_at IS NOT NULL "
            f"AND next_ask_at <= ? AND topic IN ({marks}) "
            f"ORDER BY next_ask_at LIMIT 1",
            (upcoming_index, *topics),
        ).fetchone()

    def set_reask(self, qid: int, at_index: int) -> None:
        self.db.execute("UPDATE questions SET next_ask_at = ? WHERE id = ?", (at_index, qid))
        self.db.commit()

    def clear_reask(self, qid: int) -> None:
        self.db.execute("UPDATE questions SET next_ask_at = NULL WHERE id = ?", (qid,))
        self.db.commit()

    def bump_asked(self) -> int:
        n = self.get_int("asked") + 1
        self.set("asked", n)
        return n

    # --- attempts -----------------------------------------------------------
    def record_attempt(self, qid: int, correct: bool, user_answer: str,
                       before: float, after: float) -> None:
        self.db.execute(
            "INSERT INTO attempts(question_id, correct, user_answer, rating_before, "
            "rating_after, created_at) VALUES(?,?,?,?,?,?)",
            (qid, int(correct), user_answer, before, after, now_iso()),
        )
        self.db.execute(
            "INSERT INTO state(key, value) VALUES('attempts', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
        )
        self.db.commit()

    def topic_stats(self) -> dict[str, tuple[int, int]]:
        rows = self.db.execute(
            "SELECT q.topic AS topic, COUNT(*) AS asked, SUM(a.correct) AS correct "
            "FROM attempts a JOIN questions q ON q.id = a.question_id "
            "GROUP BY q.topic"
        ).fetchall()
        return {r["topic"]: (r["asked"], int(r["correct"] or 0)) for r in rows}

    def count_questions(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM questions").fetchone()[0])


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible chat completions, stdlib only)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    pass


class LLM:
    def __init__(self, provider: str, model: str, api_key: str, base_url: str,
                 timeout: int = 180, thinking: bool | None = None):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.thinking = thinking
        self.use_json_mode = provider in JSON_MODE_PROVIDERS

    def chat(self, system: str, user: str, temperature: float = 0.7,
             max_tokens: int = 3000) -> str:
        if self.provider == "deepseek" and self.thinking:
            # reasoning tokens count against max_tokens; leave room for the answer
            max_tokens = max(max_tokens, 8192)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.provider == "deepseek" and self.thinking is not None:
            payload["thinking"] = {
                "type": "enabled" if self.thinking else "disabled"
            }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Avoid urllib's generic Python-urllib signature on providers that
            # apply Cloudflare client-signature checks; identify this client
            # honestly instead of impersonating a browser.
            "User-Agent": "HKDSE-Physics-Trainer/1.0 (Python urllib)",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"

        delay = 5
        last_err = None
        for attempt in range(5):
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode())
                return body["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                err_body = e.read().decode(errors="replace")
                if e.code == 429:
                    wait = delay
                    try:
                        wait = int(float(e.headers.get("retry-after") or delay))
                    except (TypeError, ValueError):
                        pass
                    wait = int(clamp(wait, 1, 90))
                    warn(f"rate limited — waiting {wait}s (attempt {attempt + 1}/5)")
                    time.sleep(wait)
                    delay = int(min(delay * 2, 90))
                    last_err = f"429: {err_body[:200]}"
                    continue
                if e.code == 400 and self.use_json_mode:
                    # some models/endpoints reject response_format; retry plain
                    self.use_json_mode = False
                    payload.pop("response_format", None)
                    continue
                if e.code == 400 and "thinking" in payload:
                    # older/alternate endpoints may not accept thinking controls
                    payload.pop("thinking", None)
                    continue
                raise LLMError(f"HTTP {e.code} from {self.provider}: {err_body[:300]}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                warn(f"network error ({e}) — retrying in {delay}s")
                time.sleep(delay)
                delay = int(min(delay * 2, 90))
                last_err = str(e)
                continue
        raise LLMError(f"giving up after retries: {last_err}")


# ---------------------------------------------------------------------------
# question generation
# ---------------------------------------------------------------------------

GEN_SYSTEM = (
    "You are an expert HKDSE Physics examiner. Write clear, self-contained questions "
    "that stay strictly inside the supplied Hong Kong DSE Physics curriculum outline. "
    "Do not add related but out-of-syllabus material; the supplied exclusions are "
    "absolute and must not appear in the question, hint or solution. Reply with JSON only."
)

GEN_USER_TMPL = """Write ONE HKDSE Physics quiz problem. It may have one part, or two parts labelled (a) and (b).

HKDSE syllabus section: {topic}
Allowed syllabus content: {subtopic}
Explicitly excluded by the HKDSE syllabus (do not require, mention or teach these): {exclusions}
Difficulty: practice band {level} of 7 (reference label HKDSE {grade_label}-style; not a prediction of the student's grade) — {rubric}
{extra}
Requirements:
- Test only the allowed syllabus content above; do not draw on other physics topics.
- The exclusions are absolute: even if they are related to the subtopic, do not ask about them. Choose an in-syllabus aspect instead.
- Keep the maths and concepts within HKDSE Physics and its assumed mathematics.
- Diffraction-grating notation: write d sin(theta) = n lambda and use n (an integer) for the diffraction order; do not use m or another symbol for the order.
- Use an exam-like question style: a mix of concise calculations, explanation, interpretation and applications.
- Fully self-contained: no figures, no diagrams, nothing to look up.
- If there are two parts, write both in the question text, labelled (a) and (b), and give each part its own entry in "parts".
- Each part must have exactly one unambiguous correct answer.
- Use realistic SI values. Do not make every final answer a whole number: in a fair share of questions, use an irrational quantity (pi, a square root, or a trigonometric ratio) or values that do not cancel neatly, and round only the final answer.
- Give each final numeric answer to 3 significant figures, including trailing zeros where appropriate (for example 18.0 or 0.250). Do not round intermediate values.
- If a part's answer is a number, its "answer" is just the decimal number (no unit) and "unit" holds the unit. Do not put a surd, fraction, pi expression, or working in "answer"; the solution may show the exact form before rounding.
- For a short-text part, give a concise final answer (one short phrase or sentence), not working.
- "solution": a concise step-by-step derivation covering every part (2-6 steps).
- "hint": one sentence that nudges without giving the answers away.
- Never mention the difficulty level in the question text.
- Use one part for a single-part question, with label "".

Reply with JSON only, exactly in this shape:
{{"topic": "{topic}", "subtopic": "{subtopic}", "level": {level}, "question": "...", "parts": [{{"label": "(a)", "answer": "...", "answer_format": "numeric", "unit": "..."}}, {{"label": "(b)", "answer": "...", "answer_format": "short_text", "unit": null}}], "hint": "...", "solution": "..."}}
Use answer_format "numeric" for a numeric answer, otherwise "short_text" (then unit is null). Use at most two parts."""

VARIANT_TMPL = """The student got this problem wrong earlier, so this one is a fresh variant that tests the same concept at the same difficulty:
Earlier problem: {question}
Its answer was: {answer} {unit}
Create a NEW problem with a different scenario and different numbers.
"""

SOLVE_SYSTEM = (
    "You are a meticulous HKDSE Physics solver and syllabus checker. Solve every "
    "part of the problem and reply with JSON only: "
    '{"answers": [{"label": "(a)", "answer": "<final answer only>"}], '
    '"working": "...", "confidence": 0.0, "off_syllabus": false, '
    '"off_syllabus_reason": ""}. The answer values must be final answers only '
    "(a number with unit, or a short phrase), not working. For numeric answers, "
    "evaluate irrational quantities and give a decimal to at least 3 significant "
    "figures; do not leave the answer as a surd, fraction, or pi expression."
)

SOLVE_USER_TMPL = """HKDSE syllabus section: {topic}
Allowed syllabus content: {subtopic}
Excluded from the HKDSE syllabus: {exclusions}

Problem:
{question}

Solve it. Give one entry in "answers" for each labelled part, in order (if there is only one part, return one entry). Put working in "working" and only the final value in each "answer". Set off_syllabus to true if a proper solution requires any excluded item or content outside the allowed syllabus, and give a short reason."""


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM reply."""
    candidates: list[str] = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        candidates.append(m.group(1))
    for start in [text.find("{"), text.rfind("{")]:
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in model reply")


def _coerce_part(raw_part: dict, index: int, single: bool) -> dict | None:
    answer = raw_part.get("answer")
    if answer is None or str(answer).strip() == "":
        return None
    answer = str(answer).strip()
    fmt = str(raw_part.get("answer_format") or "").strip().lower()
    if fmt not in ("numeric", "short_text"):
        fmt = "numeric" if parse_number(answer) is not None else "short_text"
    unit = raw_part.get("unit")
    unit = str(unit).strip() if unit not in (None, "") else None
    if unit and unit.lower() in ("null", "none", "n/a", "-"):
        unit = None

    label = ""
    if not single:
        label = str(raw_part.get("label") or f"({chr(97 + index)})").strip()
        if label.lower() in ("a", "(a)"):
            label = "(a)"
        elif label.lower() in ("b", "(b)"):
            label = "(b)"
        else:
            label = f"({chr(97 + index)})"

    return {
        "label": label,
        "answer": answer,
        "answer_format": fmt,
        "unit": unit,
    }


def coerce_question(raw: dict, topic: str, subtopic: str, level: int) -> dict | None:
    question = str(raw.get("question") or "").strip()
    if not question:
        return None

    raw_parts = raw.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raw_parts = [raw]
    if len(raw_parts) > 2:
        return None

    single = len(raw_parts) == 1
    parts = []
    for index, raw_part in enumerate(raw_parts):
        if not isinstance(raw_part, dict):
            return None
        part = _coerce_part(raw_part, index, single)
        if part is None:
            return None
        parts.append(part)

    first = parts[0]
    return {
        "topic": topic,
        "subtopic": subtopic,
        "level": level,
        "question": question,
        "answer": first["answer"],
        "answer_format": first["answer_format"],
        "unit": first["unit"],
        "parts": parts,
        "hint": str(raw.get("hint") or "").strip(),
        "solution": str(raw.get("solution") or "").strip(),
    }


def question_parts(q: dict) -> list[dict]:
    parts = q.get("parts")
    if parts:
        return parts
    return [{
        "label": "",
        "answer": q["answer"],
        "answer_format": q.get("answer_format", "short_text"),
        "unit": q.get("unit"),
    }]


def part_answer_string(part: dict) -> str:
    return part["answer"] + (f" {part['unit']}" if part.get("unit") else "")


def solver_check(llm: LLM, q: dict):
    text = llm.chat(SOLVE_SYSTEM, SOLVE_USER_TMPL.format(
        topic=q["topic"], subtopic=q.get("subtopic", ""),
        exclusions=syllabus_exclusions(q["topic"]),
        question=q["question"]),
        temperature=0.1, max_tokens=3000)
    data = extract_json(text)

    raw_answers = data.get("answers")
    answers: list[str] = []
    if isinstance(raw_answers, list):
        for item in raw_answers:
            value = item.get("answer") if isinstance(item, dict) else item
            value = str(value or "").strip()
            if not value:
                raise ValueError("solver returned an empty answer")
            answers.append(value)
    else:
        value = str(data.get("answer") or "").strip()
        if not value:
            raise ValueError("solver returned no answer")
        answers = [value]

    return answers, bool(data.get("off_syllabus"))


def verify_question(llm: LLM, q: dict) -> tuple[bool, bool]:
    """Independently check only the numeric/calculation parts.

    Typed explanations are not verified: wording varies and the model's
    explanation should not decide whether a question is usable.
    """
    parts = question_parts(q)
    numeric = [(index, part) for index, part in enumerate(parts)
               if part.get("answer_format") == "numeric"]
    if not numeric:
        return True, False

    solver_answers, off_syllabus = solver_check(llm, q)
    if off_syllabus:
        return False, True

    if len(solver_answers) == len(parts):
        pairs = [(solver_answers[index], part) for index, part in numeric]
    elif len(solver_answers) == len(numeric):
        pairs = [(solver_answers[position], part)
                 for position, (_index, part) in enumerate(numeric)]
    else:
        # The solver returned a different shape; accept a numeric part if any
        # of its answers contains a matching value.
        return all(
            any(verification_match(part, answer) for answer in solver_answers)
            for _index, part in numeric
        ), False

    return all(
        verification_match(part, answer) for answer, part in pairs
    ), False


def is_long_question(q: dict) -> bool:
    return (len(q.get("question", "")) >= LONG_QUESTION_CHARS
            or len(question_parts(q)) > 1)


def generate_question(llm: LLM, topic: str, subtopic: str, level: int, verify: bool,
                      variant_of: dict | None = None, max_attempts: int = 3,
                      style_note: str = ""):
    """Returns (question_dict, warning_or_None)."""
    best = None
    best_warn = None
    for _ in range(max_attempts):
        extra_parts = []
        if variant_of:
            answer_desc = " | ".join(
                part_answer_string(p) for p in question_parts(variant_of)
            )
            extra_parts.append(VARIANT_TMPL.format(
                question=variant_of["question"],
                answer=answer_desc,
                unit="",
            ))
        if style_note:
            extra_parts.append(style_note)
        extra = "\n".join(extra_parts)
        prompt = GEN_USER_TMPL.format(topic=topic, subtopic=subtopic, level=level,
                                      grade_label=dse_grade_label(level),
                                      rubric=rubric_line(level),
                                      exclusions=syllabus_exclusions(topic),
                                      extra=extra)
        raw_text = llm.chat(GEN_SYSTEM, prompt, temperature=0.8)
        try:
            raw = extract_json(raw_text)
        except ValueError:
            continue
        q = coerce_question(raw, topic, subtopic, level)
        if not q:
            continue
        if not verify:
            return q, None, False
        if is_long_question(q):
            return q, None, True
        try:
            matches, off_syllabus = verify_question(llm, q)
        except (LLMError, ValueError):
            return q, "verification pass did not complete — answer not cross-checked", False
        if off_syllabus:
            best = q
            best_warn = ("this question was flagged as possibly outside the HKDSE syllabus "
                         "and could not be verified")
            continue
        if matches:
            return q, None, False
        best = q
        best_warn = "this question could not be independently verified"
    if best:
        return best, best_warn, False
    raise LLMError("the model did not produce a usable question")


# ---------------------------------------------------------------------------
# built-in mock generator (offline testing, no API key)
# ---------------------------------------------------------------------------

def _mock_kinematics(rng, level):
    d, t = rng.uniform(40, 600), rng.uniform(2, 40)
    v = d / t
    return dict(topic="Force and Motion", level=level,
                question=f"A cyclist rides {d:.0f} m in {t:.1f} s at a constant speed. What is her speed?",
                answer=f"{v:.3g}", answer_format="numeric", unit="m/s",
                hint="Constant speed: v = distance / time.",
                solution=f"v = d / t = {d:.0f} m / {t:.1f} s = {v:.3g} m/s.")


def _mock_freefall(rng, level):
    h = rng.uniform(5, 200)
    v = math.sqrt(2 * 9.81 * h)
    return dict(topic="Force and Motion", level=level,
                question=f"A stone is dropped from rest from a height of {h:.1f} m. "
                         f"With what speed does it hit the ground? (g = 9.81 m/s²)",
                answer=f"{v:.3g}", answer_format="numeric", unit="m/s",
                hint="Use v² = 2gh.",
                solution=f"v = √(2gh) = √(2 · 9.81 · {h:.1f}) = {v:.3g} m/s.")


def _mock_kinetic(rng, level):
    m, v = rng.uniform(0.5, 1500), rng.uniform(2, 40)
    e = 0.5 * m * v * v
    return dict(topic="Force and Motion", level=level,
                question=f"A {m:.1f} kg object moves at {v:.1f} m/s. What is its kinetic energy?",
                answer=f"{e:.3g}", answer_format="numeric", unit="J",
                hint="KE = ½mv².",
                solution=f"KE = ½mv² = 0.5 · {m:.1f} · {v:.1f}² = {e:.3g} J.")


def _mock_projectile(rng, level):
    v, th = rng.uniform(10, 60), rng.uniform(20, 70)
    r = v * v * math.sin(2 * math.radians(th)) / 9.81
    return dict(topic="Force and Motion", level=level,
                question=f"A ball is launched at {v:.1f} m/s at {th:.0f}° above the horizontal "
                         f"over level ground. How far away does it land? (g = 9.81 m/s²)",
                answer=f"{r:.3g}", answer_format="numeric", unit="m",
                hint="Range on level ground: R = v² sin(2θ) / g.",
                solution=f"R = v² sin(2θ)/g = {v:.1f}² · sin(2·{th:.0f}°)/9.81 = {r:.3g} m.")


def _mock_gas(rng, level):
    n, T, vol = rng.uniform(0.5, 5), rng.uniform(250, 450), rng.uniform(0.01, 0.5)
    p = n * 8.314 * T / vol
    return dict(topic="Heat and Gases", level=level,
                question=f"{n:.2f} mol of an ideal gas occupies {vol:.3f} m³ at {T:.0f} K. "
                         f"What is the pressure? (R = 8.314 J/(mol·K))",
                answer=f"{p:.3g}", answer_format="numeric", unit="Pa",
                hint="Ideal gas law: PV = nRT.",
                solution=f"P = nRT/V = {n:.2f} · 8.314 · {T:.0f}/{vol:.3f} = {p:.3g} Pa.")


def _mock_heat(rng, level):
    m, dT = rng.uniform(0.1, 5), rng.uniform(5, 80)
    q = m * 4186 * dT
    return dict(topic="Heat and Gases", level=level,
                question=f"How much heat is needed to raise {m:.2f} kg of water by {dT:.0f} °C? "
                         f"(c_water = 4186 J/(kg·°C))",
                answer=f"{q:.3g}", answer_format="numeric", unit="J",
                hint="Q = mcΔT.",
                solution=f"Q = mcΔT = {m:.2f} · 4186 · {dT:.0f} = {q:.3g} J.")


def _mock_ohms(rng, level):
    r, i = rng.uniform(5, 500), rng.uniform(0.01, 5)
    u = i * r
    return dict(topic="Electricity and Magnetism", level=level,
                question=f"A resistor of {r:.0f} Ω carries a current of {i:.2f} A. "
                         f"What is the voltage across it?",
                answer=f"{u:.3g}", answer_format="numeric", unit="V",
                hint="Ohm's law: V = IR.",
                solution=f"V = IR = {i:.2f} · {r:.0f} = {u:.3g} V.")


def _mock_coulomb(rng, level):
    q1, q2 = rng.uniform(1, 50), rng.uniform(1, 50)
    r = rng.uniform(0.05, 2)
    f = 8.99e9 * (q1 * 1e-6) * (q2 * 1e-6) / (r * r)
    return dict(topic="Electricity and Magnetism", level=level,
                question=f"Two point charges of {q1:.1f} µC and {q2:.1f} µC are {r:.2f} m apart. "
                         f"What is the magnitude of the force between them? (k = 8.99×10⁹ N·m²/C²)",
                answer=f"{f:.3g}", answer_format="numeric", unit="N",
                hint="Coulomb's law: F = kq₁q₂/r².",
                solution=f"F = kq₁q₂/r² = 8.99e9 · {q1:.1f}e-6 · {q2:.1f}e-6 / {r:.2f}² = {f:.3g} N.")


def _mock_wave(rng, level):
    f, lam = rng.uniform(20, 2000), rng.uniform(0.05, 10)
    v = f * lam
    return dict(topic="Wave Motion", level=level,
                question=f"A wave has a frequency of {f:.0f} Hz and a wavelength of {lam:.2f} m. "
                         f"What is its speed?",
                answer=f"{v:.3g}", answer_format="numeric", unit="m/s",
                hint="v = fλ.",
                solution=f"v = fλ = {f:.0f} · {lam:.2f} = {v:.3g} m/s.")


def _mock_photon(rng, level):
    lam = rng.uniform(200, 800)
    e = 6.626e-34 * 3.0e8 / (lam * 1e-9)
    return dict(topic="Atomic World (Elective 2)", level=level,
                question=f"What is the energy of a photon with wavelength {lam:.0f} nm? "
                         f"(h = 6.626×10⁻³⁴ J·s, c = 3.00×10⁸ m/s)",
                answer=f"{e:.3g}", answer_format="numeric", unit="J",
                hint="E = hc/λ (convert nm to m).",
                solution=f"E = hc/λ = 6.626e-34 · 3.00e8 / {lam:.0f}e-9 = {e:.3g} J.")


def _mock_half_life(rng, level):
    initial = rng.choice([800, 1200, 1600, 2400, 3200])
    half_life = rng.choice([4, 5, 6, 8, 10, 12])
    elapsed_halves = rng.randint(1, 5)
    elapsed = half_life * elapsed_halves
    remaining = initial / (2 ** elapsed_halves)
    return dict(topic="Radioactivity and Nuclear Energy", level=level,
                question=f"A radioactive sample initially contains {initial} undecayed nuclei. "
                         f"Its half-life is {half_life} days. How many undecayed nuclei remain after {elapsed} days?",
                answer=f"{remaining:.0f}", answer_format="numeric", unit="nuclei",
                hint="Count how many half-lives have elapsed, then halve the number that many times.",
                solution=f"{elapsed}/{half_life} = {elapsed_halves} half-lives; "
                         f"N = {initial}/2^{elapsed_halves} = {remaining:.0f} nuclei.")


def _mock_home_energy(rng, level):
    power_kw = rng.choice([0.6, 0.8, 1.0, 1.2, 1.5, 2.0])
    hours = rng.choice([1, 2, 3, 4, 5])
    tariff = rng.choice([1.2, 1.5, 1.8, 2.0])
    energy = power_kw * hours
    cost = energy * tariff
    return dict(topic="Energy and Use of Energy (Elective 3)", level=level,
                question=f"A {power_kw:.1f} kW appliance is used for {hours} hours. "
                         f"If electricity costs HK${tariff:.2f} per kWh, what is the cost of the electricity used?",
                answer=f"{cost:.3g}", answer_format="numeric", unit="HK dollars",
                hint="Energy used in kWh = power in kW × time in hours.",
                solution=f"Energy = {power_kw:.1f} × {hours} = {energy:.1f} kWh; "
                         f"cost = {energy:.1f} × HK${tariff:.2f} = HK${cost:.3g}.")


MOCK_FAMILIES = [
    ("Force and Motion", 1, 0, _mock_kinematics),
    ("Force and Motion", 2, 0, _mock_freefall),
    ("Force and Motion", 2, 3, _mock_kinetic),
    ("Force and Motion", 4, 2, _mock_projectile),
    ("Heat and Gases", 3, 3, _mock_gas),
    ("Heat and Gases", 3, 0, _mock_heat),
    ("Electricity and Magnetism", 1, 1, _mock_ohms),
    ("Electricity and Magnetism", 4, 0, _mock_coulomb),
    ("Wave Motion", 1, 0, _mock_wave),
    ("Atomic World (Elective 2)", 3, 1, _mock_photon),
    ("Radioactivity and Nuclear Energy", 2, 2, _mock_half_life),
    ("Energy and Use of Energy (Elective 3)", 2, 0, _mock_home_energy),
]


def mock_question(topic: str, subtopic: str, level: int, rng: random.Random) -> dict:
    all_families = [f for f in MOCK_FAMILIES if f[0] == topic] or MOCK_FAMILIES
    matching = [f for f in all_families if SYLLABUS[f[0]][f[2]] == subtopic]
    fams = matching or all_families
    closest_level = min(abs(f[1] - level) for f in fams)
    fams = [f for f in fams if abs(f[1] - level) == closest_level]
    family = rng.choice(fams)
    question = family[3](rng, level)
    question["subtopic"] = SYLLABUS[family[0]][family[2]]
    return question


# ---------------------------------------------------------------------------
# quiz flow
# ---------------------------------------------------------------------------

def pick_topic(store: Store, topics: list[str], rng: random.Random) -> str:
    if len(topics) == 1:
        return topics[0]
    if rng.random() < 0.5:
        return rng.choice(topics)
    return min(topics, key=store.rating)  # lean into weak areas


IRRATIONAL_STYLE_CHANCE = 0.40
IRRATIONAL_STYLE = (
    "For this particular question, deliberately use at least one irrational "
    "quantity or non-cancelling value (for example pi, a square root, or a "
    "trigonometric ratio) so the final answer is not a neat whole number. Carry "
    "the exact value through the working, round only the final numeric answer to "
    "3 significant figures, and put that decimal (not a surd or pi expression) in "
    "the answer field. Keep the data realistic and HKDSE-appropriate."
)


def next_question(llm: LLM | None, store: Store, topics: list[str],
                  rng: random.Random, verify: bool):
    """Generate (or revisit) the next question. Returns (qid, q, source_note, warning)."""
    restored = store.get_json("restore_question")
    if restored:
        store.set("restore_question", "")
        restored["deferred_verify"] = is_long_question(restored)
        qid = store.save_question(restored)
        store.bump_asked()
        return qid, restored, "restored after accidental discard", None

    asked_next = store.get_int("asked") + 1
    due = store.due_reask(asked_next, topics)
    variant_of = dict(due) if due else None
    source_note = "variant of one you missed" if due else None

    if due:
        topic = variant_of["topic"]
        subtopic = variant_of.get("subtopic") or rng.choice(SYLLABUS[topic])
        level = variant_of["level"]
    else:
        topic = pick_topic(store, topics, rng)
        subtopic = rng.choice(SYLLABUS[topic])
        level = level_of_elo(target_difficulty(store.rating(topic), rng))

    warn = None
    deferred = False
    if llm is None:
        q = mock_question(topic, subtopic, level, rng)
    else:
        style_note = IRRATIONAL_STYLE if rng.random() < IRRATIONAL_STYLE_CHANCE else ""
        try:
            q, warn, deferred = generate_question(
                llm, topic, subtopic, level, verify, variant_of,
                style_note=style_note,
            )
        except LLMError as e:
            if not variant_of:
                raise
            # fall back to re-asking the stored original
            parts = []
            if variant_of.get("parts_json"):
                try:
                    parts = json.loads(variant_of["parts_json"])
                except (TypeError, json.JSONDecodeError):
                    parts = []
            if not parts:
                parts = [{
                    "label": "",
                    "answer": variant_of["answer"],
                    "answer_format": variant_of["answer_format"],
                    "unit": variant_of["unit"],
                }]
            q = dict(topic=topic, subtopic=subtopic, level=level,
                     question=variant_of["question"], answer=variant_of["answer"],
                     answer_format=variant_of["answer_format"],
                     unit=variant_of["unit"], hint=variant_of["hint"],
                     parts=parts, solution=variant_of["solution"])
            deferred = is_long_question(q)
            source_note = "missed earlier — try again"
            warn = f"could not build a variant ({e}); showing the original"

    q["deferred_verify"] = bool(deferred)

    if due:
        store.clear_reask(due["id"])
    qid = store.save_question(q)
    store.bump_asked()
    return qid, q, source_note, warn


def show_question(q: dict, source_note: str | None, warning: str | None) -> None:
    print()
    print(dim("─" * 74))
    meta = (f"{q['topic']} · practice band {q['level']}/7 "
            f"(HKDSE {dse_grade_label(q['level'])}-style) · {level_descriptor(q['level'])}")
    if source_note:
        meta += f" · {source_note}"
    print(bold(meta))
    if q.get("subtopic"):
        print(dim("Syllabus focus: ") + q["subtopic"])
    print()
    print(rewrap(q["question"]))
    print()
    if warning:
        print(yellow("!  " + warning))
        print()


def answer_string(q: dict) -> str:
    return q["answer"] + (f" {q['unit']}" if q.get("unit") else "")


_TEXT_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "it", "its", "of", "to",
    "in", "on", "for", "and", "or", "as", "at", "by", "be", "been", "this",
    "that", "these", "those", "with", "from", "so", "because", "about",
    "approximately", "roughly", "thus", "hence", "therefore", "than", "then",
    "there", "their", "they", "will", "would", "can", "could", "should",
    "may", "might", "do", "does", "did", "very", "much", "many", "some",
    "any", "when", "while", "which", "where", "what", "how", "why", "if",
}


_NEGATIONS = {
    "no", "not", "never", "none", "without", "cannot", "cant", "nor",
    "isnt", "arent", "doesnt", "dont", "wont",
}

_SYNONYMS = {
    "loss": "lost", "lose": "lost", "loses": "lost", "losing": "lost",
    "lost": "lost",
    "surrounding": "surroundings", "surroundings": "surroundings",
    "heating": "heat", "heats": "heat", "heated": "heat",
    "needed": "need", "needs": "need",
    "rises": "increase", "rise": "increase", "rising": "increase",
    "rose": "increase", "raised": "increase", "raises": "increase",
    "increases": "increase", "increased": "increase", "increasing": "increase",
    "decreases": "decrease", "decreased": "decrease", "decreasing": "decrease",
}


def _canon_token(token: str) -> str:
    token = _SYNONYMS.get(token, token)
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            token = token[:-len(suffix)]
            break
    return token


def _text_tokens(text) -> set[str]:
    tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?(?:e[-+]?\d+)?", str(text).lower())
    return {token for token in tokens
            if len(token) > 1 and token not in _TEXT_STOPWORDS}


def _text_matches(user_text, expected_text) -> bool:
    user_norm, expected_norm = normalize(user_text), normalize(expected_text)
    if not user_norm or not expected_norm:
        return False
    if user_norm in expected_norm or expected_norm in user_norm:
        return True

    user_tokens = _text_tokens(user_text)
    expected_tokens = _text_tokens(expected_text)
    if not user_tokens or not expected_tokens:
        return False
    if bool(user_tokens & _NEGATIONS) != bool(expected_tokens & _NEGATIONS):
        return False

    user_canon = {_canon_token(token) for token in user_tokens}
    expected_canon = {_canon_token(token) for token in expected_tokens}
    if user_canon <= expected_canon or expected_canon <= user_canon:
        return True

    overlap = len(user_canon & expected_canon) / len(user_canon)
    return overlap >= 0.75


def _grade_one(answer_format: str, expected_text: str, user_text: str):
    if answer_format == "numeric":
        expected = parse_number(expected_text)
        got = parse_number(user_text)
        if expected is None or got is None:
            return False, "expected a numeric answer"
        tol = max(0.02, 0.02 * abs(expected))
        return abs(got - expected) <= tol, None
    return _text_matches(user_text, expected_text), None


def grade(q: dict, user_text: str):
    """Grade a single-answer question. Returns (correct, note)."""
    return _grade_one(q.get("answer_format"), q["answer"], user_text)


def grade_parts(q: dict, user_answers: list[str]):
    """Grade every part.

    If a question contains at least one numeric/calculation part, only those
    numeric parts are scored. Typed explanations are returned as self-check
    details and do not affect correctness. Pure text questions are still
    graded by text matching.
    """
    parts = question_parts(q)
    if len(user_answers) != len(parts):
        return False, f"expected {len(parts)} answer(s)", []

    has_numeric = any(part.get("answer_format") == "numeric" for part in parts)
    details = []
    all_correct = True
    for part, answer in zip(parts, user_answers):
        if has_numeric and part.get("answer_format") != "numeric":
            details.append({
                "label": part.get("label", ""),
                "correct": True,
                "self_check": True,
                "your_answer": answer,
                "expected": part_answer_string(part),
                "note": "self-check — not scored",
            })
            continue

        correct, note = _grade_one(
            part.get("answer_format"), part["answer"], answer
        )
        all_correct = all_correct and correct
        details.append({
            "label": part.get("label", ""),
            "correct": correct,
            "self_check": False,
            "your_answer": answer,
            "expected": part_answer_string(part),
            "note": note,
        })
    return all_correct, (None if all_correct else "one or more parts are incorrect"), details


def ask_user(q: dict, store: Store, topics: list[str]):
    """Returns (action, payload): answer | skip | bad | undo | quit.

    A two-part question returns a list of answers in part order.
    """
    parts = question_parts(q)
    if len(parts) > 1:
        answers: list[str] = []
        for part in parts:
            label = part.get("label") or "answer"
            while True:
                try:
                    raw = input(bold(f"{label} ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return "quit", None
                if not raw:
                    continue
                cmd = raw.lower()
                if cmd in ("quit", "exit", "q"):
                    return "quit", None
                if cmd == "hint":
                    print(dim("hint › ") + (q.get("hint") or "no hint for this one"))
                    continue
                if cmd == "skip":
                    return "skip", None
                if cmd == "stats":
                    print_stats(store, topics)
                    continue
                if cmd == "bad":
                    return "bad", None
                if cmd == "undo":
                    if store.get_json("last_bad_question"):
                        return "undo", None
                    print(dim("there is no recently discarded question to restore"))
                    continue
                answers.append(raw)
                break
        return "answer", answers

    while True:
        try:
            raw = input(bold("› ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit", None
        if not raw:
            continue
        cmd = raw.lower()
        if cmd in ("quit", "exit", "q"):
            return "quit", None
        if cmd == "hint":
            print(dim("hint › ") + (q.get("hint") or "no hint for this one"))
            continue
        if cmd == "skip":
            return "skip", None
        if cmd == "stats":
            print_stats(store, topics)
            continue
        if cmd == "bad":
            return "bad", None
        if cmd == "undo":
            if store.get_json("last_bad_question"):
                return "undo", None
            print(dim("there is no recently discarded question to restore"))
            continue
        return "answer", raw


def confirm_unverified(q: dict, user_answer, store: Store,
                       topics: list[str]) -> str:
    """Ask the user to judge their own answer when verification failed.

    The expected answer and the solver's answer are never shown here, so the
    question can still be graded by the user without leaking the answer.
    """
    print()
    print(yellow("This question could not be independently verified."))
    if isinstance(user_answer, (list, tuple)):
        for part, answer in zip(question_parts(q), user_answer):
            label = part.get("label") or "answer"
            print(dim(f"  {label} ") + str(answer))
    else:
        print(dim("  your answer: ") + str(user_answer))
    print(dim("  Do you believe your answer is correct?"))
    print(dim("  [continue] count it as correct · [bad] discard this question · [quit] exit"))
    while True:
        try:
            raw = input(bold("› ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if raw == "continue":
            return "continue"
        if raw == "bad":
            return "bad"
        if raw in ("quit", "exit", "q"):
            return "quit"
        if raw == "stats":
            print_stats(store, topics)
            continue
        print(dim("  type 'continue' to count it as correct, or 'bad' to discard it"))


def show_result(q: dict, user_answer, correct: bool, note: str | None,
                before: float, after: float, store: Store,
                part_details: list[dict] | None = None) -> None:
    print()
    if correct:
        print(green("✔ correct") +
              f"   rating {before:.0f} → {after:.0f}   "
              f"streak {store.get_int('streak')} (best {store.get_int('best_streak')})")
    else:
        print(red("✘ not quite") + f"   rating {before:.0f} → {after:.0f}")
        if part_details:
            for detail in part_details:
                if detail.get("self_check"):
                    label = (detail["label"] + " ") if detail.get("label") else ""
                    print(dim(f"  ○ {label}self-check — model answer: "
                              f"{detail['expected']}"))
                    print(dim(f"    your answer: {detail['your_answer']}"))
                    continue
                mark = green("✔") if detail["correct"] else red("✘")
                label = (detail["label"] + " ") if detail.get("label") else ""
                print(f"  {mark} {label}{detail['your_answer']}   "
                      f"(expected {detail['expected']})")
                if detail.get("note"):
                    print(dim("  note: ") + detail["note"])
        else:
            print(dim("  your answer: ") + str(user_answer))
            if note:
                print(dim("  note: ") + note)
        if len(question_parts(q)) == 1:
            print(dim("  (a variant of this will come back in a few questions)"))
    if part_details:
        for detail in part_details:
            label = (detail["label"] + " ") if detail.get("label") else ""
            print(f"  {label}answer: " + bold(detail["expected"]))
    else:
        print("  answer: " + bold(answer_string(q)))
    if q.get("solution"):
        print()
        print(dim("  solution:"))
        print(rewrap(q["solution"], indent="    "))


def print_stats(store: Store, topics: list[str]) -> None:
    stats = store.topic_stats()
    print()
    print(bold(f"  {'topic':<40}{'rating':>8}{'band':>7}{'asked':>7}{'acc':>6}"))
    total_a = total_c = 0
    for t in topics:
        r = store.rating(t)
        asked, correct = stats.get(t, (0, 0))
        total_a += asked
        total_c += correct
        acc = f"{correct / asked:.0%}" if asked else "—"
        print(f"  {t:<40}{r:>8.0f}{dse_grade_label(user_level(r)):>7}{asked:>7}{acc:>6}")
    if len(topics) > 1:
        overall = sum(store.rating(t) for t in topics) / len(topics)
        acc = f"{total_c / total_a:.0%}" if total_a else "—"
        print(f"  {'overall':<40}{overall:>8.0f}{dse_grade_label(user_level(overall)):>7}{total_a:>7}{acc:>6}")
    print(dim(f"  streak {store.get_int('streak')} · best {store.get_int('best_streak')} · "
              f"questions generated {store.count_questions()}"))
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolve_topics(raw: str) -> list[str]:
    if not raw or raw.strip().lower() in ("all", "*"):
        return list(ALL_TOPICS)
    out: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok == "core":
            out.extend(t for t in CORE_TOPICS if t not in out)
            continue
        if tok in ("electives", "selected electives"):
            out.extend(t for t in ELECTIVE_TOPICS if t not in out)
            continue
        canonical_names = {name.lower(): name for name in ALL_TOPICS}
        t = TOPIC_ALIASES.get(tok) or canonical_names.get(tok)
        if not t:
            matches = [x for x in ALL_TOPICS if x.lower().startswith(tok)]
            if len(matches) == 1:
                t = matches[0]
        if not t:
            sys.exit(f"unknown topic '{tok}'. options: {', '.join(ALL_TOPICS)}")
        if t not in out:
            out.append(t)
    return out or list(ALL_TOPICS)


def resolve_llm(args) -> LLM | None:
    if args.provider == "mock":
        return None
    base_url, key_env, default_model = PROVIDERS[args.provider]
    api_key = (args.api_key
               or os.environ.get("PHYSICS_TRAINER_API_KEY")
               or (os.environ.get(key_env) if key_env else None))
    base_url = args.base_url or os.environ.get("PHYSICS_TRAINER_BASE_URL") or base_url
    model = args.model or os.environ.get("PHYSICS_TRAINER_MODEL") or default_model
    if not api_key and args.provider != "ollama":
        hint = KEY_HELP.get(args.provider, "")
        sys.exit(f"No API key for '{args.provider}'. Set {key_env} or pass --api-key.\n"
                 f"Get a key: {hint}")
    thinking = getattr(args, "thinking", None)
    if args.provider == "deepseek" and thinking is None:
        thinking = False
    return LLM(args.provider, model, api_key or "", base_url, thinking=thinking)


def parse_args():
    p = argparse.ArgumentParser(
        description="Endless adaptive physics trainer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    default_provider = os.environ.get("PHYSICS_TRAINER_PROVIDER", "deepseek").lower()
    if default_provider not in PROVIDERS:
        default_provider = "deepseek"
    p.add_argument("--provider", choices=sorted(PROVIDERS), default=default_provider)
    p.add_argument("--model", help="override the provider's default model")
    p.add_argument("--api-key", help="override the API key")
    p.add_argument("--base-url", help="override the API base URL (OpenAI-compatible)")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite file for progress")
    p.add_argument("--topics", default="all",
                   help="comma-separated HKDSE sections: heat, motion, waves, electricity, "
                        "radioactivity, atomic/elective2, energy/elective3 — or 'all'")
    p.add_argument("--start-level", type=int, choices=range(1, MAX_LEVEL + 1), metavar="1-7",
                   help="initial practice band (1,2,3,4,5,6=5*,7=5**; fresh progress only)")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the second-model answer check (saves API calls, less safe)")
    p.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None,
                   help="enable model thinking/reasoning where supported (slower; "
                        "DeepSeek defaults to off)")
    p.add_argument("--reset", action="store_true", help="delete saved progress first")
    p.add_argument("--seed", type=int, default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    topics = resolve_topics(args.topics)

    db_path = os.path.expanduser(args.db)
    if args.reset and os.path.exists(db_path):
        os.remove(db_path)
    store = Store(db_path)

    if store.get_int("attempts") == 0 and args.start_level:
        for t in ALL_TOPICS:
            store.set_rating(t, elo_of_level(args.start_level) - TARGET_OFFSET)

    llm = resolve_llm(args)
    verify = (not args.no_verify) and llm is not None

    print()
    print(bold("Physics Trainer") + dim(" — endless adaptive physics questions"))
    if llm is None:
        print(dim("  generator: ") + "built-in mock (no API needed — offline demo)")
    else:
        print(dim("  generator: ") + f"{args.provider} · {llm.model}" +
              ("" if verify else " · verification off"))
    print(dim("  topics:    ") + ", ".join(topics))
    print(dim("  progress:  ") + db_path)
    print(dim("  during a question: ") + "answer · hint · skip · stats · bad · undo · quit")

    session_asked = session_correct = 0
    try:
        while True:
            qid, q, source_note, warning = next_question(
                llm, store, topics, rng, verify
            )
            show_question(q, source_note, warning)
            action, payload = ask_user(q, store, topics)

            if action == "quit":
                break
            if action == "bad":
                store.set("last_bad_question", json.dumps(q))
                store.delete_question(qid)
                print(dim("discarded — generating another one..."))
                continue
            if action == "undo":
                store.delete_question(qid)
                store.set("restore_question", json.dumps(store.get_json("last_bad_question")))
                store.set("last_bad_question", "")
                store.set("asked", max(0, store.get_int("asked") - 1))
                print(dim("restoring the last discarded question..."))
                continue
            if action == "skip":
                store.set_reask(qid, store.get_int("asked") + REASK_GAP)
                print(dim("skipped — it will come back later."))
                continue

            if q.get("deferred_verify") and verify and llm is not None:
                try:
                    deferred_ok, _ = verify_question(llm, q)
                except (LLMError, ValueError):
                    deferred_ok = False
                if not deferred_ok:
                    warning = "this question could not be independently verified"

            answers = payload if isinstance(payload, list) else [payload]
            correct, note, part_details = grade_parts(q, answers)
            if warning:
                decision = confirm_unverified(q, answers, store, topics)
                if decision == "quit":
                    break
                if decision == "bad":
                    store.set("last_bad_question", json.dumps(q))
                    store.delete_question(qid)
                    print(dim("discarded — generating another question..."))
                    continue
                correct, note = True, "counted as correct on your confirmation"
                part_details = [
                    {
                        "label": part.get("label", ""),
                        "correct": True,
                        "your_answer": answer,
                        "expected": part_answer_string(part),
                        "note": "",
                    }
                    for part, answer in zip(question_parts(q), answers)
                ]
            normalized = " | ".join(answers)

            before = store.rating(q["topic"])
            streak_before = store.get_int("streak")
            after, _ = updated_rating(
                before, q["level"], correct, store.get_int("attempts"),
                streak=streak_before, streak_boost=True,
            )
            store.set_rating(q["topic"], after)
            store.record_attempt(qid, correct, normalized, before, after)

            if correct:
                streak = store.get_int("streak") + 1
                store.set("streak", streak)
                store.set("best_streak", max(streak, store.get_int("best_streak")))
            else:
                store.set("streak", 0)
                store.set_reask(qid, store.get_int("asked") + REASK_GAP)

            session_asked += 1
            session_correct += int(correct)
            show_result(q, normalized, correct, note, before, after, store, part_details)

    except LLMError as e:
        print(red(f"\nAPI error: {e}"))
    except KeyboardInterrupt:
        print()

    print()
    if session_asked:
        print(dim(f"session: {session_asked} answered · {session_correct} correct "
                  f"({session_correct / session_asked:.0%})"))
    overall = sum(store.rating(t) for t in topics) / len(topics)
    band = user_level(overall)
    print(f"overall practice band {band}/7 (HKDSE {dse_grade_label(band)}-style question difficulty; "
          "not a predicted grade) · use 'stats' for the topic breakdown")
    print(dim("see you next time."))


if __name__ == "__main__":
    main()
