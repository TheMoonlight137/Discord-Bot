"""Non-web quiz session state shared by the Discord bot.

This is the old server-side session logic (pending question, grading,
deferred verification, SQLite persistence) with the HTTP/UI layer removed.
"""

from __future__ import annotations

import json
import os
import random
import threading
from pathlib import Path

import trainer as T


class NoQuestionError(Exception):
    """No pending question is available."""


CFG: dict = {}
SESSIONS: dict[str, "Session"] = {}
SESSIONS_LOCK = threading.Lock()


def load_env_file(path: str) -> None:
    p = Path(path).expanduser()
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


class Session:
    """One quiz state. Each session has its own SQLite database."""

    def __init__(self, sid: str, db_path: Path):
        self.sid = sid
        self.db_path = db_path
        self.lock = threading.Lock()
        self.rng = random.Random()
        self.pending: dict | None = None
        self.streak_boost = False
        self._init_db()
        self.streak_boost = self._load_streak_boost()
        self._restore_pending()

    def _init_db(self) -> None:
        store = T.Store(str(self.db_path))
        try:
            if store.get_int("attempts") == 0 and CFG["start_level"]:
                for topic in T.ALL_TOPICS:
                    store.set_rating(
                        topic,
                        T.elo_of_level(CFG["start_level"]) - T.TARGET_OFFSET,
                    )
        finally:
            store.db.close()

    def _store(self) -> T.Store:
        return T.Store(str(self.db_path))

    def set_topics(self, topics: list[str]) -> None:
        store = self._store()
        try:
            store.set("default_topics", json.dumps(list(topics)))
        finally:
            store.db.close()

    def _load_streak_boost(self) -> bool:
        store = self._store()
        try:
            return store.get("streak_boost") == "1"
        finally:
            store.db.close()

    def set_streak_boost(self, enabled: bool) -> None:
        """Enable/disable solo exponential Elo gains; persisted across restarts."""
        self.streak_boost = bool(enabled)
        store = self._store()
        try:
            store.set("streak_boost", "1" if enabled else "")
        finally:
            store.db.close()

    def stored_topics(self) -> list[str]:
        store = self._store()
        try:
            raw = store.get("default_topics")
        finally:
            store.db.close()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return [str(topic) for topic in parsed]
            except (TypeError, json.JSONDecodeError):
                pass
        return list(CFG["topics"])

    def _clear_pending(self, store: T.Store) -> None:
        for key in ("pending_qid", "pending_warning", "pending_source",
                    "pending_confirm", "pending_user_answer", "pending_deferred"):
            store.set(key, "")

    def _restore_pending(self) -> None:
        """Reload a question that was displayed before a restart."""
        store = self._store()
        try:
            qid = store.get_int("pending_qid", 0)
            if not qid:
                return
            row = store.db.execute(
                "SELECT * FROM questions WHERE id = ?", (qid,)
            ).fetchone()
            if row is None:
                self._clear_pending(store)
                return
            q = {
                "topic": row["topic"],
                "subtopic": row["subtopic"],
                "level": row["level"],
                "question": row["question"],
                "answer": row["answer"],
                "answer_format": row["answer_format"],
                "unit": row["unit"],
                "hint": row["hint"],
                "solution": row["solution"],
            }
            parts = []
            try:
                if row["parts_json"]:
                    parsed = json.loads(row["parts_json"])
                    if isinstance(parsed, list):
                        parts = parsed
            except (TypeError, json.JSONDecodeError):
                parts = []
            if not parts:
                parts = [{
                    "label": "",
                    "answer": row["answer"],
                    "answer_format": row["answer_format"],
                    "unit": row["unit"],
                }]
            q["parts"] = parts

            raw_user = store.get("pending_user_answer") or ""
            user_answer = None
            if raw_user:
                try:
                    parsed = json.loads(raw_user)
                    if isinstance(parsed, list):
                        user_answer = [str(x) for x in parsed]
                    else:
                        user_answer = [str(parsed)]
                except json.JSONDecodeError:
                    user_answer = [raw_user]

            needs_confirmation = store.get("pending_confirm") == "1"
            if needs_confirmation and not user_answer:
                self._clear_pending(store)
                return

            self.pending = {
                "qid": qid,
                "q": q,
                "warning": store.get("pending_warning") or None,
                "source_note": store.get("pending_source") or None,
                "needs_confirmation": needs_confirmation,
                "user_answer": user_answer,
                "deferred": store.get("pending_deferred") == "1",
            }
        finally:
            store.db.close()

    # --- question flow ----------------------------------------------------
    def next_question(self, topics: list[str] | None = None) -> dict:
        with self.lock:
            if self.pending is not None:
                return self._question_payload()
            if CFG["mock"]:
                llm = None
            elif CFG["llm"] is None:
                raise T.LLMError(CFG["llm_error"] or "no model configured")
            else:
                llm = CFG["llm"]
            scope = topics or self.stored_topics()
            store = self._store()
            try:
                qid, q, source_note, warning = T.next_question(
                    llm, store, scope, self.rng, CFG["verify"]
                )
                store.set("pending_qid", qid)
                store.set("pending_warning", warning or "")
                store.set("pending_source", source_note or "")
                store.set("pending_confirm", "")
                store.set("pending_user_answer", "")
                store.set("pending_deferred", "1" if q.get("deferred_verify") else "")
            finally:
                store.db.close()
            self.pending = {
                "qid": qid,
                "q": q,
                "warning": warning,
                "source_note": source_note,
                "needs_confirmation": False,
                "user_answer": None,
                "deferred": bool(q.get("deferred_verify")),
            }
            return self._question_payload()

    def _question_payload(self) -> dict:
        assert self.pending is not None
        q = self.pending["q"]
        return {
            "topic": q["topic"],
            "subtopic": q.get("subtopic", ""),
            "level": q["level"],
            "band": T.dse_grade_label(q["level"]),
            "descriptor": T.level_descriptor(q["level"]),
            "question": q["question"],
            "warning": self.pending["warning"],
            "source_note": self.pending["source_note"],
            "needs_confirmation": bool(self.pending.get("needs_confirmation")),
            "your_answer": self.pending.get("user_answer"),
            "your_answers": self.pending.get("user_answer"),
            "parts": [
                {"label": p.get("label", ""), "format": p.get("answer_format", "short_text")}
                for p in T.question_parts(q)
            ],
            "stats": self._stats_payload(),
        }

    def answer(self, answers: list[str]) -> dict:
        with self.lock:
            pending = self.pending
            if pending is None:
                raise NoQuestionError("there is no question waiting for an answer")
            if pending["needs_confirmation"]:
                raise ValueError("confirm your previous answer first")

            texts = [str(a).strip() for a in answers]
            if not texts or any(not t for t in texts):
                raise ValueError("answer cannot be empty")
            q = pending["q"]
            parts = T.question_parts(q)
            if len(texts) != len(parts):
                raise ValueError(f"expected {len(parts)} answer(s)")

            if pending.get("deferred") and CFG.get("verify") and CFG.get("llm") is not None:
                try:
                    deferred_ok, _ = T.verify_question(CFG["llm"], q)
                except (T.LLMError, ValueError):
                    deferred_ok = False
                pending["deferred"] = False
                if not deferred_ok:
                    pending["needs_confirmation"] = True
                    pending["user_answer"] = texts
                    store = self._store()
                    try:
                        store.set("pending_deferred", "")
                        store.set("pending_confirm", "1")
                        store.set("pending_user_answer", json.dumps(texts))
                    finally:
                        store.db.close()
                    return {
                        "needs_confirmation": True,
                        "your_answers": texts,
                        "parts": [{"label": p.get("label", "")} for p in parts],
                    }

            correct, note, part_details = T.grade_parts(q, texts)

            if pending["warning"]:
                # Never reveal the expected answer when the verifier failed.
                pending["needs_confirmation"] = True
                pending["user_answer"] = texts
                store = self._store()
                try:
                    store.set("pending_confirm", "1")
                    store.set("pending_user_answer", json.dumps(texts))
                finally:
                    store.db.close()
                return {
                    "needs_confirmation": True,
                    "your_answers": texts,
                    "parts": [{"label": p.get("label", "")} for p in parts],
                }

            return self._finish(correct, texts, note, part_details)

    def confirm(self, is_correct: bool) -> dict:
        with self.lock:
            pending = self.pending
            if pending is None or not pending.get("needs_confirmation"):
                raise ValueError("there is no answer waiting for confirmation")
            if is_correct:
                q = pending["q"]
                part_details = [
                    {
                        "label": part.get("label", ""),
                        "correct": True,
                        "your_answer": answer,
                        "expected": T.part_answer_string(part),
                        "note": "",
                    }
                    for part, answer in zip(T.question_parts(q), pending["user_answer"])
                ]
                return self._finish(
                    True, pending["user_answer"],
                    "counted as correct on your confirmation", part_details,
                )
            store = self._store()
            try:
                store.delete_question(pending["qid"])
                self._clear_pending(store)
            finally:
                store.db.close()
            self.pending = None
            return {"discarded": True, "stats": self._stats_payload()}

    def _finish(self, correct: bool, user_answers: list[str], note: str | None,
                part_details: list[dict] | None = None) -> dict:
        assert self.pending is not None
        q = self.pending["q"]
        topic = q["topic"]
        user_answer_str = " | ".join(user_answers)
        store = self._store()
        try:
            before = store.rating(topic)
            streak_before = store.get_int("streak")
            gain_multiplier = (
                T.streak_gain_multiplier(streak_before)
                if self.streak_boost and correct else 1.0
            )
            band_multiplier = T.band_gain_multiplier(before) if correct else 1.0
            after, _ = T.updated_rating(
                before, q["level"], correct, store.get_int("attempts"),
                streak=streak_before, streak_boost=self.streak_boost,
            )
            store.set_rating(topic, after)
            store.record_attempt(self.pending["qid"], correct, user_answer_str,
                                 before, after)

            if correct:
                streak = store.get_int("streak") + 1
                store.set("streak", streak)
                store.set("best_streak", max(streak, store.get_int("best_streak")))
            else:
                store.set("streak", 0)
                store.set_reask(self.pending["qid"], store.get_int("asked") + T.REASK_GAP)

            band = T.dse_grade_label(T.user_level(after))
            streak = store.get_int("streak")
            best = store.get_int("best_streak")
            self._clear_pending(store)
            stats = self._stats_payload(store)
        finally:
            store.db.close()

        self.pending = None
        return {
            "result": True,
            "correct": correct,
            "your_answer": user_answers[0] if len(user_answers) == 1 else user_answer_str,
            "your_answers": user_answers,
            "parts": part_details or [],
            "expected": (part_details[0]["expected"] if part_details
                         else T.answer_string(q)),
            "solution": q.get("solution") or "",
            "rating_before": round(before),
            "rating_after": round(after),
            "band": band,
            "user_level": T.user_level(after),
            "streak": streak,
            "best_streak": best,
            "streak_multiplier": round(gain_multiplier, 2),
            "band_multiplier": round(band_multiplier, 2),
            "note": note or "",
            "stats": stats,
        }

    def hint(self) -> dict:
        with self.lock:
            if self.pending is None:
                raise NoQuestionError("there is no active question")
            return {"hint": self.pending["q"].get("hint") or "no hint for this one"}

    def skip(self) -> dict:
        with self.lock:
            if self.pending is None:
                raise NoQuestionError("there is no active question")
            store = self._store()
            try:
                store.set_reask(
                    self.pending["qid"], store.get_int("asked") + T.REASK_GAP
                )
                self._clear_pending(store)
            finally:
                store.db.close()
            self.pending = None
            return {"skipped": True, "stats": self._stats_payload()}

    def discard(self) -> dict:
        with self.lock:
            if self.pending is None:
                raise NoQuestionError("there is no active question")
            store = self._store()
            try:
                store.delete_question(self.pending["qid"])
                self._clear_pending(store)
            finally:
                store.db.close()
            self.pending = None
            return {"discarded": True, "stats": self._stats_payload()}

    # --- stats -------------------------------------------------------------
    def _stats_payload(self, store: T.Store | None = None) -> dict:
        own = store is None
        if own:
            store = self._store()
        try:
            per_topic = store.topic_stats()
            topics = []
            total_asked = total_correct = 0
            for name in CFG["topics"]:
                rating = store.rating(name)
                asked, correct = per_topic.get(name, (0, 0))
                total_asked += asked
                total_correct += correct
                topics.append({
                    "topic": name,
                    "rating": round(rating),
                    "band": T.dse_grade_label(T.user_level(rating)),
                    "level": T.user_level(rating),
                    "asked": asked,
                    "correct": correct,
                })
            overall = sum(store.rating(t) for t in CFG["topics"]) / len(CFG["topics"])
            return {
                "topics": topics,
                "overall": {
                    "rating": round(overall),
                    "band": T.dse_grade_label(T.user_level(overall)),
                    "level": T.user_level(overall),
                    "asked": total_asked,
                    "correct": total_correct,
                },
                "streak": store.get_int("streak"),
                "best_streak": store.get_int("best_streak"),
                "questions": store.count_questions(),
            }
        finally:
            if own:
                store.db.close()


def get_session(sid: str) -> Session:
    with SESSIONS_LOCK:
        session = SESSIONS.get(sid)
        if session is None:
            path = Path(CFG["sessions_dir"]) / f"{sid}.db"
            session = Session(sid, path)
            SESSIONS[sid] = session
        return session
