"""HKDSE Physics Trainer for the Discord bot.

Solo:
    /physics        adaptive question, ephemeral, per-user rating
    /physics_stats  per-user stats

Party:
    /physics_party         public question; first correct answer wins and moves on
    /physics_party_stats   per-server/group rating + leaderboard

Storage:
    ~/.local/share/physics-trainer/discord/<user_id>.db      solo, per user
    ~/.local/share/physics-trainer/party/<guild|group_id>.db party, per server/group

No external Discord servers or invite links are used by this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import traceback
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

_HERE = Path(__file__).resolve().parent
TRAINER_DIR = _HERE if (_HERE / "trainer.py").exists() else Path.home() / "physics-trainer"
sys.path.insert(0, str(TRAINER_DIR))

import trainer as T          # noqa: E402
import quiz_session as core  # noqa: E402

ACCENT = discord.Color.from_rgb(0x5E, 0xEA, 0xD4)
MAX_FIELD = 1024

TOPIC_CHOICES = [
    app_commands.Choice(name="All core + electives", value="all"),
    app_commands.Choice(name="All core topics", value="core"),
    app_commands.Choice(name="All electives", value="electives"),
    app_commands.Choice(name="Heat and Gases", value="heat"),
    app_commands.Choice(name="Force and Motion", value="force"),
    app_commands.Choice(name="Wave Motion", value="waves"),
    app_commands.Choice(name="Electricity and Magnetism", value="electricity"),
    app_commands.Choice(name="Radioactivity and Nuclear Energy", value="radioactivity"),
    app_commands.Choice(name="Atomic World (Elective 2)", value="atomic"),
    app_commands.Choice(name="Energy and Use of Energy (Elective 3)", value="energy"),
]

PARTY_DIR = Path("~/.local/share/physics-trainer/party").expanduser()
PARTY_DIR.mkdir(parents=True, exist_ok=True)
PARTIES: dict[str, "Party"] = {}
PARTY_VIEWS: dict[int, "PartyView"] = {}
PARTY_LOCK = threading.Lock()

DUEL_DIR = Path("~/.local/share/physics-trainer/duels").expanduser()
DUEL_DIR.mkdir(parents=True, exist_ok=True)
DUELS: dict[str, "Duel"] = {}
DUEL_LOCK = threading.Lock()

_BOT: commands.Bot | None = None


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _configure() -> None:
    core.load_env_file("~/.config/physics-trainer.env")

    key = (os.environ.get("DEEPSEEK_API_KEY")
           or os.environ.get("PHYSICS_TRAINER_API_KEY"))
    base_url = os.environ.get("PHYSICS_TRAINER_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("PHYSICS_TRAINER_MODEL") or "deepseek-flash"

    llm = T.LLM("deepseek", model, key or "", base_url, thinking=False) if key else None

    sessions_dir = Path("~/.local/share/physics-trainer/discord").expanduser()
    sessions_dir.mkdir(parents=True, exist_ok=True)

    core.CFG.update({
        "llm": llm,
        "llm_error": (None if key else
                      "No DeepSeek API key. Set DEEPSEEK_API_KEY in "
                      "~/.config/physics-trainer.env."),
        "mock": False,
        "provider": "deepseek",
        "topics": T.resolve_topics(os.environ.get("PHYSICS_TRAINER_TOPICS", "all")),
        "verify": True,
        "start_level": None,
        "sessions_dir": sessions_dir,
        "password": "",
    })


def _session(user_id: int) -> core.Session:
    return core.get_session(str(user_id))


def _party_key(guild, channel) -> str:
    return f"guild_{guild.id}" if guild else f"group_{channel.id}"


def _party_permission_error(interaction: discord.Interaction) -> str | None:
    """Return a user-facing message if the bot cannot post a public party message."""
    if interaction.guild is None:
        return "Party mode needs a server channel. In DMs, use `/physics`."
    me = interaction.guild.me
    if me is None:
        return "Add the bot to this server before using `/physics_party`."
    perms = interaction.channel.permissions_for(me)
    checks = (
        ("View Channel", perms.view_channel),
        ("Send Messages", perms.send_messages),
        ("Embed Links", perms.embed_links),
        ("Read Message History", perms.read_message_history),
    )
    missing = [name for name, allowed in checks if not allowed]
    if missing:
        return ("I can't post the party question here. Missing: "
                + ", ".join(missing)
                + ". Give the bot those permissions in this channel, "
                  "or try another channel.")
    return None


def _get_party(key: str) -> "Party":
    with PARTY_LOCK:
        party = PARTIES.get(key)
        if party is None:
            party = Party(key)
            PARTIES[key] = party
        return party


# ---------------------------------------------------------------------------
# embeds
# ---------------------------------------------------------------------------

def _clip(text, limit: int = MAX_FIELD) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _add_long_field(embed: discord.Embed, name: str, text: str) -> None:
    text = str(text or "")
    if not text:
        return
    chunks = [text[i:i + 950] for i in range(0, len(text), 950)]
    for index, chunk in enumerate(chunks[:4]):
        label = name if len(chunks) == 1 else f"{name} ({index + 1}/{len(chunks)})"
        embed.add_field(name=label, value=chunk, inline=False)


def question_embed(payload: dict, party: bool = False) -> discord.Embed:
    title = (f"HKDSE Physics · Band {payload['level']}/7 "
             f"· HKDSE {payload['band']}-style")
    if party:
        title = "🎉 Party · " + title
    embed = discord.Embed(title=title,
                          description=_clip(payload["question"], 4000),
                          color=ACCENT)
    embed.add_field(name="Topic", value=_clip(payload["topic"], 100), inline=True)
    parts = payload.get("parts") or []
    if len(parts) > 1:
        embed.add_field(
            name="Answer both parts",
            value=" · ".join(part.get("label", "") for part in parts),
            inline=False,
        )
    if party:
        embed.add_field(
            name="How to play",
            value="Press **Answer** — the first correct answer wins and moves on.",
            inline=False,
        )
    if payload.get("warning"):
        embed.add_field(name="⚠ Verification", value=_clip(payload["warning"]),
                        inline=False)
    footer = payload.get("subtopic") or ""
    if payload.get("source_note"):
        footer = (footer + " · " if footer else "") + payload["source_note"]
    if footer:
        embed.set_footer(text=_clip(footer, 2048))
    return embed


def result_embed(result: dict) -> discord.Embed:
    correct = bool(result.get("correct"))
    embed = discord.Embed(
        title="✔ Correct" if correct else "✘ Not quite",
        color=discord.Color.green() if correct else discord.Color.red(),
    )
    for part in result.get("parts") or []:
        label = part.get("label") or "Answer"
        if part.get("self_check"):
            value = (f"Your answer: {part.get('your_answer', '—')}\n"
                     f"Model answer: {part.get('expected', '—')}\n"
                     f"Self-check — not scored.")
        else:
            value = (f"Your answer: {part.get('your_answer', '—')}\n"
                     f"Expected: {part.get('expected', '—')}")
        embed.add_field(name=_clip(label, 100), value=_clip(value), inline=False)
    if not result.get("parts") and result.get("note"):
        embed.add_field(name="Note", value=_clip(result["note"]), inline=False)
    if result.get("solution"):
        _add_long_field(embed, "Solution", result["solution"])
    multiplier = float(result.get("streak_multiplier") or 1.0)
    band_multiplier = float(result.get("band_multiplier") or 1.0)
    gain_notes = []
    if correct and band_multiplier < 1.0:
        gain_notes.append(f"×{band_multiplier:g} band gain")
    if correct and multiplier > 1.0:
        gain_notes.append(f"×{multiplier:g} streak gain")
    gain_note = (" · " + " · ".join(gain_notes)) if gain_notes else ""
    embed.set_footer(text=(
        f"Rating {result['rating_before']} → {result['rating_after']}{gain_note} · "
        f"band {result['band']} · streak {result['streak']} "
        f"(best {result['best_streak']})"
    ))
    return embed


def stats_embed(stats: dict) -> discord.Embed:
    overall = stats["overall"]

    def acc(row):
        if not row["asked"]:
            return "no answers yet"
        pct = round(100 * row["correct"] / row["asked"])
        return f"{row['correct']}/{row['asked']} ({pct}%)"

    lines = [f"**Overall** · rating {overall['rating']} · band {overall['band']} "
             f"· {acc(overall)}", ""]
    for row in stats["topics"]:
        lines.append(f"**{row['topic']}** · rating {row['rating']} "
                     f"· band {row['band']} · {acc(row)}")
    lines += ["", f"Streak {stats['streak']} · best {stats['best_streak']} "
                  f"· {stats['questions']} questions generated"]
    return discord.Embed(title="Your HKDSE Physics progress",
                         description="\n".join(lines), color=ACCENT)


def party_stats_embed(stats: dict, leaderboard: list[tuple]) -> discord.Embed:
    embed = stats_embed(stats)
    embed.title = "Party progress — this server/group"
    if leaderboard:
        lines = [f"<@{user_id}> — {correct}/{attempts}"
                 for user_id, attempts, correct in leaderboard]
        embed.add_field(name="Leaderboard", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Leaderboard", value="No attempts yet.", inline=False)
    return embed


def confirmation_embed(result: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Could not independently verify",
        description=("Your answers are below. The expected answers stay hidden "
                     "until you commit."),
        color=discord.Color.orange(),
    )
    parts = result.get("parts") or []
    for index, answer in enumerate(result.get("your_answers") or []):
        label = parts[index].get("label") if index < len(parts) else ""
        embed.add_field(name=label or f"Answer {index + 1}",
                        value=_clip(answer), inline=False)
    return embed


def error_embed(exc: Exception) -> discord.Embed:
    message = str(exc) or exc.__class__.__name__
    return discord.Embed(title="Physics trainer error",
                         description=_clip(message, 4000),
                         color=discord.Color.red())


def _answer_text_from_parts(parts: list[dict]) -> str:
    parts = [part for part in parts if part.get("expected") or part.get("answer")]
    if not parts:
        return "—"
    rendered = []
    for part in parts:
        label = (part.get("label") or "").strip()
        answer = part.get("expected") or T.part_answer_string(part)
        rendered.append(f"{label} {answer}".strip())
    return "; ".join(rendered)


# ---------------------------------------------------------------------------
# party state
# ---------------------------------------------------------------------------

class Party:
    """One party per guild/group. The rating is shared by that server/group."""

    def __init__(self, key: str):
        self.key = key
        self.lock = threading.Lock()
        self.session = core.Session(key, PARTY_DIR / f"{key}.db")
        self._ensure_tables()

    def _store(self) -> T.Store:
        return self.session._store()

    def _ensure_tables(self) -> None:
        store = self._store()
        try:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS party_attempts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id TEXT NOT NULL, correct INTEGER NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            store.db.commit()
        finally:
            store.db.close()

    # --- persistent metadata ------------------------------------------------
    def _set_state(self, key: str, value) -> None:
        store = self._store()
        try:
            store.set(key, value)
        finally:
            store.db.close()

    def _get_int(self, key: str) -> int:
        store = self._store()
        try:
            return store.get_int(key, 0)
        finally:
            store.db.close()

    def _get_state(self, key: str):
        store = self._store()
        try:
            return store.get(key)
        finally:
            store.db.close()

    def set_topics(self, topics: list[str]) -> None:
        self._set_state("party_topics", json.dumps(list(topics)))

    def topics(self) -> list[str]:
        raw = self._get_state("party_topics")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return [str(topic) for topic in parsed]
            except (TypeError, json.JSONDecodeError):
                pass
        return list(core.CFG["topics"])

    def set_channel(self, channel_id: int) -> None:
        self._set_state("party_channel_id", channel_id)

    def channel_id(self) -> int:
        return self._get_int("party_channel_id")

    def set_message(self, message_id: int) -> None:
        self._set_state("party_message_id", message_id)

    def message_id(self) -> int:
        return self._get_int("party_message_id")

    def set_starter(self, user_id: int) -> None:
        self._set_state("party_starter_id", user_id)

    def starter_id(self) -> int:
        return self._get_int("party_starter_id")

    # --- attempts -----------------------------------------------------------
    def record_attempt(self, user_id: int, correct: bool) -> None:
        store = self._store()
        try:
            store.db.execute(
                "INSERT INTO party_attempts(user_id, correct, created_at) "
                "VALUES(?,?,?)",
                (str(user_id), int(correct), T.now_iso()),
            )
            store.db.commit()
        finally:
            store.db.close()

    def leaderboard(self, limit: int = 10) -> list[tuple]:
        store = self._store()
        try:
            rows = store.db.execute(
                "SELECT user_id, COUNT(*) AS attempts, SUM(correct) AS correct "
                "FROM party_attempts GROUP BY user_id "
                "ORDER BY correct DESC, attempts ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [(row["user_id"], row["attempts"], int(row["correct"] or 0))
                    for row in rows]
        finally:
            store.db.close()

    # --- flow ---------------------------------------------------------------
    def submit(self, user_id: int, qid: int, answers: list[str]) -> dict:
        with self.lock:
            pending = self.session.pending
            if pending is None or pending["qid"] != qid:
                return {"closed": True}

            question = pending["q"]
            correct, note, details = T.grade_parts(question, answers)
            self.record_attempt(user_id, correct)

            if not correct:
                return {"wrong": True}

            result = self.session._finish(True, answers, note, details)
            return {"winner": result, "question": question}

    def reveal(self):
        """Close the question as unanswered (a gentle group rating drop)."""
        with self.lock:
            pending = self.session.pending
            if pending is None:
                return None
            question = pending["q"]
            self.session._finish(
                False, ["revealed"], "no one answered correctly", []
            )
            return question

    def advance(self) -> dict:
        with self.lock:
            llm = core.CFG.get("llm")
            verify = bool(core.CFG.get("verify")) and llm is not None
            for _ in range(3):
                payload = self.session.next_question(self.topics())
                if not verify:
                    return payload
                question = self.session.pending["q"]
                try:
                    ok, _off = T.verify_question(llm, question)
                except (T.LLMError, ValueError):
                    ok = False
                if ok:
                    return payload
                self.session.discard()
            raise T.LLMError("couldn't generate an independently verified question")


# ---------------------------------------------------------------------------
# 1v1 duels (DM text)
# ---------------------------------------------------------------------------

class Duel:
    """A private 1v1 between two users, played by replying in DMs."""

    def __init__(self, user_a: int, user_b: int):
        self.a, self.b = sorted((user_a, user_b))
        self.key = f"duel_{self.a}_{self.b}"
        self.lock = threading.Lock()
        self.session = core.Session(self.key, DUEL_DIR / f"{self.key}.db")
        self.attempted: set[int] = set()
        self.closed = True
        self.topics = list(core.CFG["topics"])
        self._ensure_table()

    def _ensure_table(self) -> None:
        store = self.session._store()
        try:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS duel_wins ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, winner_id TEXT NOT NULL, "
                "loser_id TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            store.db.commit()
        finally:
            store.db.close()

    def other(self, user_id: int) -> int:
        return self.b if user_id == self.a else self.a

    def start(self, topics: list[str] | None = None) -> dict:
        with self.lock:
            if not self.closed:
                return {}
            if topics:
                self.topics = list(topics)
            llm = core.CFG.get("llm")
            verify = bool(core.CFG.get("verify")) and llm is not None
            for _ in range(3):
                payload = self.session.next_question(self.topics)
                if not verify:
                    break
                question = self.session.pending["q"]
                try:
                    ok, _off = T.verify_question(llm, question)
                except (T.LLMError, ValueError):
                    ok = False
                if ok:
                    break
                self.session.discard()
            else:
                raise T.LLMError(
                    "couldn't generate an independently verified question"
                )
            self.attempted.clear()
            self.closed = False
            return payload

    def submit(self, user_id: int, text: str) -> dict:
        with self.lock:
            if self.closed:
                return {"closed": True}
            if user_id not in (self.a, self.b):
                return {"not_player": True}
            if user_id in self.attempted:
                return {"already": True}

            pending = self.session.pending
            if pending is None:
                return {"closed": True}
            question = pending["q"]
            parts = T.question_parts(question)
            answers = [line.strip() for line in (text or "").splitlines()
                       if line.strip()]
            if len(answers) != len(parts):
                return {"need": len(parts)}

            correct, note, details = T.grade_parts(question, answers)
            self.attempted.add(user_id)

            if correct:
                result = self.session._finish(True, answers, note, details)
                self.closed = True
                self._record_win(user_id, self.other(user_id))
                return {"winner": result, "user_id": user_id, "question": question}

            if len(self.attempted) >= 2:
                result = self.session._finish(
                    False, ["no answer"] * len(parts),
                    "both players answered incorrectly", []
                )
                self.closed = True
                return {"both_wrong": True, "question": question, "result": result}

            return {"wrong": True}

    def _record_win(self, winner_id: int, loser_id: int) -> None:
        store = self.session._store()
        try:
            store.db.execute(
                "INSERT INTO duel_wins(winner_id, loser_id, created_at) "
                "VALUES(?,?,?)",
                (str(winner_id), str(loser_id), T.now_iso()),
            )
            store.db.commit()
        finally:
            store.db.close()

    def score(self) -> dict[str, int]:
        store = self.session._store()
        try:
            rows = store.db.execute(
                "SELECT winner_id, COUNT(*) FROM duel_wins GROUP BY winner_id"
            ).fetchall()
            return {str(row[0]): int(row[1]) for row in rows}
        finally:
            store.db.close()

    def stats(self) -> dict:
        return self.session._stats_payload()


def _get_duel(user_a: int, user_b: int) -> Duel:
    key = f"duel_{min(user_a, user_b)}_{max(user_a, user_b)}"
    with DUEL_LOCK:
        duel = DUELS.get(key)
        if duel is None:
            duel = Duel(user_a, user_b)
            DUELS[key] = duel
        return duel


def _active_duel_for(user_id: int) -> Duel | None:
    with DUEL_LOCK:
        for duel in DUELS.values():
            if not duel.closed and user_id in (duel.a, duel.b):
                return duel
    return None


def duel_embed(payload: dict, challenger: discord.User,
               opponent: discord.User) -> discord.Embed:
    embed = discord.Embed(
        title="⚔️ 1v1",
        description=_clip(payload["question"], 4000),
        color=discord.Color.gold(),
    )
    parts = payload.get("parts") or []
    if len(parts) > 1:
        embed.add_field(
            name="Reply format",
            value=(f"Send {len(parts)} lines, one answer per part, in order: "
                   + " / ".join(part.get("label", "") for part in parts)),
            inline=False,
        )
    embed.add_field(
        name="Rules",
        value="First correct answer wins. One attempt each. Reply in this DM.",
        inline=False,
    )
    embed.add_field(name="Topic", value=_clip(payload["topic"], 100), inline=True)
    embed.set_footer(text=f"{challenger.display_name} vs {opponent.display_name}")
    return embed


async def _dm(user_id: int, **kwargs) -> bool:
    user = (_BOT.get_user(user_id) if _BOT else None)
    if user is None and _BOT is not None:
        try:
            user = await _BOT.fetch_user(user_id)
        except discord.HTTPException:
            return False
    if user is None:
        return False
    try:
        await user.send(**kwargs)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


# ---------------------------------------------------------------------------
# party views
# ---------------------------------------------------------------------------

class PartyAnswerModal(discord.ui.Modal):
    def __init__(self, key: str, qid: int, parts: list[dict]):
        super().__init__(title="Answer both parts" if len(parts) > 1 else "Your answer")
        self.key = key
        self.qid = qid
        self.inputs: list[discord.ui.TextInput] = []
        for part in parts:
            label = (part.get("label") or "Answer")[:45]
            paragraph = part.get("format") == "short_text"
            field = discord.ui.TextInput(
                label=label,
                style=(discord.TextStyle.paragraph if paragraph
                       else discord.TextStyle.short),
                required=True,
                max_length=1000 if paragraph else 200,
                placeholder="Type your answer…",
            )
            self.add_item(field)
            self.inputs.append(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        party = _get_party(self.key)
        answers = [field.value.strip() for field in self.inputs]

        try:
            outcome = await asyncio.to_thread(
                party.submit, interaction.user.id, self.qid, answers
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return

        if outcome.get("closed"):
            await interaction.followup.send(
                "Too late — that question is already over.", ephemeral=True
            )
            return
        if outcome.get("wrong"):
            await interaction.followup.send(
                "Not quite — keep trying. The question is still open.", ephemeral=True
            )
            return

        winner = outcome["winner"]
        answer = _answer_text_from_parts(winner.get("parts") or [])
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send(embed=result_embed(winner), ephemeral=True)
            return

        try:
            await channel.send(
                f"✅ {interaction.user.mention} got it correct! "
                f"The answer is **{answer}**."
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I got the answer, but I can't post in this channel. "
                "Check **Send Messages** and **Embed Links**.",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Correct! ✅", ephemeral=True)
        try:
            await _finish_and_next(channel, party)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)


class PartyView(discord.ui.View):
    """Persistent buttons for one party channel."""

    def __init__(self, key: str, channel_id: int):
        super().__init__(timeout=None)
        self.key = key
        self.channel_id = channel_id
        specs = (
            ("Answer", discord.ButtonStyle.primary, "answer"),
            ("Hint", discord.ButtonStyle.secondary, "hint"),
            ("Reveal answer", discord.ButtonStyle.danger, "reveal"),
        )
        for label, style, suffix in specs:
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"party:{channel_id}:{suffix}",
            )
            button.callback = getattr(self, f"_{suffix}")
            self.add_item(button)

    async def _answer(self, interaction: discord.Interaction) -> None:
        party = _get_party(self.key)
        pending = party.session.pending
        if pending is None:
            await interaction.response.send_message(
                "That question is over. Start another with `/physics_party`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            PartyAnswerModal(self.key, pending["qid"], pending["q"]["parts"])
        )

    async def _hint(self, interaction: discord.Interaction) -> None:
        party = _get_party(self.key)
        try:
            data = await asyncio.to_thread(party.session.hint)
            await interaction.response.send_message(f"💡 {data['hint']}",
                                                    ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(embed=error_embed(exc),
                                                    ephemeral=True)

    async def _reveal(self, interaction: discord.Interaction) -> None:
        party = _get_party(self.key)
        if not _may_reveal(interaction, party):
            await interaction.response.send_message(
                "Only the party starter or a moderator can reveal the answer.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        question = await asyncio.to_thread(party.reveal)
        if question is not None:
            answer = _answer_text_from_parts(T.question_parts(question))
            try:
                await interaction.channel.send(
                    f"⏭️ No one got it. The answer is **{answer}**."
                )
            except discord.Forbidden:
                await interaction.edit_original_response(
                    content="I can't post the answer here; check my channel permissions.",
                    embed=None,
                    view=None,
                )
                return
        try:
            await _finish_and_next(interaction.channel, party)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await interaction.edit_original_response(embed=error_embed(exc),
                                                     view=None)


def _may_reveal(interaction: discord.Interaction, party: Party) -> bool:
    if interaction.user.id == party.starter_id():
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_messages)


async def _disable_party_message(channel: discord.abc.Messageable,
                                 party: Party) -> None:
    message_id = party.message_id()
    if not message_id:
        return
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def _post_party_question(channel: discord.abc.Messageable,
                               party: Party) -> None:
    payload = await asyncio.to_thread(party.advance)
    view = _party_view(party.key, channel.id)
    message = await channel.send(embed=question_embed(payload, party=True),
                                 view=view)
    party.set_channel(channel.id)
    party.set_message(message.id)


def _party_view(key: str, channel_id: int) -> "PartyView":
    with PARTY_LOCK:
        view = PARTY_VIEWS.get(channel_id)
        if view is None:
            view = PartyView(key, channel_id)
            PARTY_VIEWS[channel_id] = view
            if _BOT is not None:
                _BOT.add_view(view)
        return view


async def _finish_and_next(channel: discord.abc.Messageable,
                           party: Party) -> None:
    await _disable_party_message(channel, party)
    await _post_party_question(channel, party)


def _restore_party_views(bot: commands.Bot) -> None:
    for path in PARTY_DIR.glob("*.db"):
        key = path.stem
        try:
            party = _get_party(key)
        except Exception:  # noqa: BLE001 - skip a damaged/old database
            continue
        if party.session.pending is None:
            continue
        channel_id = party.channel_id()
        if not channel_id:
            continue
        _party_view(key, channel_id)


# ---------------------------------------------------------------------------
# solo views
# ---------------------------------------------------------------------------

class AnswerModal(discord.ui.Modal):
    def __init__(self, user_id: int, parts: list[dict]):
        super().__init__(title="Answer both parts" if len(parts) > 1 else "Your answer")
        self.user_id = user_id
        self.inputs: list[discord.ui.TextInput] = []
        for part in parts:
            label = (part.get("label") or "Answer")[:45]
            paragraph = part.get("format") == "short_text"
            field = discord.ui.TextInput(
                label=label,
                style=(discord.TextStyle.paragraph if paragraph
                       else discord.TextStyle.short),
                required=True,
                max_length=1000 if paragraph else 200,
                placeholder="Type your answer…",
            )
            self.add_item(field)
            self.inputs.append(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        session = _session(self.user_id)
        answers = [field.value.strip() for field in self.inputs]
        try:
            result = await asyncio.to_thread(session.answer, answers)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return

        if result.get("needs_confirmation"):
            await interaction.followup.send(
                embed=confirmation_embed(result),
                view=ConfirmView(self.user_id),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=result_embed(result),
                view=NextView(self.user_id),
                ephemeral=True,
            )


class QuestionView(discord.ui.View):
    def __init__(self, user_id: int, parts: list[dict]):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.parts = parts

    @discord.ui.button(label="Answer", style=discord.ButtonStyle.primary)
    async def answer(self, interaction: discord.Interaction,
                     button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AnswerModal(self.user_id, self.parts))

    @discord.ui.button(label="Hint", style=discord.ButtonStyle.secondary)
    async def hint(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        session = _session(self.user_id)
        try:
            data = await asyncio.to_thread(session.hint)
            await interaction.response.send_message(f"💡 {data['hint']}",
                                                    ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(embed=error_embed(exc),
                                                    ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        await self._advance(interaction, mode="skip")

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.danger)
    async def discard(self, interaction: discord.Interaction,
                      button: discord.ui.Button) -> None:
        await self._advance(interaction, mode="discard")

    async def _advance(self, interaction: discord.Interaction, mode: str) -> None:
        await interaction.response.defer()
        session = _session(self.user_id)
        try:
            if mode == "skip":
                await asyncio.to_thread(session.skip)
            else:
                await asyncio.to_thread(session.discard)
            payload = await asyncio.to_thread(session.next_question)
            await interaction.edit_original_response(
                embed=question_embed(payload),
                view=QuestionView(self.user_id, payload["parts"]),
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.edit_original_response(embed=error_embed(exc),
                                                     view=None)


class NextView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=900)
        self.user_id = user_id

    @discord.ui.button(label="Next question", style=discord.ButtonStyle.primary)
    async def next_question(self, interaction: discord.Interaction,
                            button: discord.ui.Button) -> None:
        await interaction.response.defer()
        session = _session(self.user_id)
        try:
            payload = await asyncio.to_thread(session.next_question)
            await interaction.edit_original_response(
                embed=question_embed(payload),
                view=QuestionView(self.user_id, payload["parts"]),
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.edit_original_response(embed=error_embed(exc),
                                                     view=None)


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=900)
        self.user_id = user_id

    @discord.ui.button(label="My answers are correct",
                       style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction,
                  button: discord.ui.Button) -> None:
        await interaction.response.defer()
        session = _session(self.user_id)
        try:
            result = await asyncio.to_thread(session.confirm, True)
            await interaction.edit_original_response(
                embed=result_embed(result),
                view=NextView(self.user_id),
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.edit_original_response(embed=error_embed(exc),
                                                     view=None)

    @discord.ui.button(label="Discard question", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction,
                 button: discord.ui.Button) -> None:
        await interaction.response.defer()
        session = _session(self.user_id)
        try:
            await asyncio.to_thread(session.confirm, False)
            payload = await asyncio.to_thread(session.next_question)
            await interaction.edit_original_response(
                embed=question_embed(payload),
                view=QuestionView(self.user_id, payload["parts"]),
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.edit_original_response(embed=error_embed(exc),
                                                     view=None)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def setup(bot: commands.Bot) -> None:
    global _BOT
    _BOT = bot
    _configure()
    _restore_party_views(bot)

    @bot.tree.command(name="physics",
                      description="Practice HKDSE Physics (core + Electives 2 & 3)")
    @app_commands.describe(topic="Restrict questions to a topic or group")
    @app_commands.choices(topic=TOPIC_CHOICES)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def physics(interaction: discord.Interaction,
                      topic: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        session = _session(interaction.user.id)
        scope = T.resolve_topics(topic)
        session.set_topics(scope)
        await asyncio.to_thread(session.set_streak_boost, True)
        if (session.pending is not None
                and session.pending["q"]["topic"] not in scope):
            await asyncio.to_thread(session.discard)
        try:
            payload = await asyncio.to_thread(session.next_question, scope)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=question_embed(payload),
            view=QuestionView(interaction.user.id, payload["parts"]),
            ephemeral=True,
        )

    @bot.tree.command(name="physics_stats",
                      description="Your HKDSE Physics rating and accuracy")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def physics_stats(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        session = _session(interaction.user.id)
        try:
            stats = await asyncio.to_thread(session._stats_payload)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(embed=stats_embed(stats), ephemeral=True)

    @bot.tree.command(
        name="physics_party",
        description="Start a public HKDSE Physics party question (first correct wins)",
    )
    @app_commands.describe(topic="Restrict questions to a topic or group")
    @app_commands.choices(topic=TOPIC_CHOICES)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def physics_party(interaction: discord.Interaction,
                            topic: str) -> None:
        key = _party_key(interaction.guild, interaction.channel)
        party = _get_party(key)
        active_channel = party.channel_id()

        if party.session.pending is not None:
            if active_channel and active_channel != interaction.channel.id:
                await interaction.response.send_message(
                    f"A party is already active in <#{active_channel}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "A party question is already active above.", ephemeral=True
                )
            return

        problem = _party_permission_error(interaction)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        scope = T.resolve_topics(topic)
        await interaction.response.defer(thinking=True)
        try:
            party.set_starter(interaction.user.id)
            party.set_topics(scope)
            await _post_party_question(interaction.channel, party)
        except discord.Forbidden:
            await interaction.followup.send(
                "Discord denied the post. Check that I have **View Channel**, "
                "**Send Messages**, **Embed Links**, and **Read Message History** "
                "in this channel.",
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send("Party question posted! 🎉", ephemeral=True)

    @bot.tree.command(
        name="physics_party_stats",
        description="This server/group's party rating and leaderboard",
    )
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def physics_party_stats(interaction: discord.Interaction) -> None:
        key = _party_key(interaction.guild, interaction.channel)
        party = _get_party(key)
        try:
            stats = await asyncio.to_thread(party.session._stats_payload)
            board = await asyncio.to_thread(party.leaderboard)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(embed=error_embed(exc),
                                                    ephemeral=True)
            return
        await interaction.response.send_message(embed=party_stats_embed(stats, board))

    @bot.tree.command(
        name="physics_duel",
        description="Start a private 1v1 through DMs (text answers)",
    )
    @app_commands.describe(opponent="The user to challenge",
                           topic="Restrict questions to a topic or group")
    @app_commands.choices(topic=TOPIC_CHOICES)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def physics_duel(interaction: discord.Interaction,
                           opponent: discord.User,
                           topic: str) -> None:
        if opponent.bot or opponent.id == interaction.user.id:
            await interaction.response.send_message(
                "Choose another human to challenge.", ephemeral=True
            )
            return

        shared = any(guild.get_member(opponent.id) for guild in bot.guilds)
        if not shared:
            await interaction.response.send_message(
                "We need a mutual server so I can DM both of you.",
                ephemeral=True,
            )
            return

        duel = _get_duel(interaction.user.id, opponent.id)
        if not duel.closed:
            await interaction.response.send_message(
                "You already have an active 1v1 with that user.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        scope = T.resolve_topics(topic)
        try:
            payload = await asyncio.to_thread(duel.start, scope)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return

        embed = duel_embed(payload, interaction.user, opponent)
        for user in (interaction.user, opponent):
            delivered = await _dm(user.id, embed=embed)
            if not delivered:
                await asyncio.to_thread(duel.session.skip)
                duel.closed = True
                await interaction.followup.send(
                    f"I couldn't DM {user.mention}. They need to allow DMs "
                    "from server members.", ephemeral=True,
                )
                return
        await interaction.followup.send(
            "1v1 started — check your DMs. 📩", ephemeral=True
        )

    @bot.tree.command(
        name="physics_duel_cancel",
        description="Cancel your active 1v1",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def physics_duel_cancel(interaction: discord.Interaction) -> None:
        duel = _active_duel_for(interaction.user.id)
        if duel is None:
            await interaction.response.send_message(
                "You have no active 1v1.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(duel.session.skip)
        except Exception:
            pass
        duel.closed = True
        with DUEL_LOCK:
            DUELS.pop(duel.key, None)
        for user_id in (duel.a, duel.b):
            await _dm(user_id, content="❌ The 1v1 was cancelled.")
        await interaction.followup.send("1v1 cancelled.", ephemeral=True)

    @bot.tree.command(
        name="physics_duel_stats",
        description="Head-to-head record and pair rating for a 1v1",
    )
    @app_commands.describe(opponent="The other player")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def physics_duel_stats(interaction: discord.Interaction,
                                 opponent: discord.User) -> None:
        duel = _get_duel(interaction.user.id, opponent.id)
        try:
            stats = await asyncio.to_thread(duel.stats)
            score = await asyncio.to_thread(duel.score)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(embed=error_embed(exc),
                                                    ephemeral=True)
            return
        embed = stats_embed(stats)
        embed.title = "1v1 record"
        first_wins = score.get(str(duel.a), 0)
        second_wins = score.get(str(duel.b), 0)
        embed.add_field(
            name="Head-to-head",
            value=f"<@{duel.a}> **{first_wins} — {second_wins}** <@{duel.b}>",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.listen("on_message")
    async def _duel_dm_listener(message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return
        duel = _active_duel_for(message.author.id)
        if duel is None:
            return
        content = message.content.strip()
        if not content or content.startswith("!"):
            return

        try:
            outcome = await asyncio.to_thread(
                duel.submit, message.author.id, content
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            await message.channel.send(embed=error_embed(exc))
            return

        if outcome.get("need"):
            await message.channel.send(
                f"Please send {outcome['need']} line(s), one answer per part."
            )
            return
        if outcome.get("already"):
            await message.channel.send(
                "You already used your attempt for this question."
            )
            return
        if outcome.get("closed") or outcome.get("not_player"):
            return
        if outcome.get("wrong"):
            await message.channel.send(
                "❌ Not quite. Your attempt is used — waiting for your opponent."
            )
            return

        if outcome.get("winner"):
            winner_id = outcome["user_id"]
            answer = _answer_text_from_parts(
                outcome["winner"].get("parts") or []
            )
            text = (f"✅ <@{winner_id}> won the 1v1! "
                    f"The answer is **{answer}**.")
            for user_id in (duel.a, duel.b):
                await _dm(user_id, content=text)
                await _dm(user_id, embed=result_embed(outcome["winner"]))
            with DUEL_LOCK:
                DUELS.pop(duel.key, None)
            return

        if outcome.get("both_wrong"):
            answer = _answer_text_from_parts(
                T.question_parts(outcome["question"])
            )
            text = f"❌ No winner. The answer is **{answer}**."
            for user_id in (duel.a, duel.b):
                await _dm(user_id, content=text)
            with DUEL_LOCK:
                DUELS.pop(duel.key, None)

