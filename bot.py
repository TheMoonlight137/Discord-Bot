import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import asyncio
import subprocess
import os
import sys

load_dotenv()

FISCH_SEASONS = [
    {"name": "Spring", "emoji": "🌸"},
    {"name": "Summer", "emoji": "☀️"},
    {"name": "Autumn", "emoji": "🍂"},
    {"name": "Winter", "emoji": "❄️"},
]
FISCH_SEASON_MINUTES = 576
FISCH_ANCHOR = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
FISCH_ANCHOR_SEASON = 2  # Autumn

LOG_CHANNEL_ID = 1489182661401514084
REPO_DIR = "/home/moonlight/Projects/discord-bot"
TZ8 = timezone(timedelta(hours=8))

TRIALS = [
    {
        "name": "Speedy Enemies",
        "emoji": "⚡",
        "modifier": "All enemies are Nimble",
        "map": "Wrecked Battlefield",
        "skills": True,
    },
    {
        "name": "Glass",
        "emoji": "🧩",
        "modifier": "Base health is set to 1",
        "map": "Stained Temple",
        "skills": True,
    },
    {
        "name": "Quarantine",
        "emoji": "☣️",
        "modifier": "Increases placement footprint of towers by 10",
        "map": "Dusty Bridges",
        "skills": True,
    },
    {
        "name": "Fog",
        "emoji": "🌫️",
        "modifier": "Tower range is reduced by 35%",
        "map": "Winter Abyss",
        "skills": True,
    },
    {
        "name": "Limitation Makes Creativity",
        "emoji": "🧱",
        "modifier": "Tower placement limits are decreased by 50%",
        "map": "Coral Deep",
        "skills": True,
    },
    {
        "name": "Flying Enemies",
        "emoji": "💨",
        "modifier": "All enemies become Flying after Wave 5",
        "map": "Sacred Mountains",
        "skills": True,
    },
    {
        "name": "Jailed Towers",
        "emoji": "🔒",
        "modifier": "A tower type is jailed every wave after Wave 5",
        "map": "Night Station",
        "skills": True,
    },
    {
        "name": "Exploding Enemies",
        "emoji": "💥",
        "modifier": "Enemies explode on death",
        "map": "Wrecked Battlefield II",
        "skills": True,
    },
    {
        "name": "Inflation",
        "emoji": "📈",
        "modifier": "All prices are increased by 50%",
        "map": "Cyber City",
        "skills": True,
    },
    {
        "name": "Committed",
        "emoji": "🧲",
        "modifier": "Towers cannot be sold",
        "map": "Retro Zone",
        "skills": True,
    },
    {
        "name": "Hidden Enemies",
        "emoji": "👁️‍🗨️",
        "modifier": "All enemies become Hidden after Wave 5",
        "map": "Forgotten Docks",
        "skills": True,
    },
    {
        "name": "Broke",
        "emoji": "💸",
        "modifier": "Income is reduced by 50%",
        "map": "Medieval Times",
        "skills": True,
    },
    {
        "name": "Healthy Enemies",
        "emoji": "🤝",
        "modifier": "All enemies are Bloated",
        "map": "Four Seasons",
        "skills": True,
    },
]

TRIAL_HOURS = 3
GMT8 = timezone(timedelta(hours=8))
ANCHOR_TRIAL_INDEX = 10
ANCHOR_TIME = datetime(2026, 8, 18, 23, 0, tzinfo=GMT8)
NUM_TRIALS = len(TRIALS)


def get_current_trial_index():
    now = datetime.now(GMT8)
    elapsed = (now - ANCHOR_TIME).total_seconds() / 3600
    cycles = int(elapsed // TRIAL_HOURS)
    return (ANCHOR_TRIAL_INDEX + cycles) % NUM_TRIALS


def format_trial(trial, index):
    return (
        f"Modifier: {trial['modifier']}\n"
        f"Map: {trial['map']}"
    )


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    bot.loop.create_task(update_fisch_status())
    print(f"Logged in as {bot.user} — synced {len(synced)} commands", flush=True)
    try:
        channel = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
        await channel.send("bot restarted!")
    except (discord.HTTPException, discord.Forbidden, discord.NotFound):
        pass
    bot.loop.create_task(github_sync_loop())


def run_git(*args):
    return subprocess.run(
        ["git", "-C", REPO_DIR, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


async def sync_from_github():
    if run_git("fetch", "github", "main").returncode != 0:
        return False, "fetch from github failed"
    head = run_git("rev-parse", "HEAD").stdout.strip()
    remote = run_git("rev-parse", "github/main").stdout.strip()
    if head == remote:
        return False, "no new changes"
    if run_git("status", "--porcelain").stdout.strip():
        return False, "local changes present, skipped"
    if run_git("merge", "--ff-only", "github/main").returncode != 0:
        return False, "not fast-forwardable, skipped"
    check = subprocess.run(
        [sys.executable, "-m", "py_compile", os.path.join(REPO_DIR, "bot.py")],
        capture_output=True,
    )
    if check.returncode != 0:
        run_git("reset", "--hard", head)
        return False, "pulled code failed to compile, rolled back"
    return True, f"updated to {remote[:7]}"


async def github_sync_loop():
    while True:
        now = datetime.now(TZ8)
        today12 = now.replace(hour=12, minute=0, second=0, microsecond=0)
        tomorrow00 = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        delay = next(
            ((t - now).total_seconds() for t in (today12, tomorrow00) if t > now),
            12 * 3600,
        )
        await asyncio.sleep(delay + 5)
        changed, detail = await sync_from_github()
        print(f"[github-sync] {detail}", flush=True)


async def update_fisch_status():
    while True:
        now = datetime.now(timezone.utc)
        elapsed = (now - FISCH_ANCHOR).total_seconds() / 60
        cycle_pos = elapsed % (FISCH_SEASON_MINUTES * 4)
        season_index = (FISCH_ANCHOR_SEASON + int(cycle_pos // FISCH_SEASON_MINUTES)) % 4

        current_season = FISCH_SEASONS[season_index]
        season_elapsed = cycle_pos % FISCH_SEASON_MINUTES
        remaining = FISCH_SEASON_MINUTES - season_elapsed
        rem_h = int(remaining // 60)
        rem_m = int(remaining % 60)

        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.playing,
                name="Fisch: Seasons",
                state=f"{current_season['emoji']} {current_season['name']} ends in {rem_h}h {rem_m}m",
            )
        )
        await asyncio.sleep(60)


@bot.tree.command(name="ping", description="Pong!")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)


@bot.tree.command(
    name="trialschedule",
    description="Shows the current and upcoming Challenge Trials schedule",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def trialschedule(interaction: discord.Interaction):
    current_index = get_current_trial_index()
    current_trial = TRIALS[current_index]
    now = datetime.now(GMT8)
    elapsed = (now - ANCHOR_TIME).total_seconds() / 3600
    current_cycle = int(elapsed // TRIAL_HOURS)
    next_cycle_end = ANCHOR_TIME + timedelta(hours=(current_cycle + 1) * TRIAL_HOURS)

    upcoming = ""
    for i in range(1, 7):
        trial_index = (current_index + i) % NUM_TRIALS
        trial = TRIALS[trial_index]
        trial_time = next_cycle_end + timedelta(hours=(i - 1) * TRIAL_HOURS)
        upcoming += f"{trial['emoji']} **{trial['name']}** — {trial['modifier']} | Map: {trial['map']} | <t:{int(trial_time.timestamp())}:t>\n"

    embed = discord.Embed(
        title="Challenge Trials Schedule",
        description=f"▶ {current_trial['emoji']} **{current_trial['name']}** — Ends <t:{int(next_cycle_end.timestamp())}:R>",
        color=discord.Color.red(),
    )
    embed.add_field(
        name=" ",
        value=format_trial(current_trial, current_index),
        inline=False,
    )
    embed.add_field(name="Upcoming", value=upcoming, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="currenttrial",
    description="Shows the current Challenge Trial",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def currenttrial(interaction: discord.Interaction):
    content, embed = build_current_trial_embed()
    await interaction.response.send_message(content=content, embed=embed)


@bot.tree.command(
    name="strat",
    description="Shows Challenge Trials strategy guides",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def strat(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Strategy Guides",
        color=discord.Color.red(),
    )
    embed.add_field(
        name="",
        value="[**Trial Chambers 2.0(Unstable and Might Not Work)**](https://docs.google.com/document/d/1NzhAEK4WJ9cA2gDCcACtW-HXQHhJplrE-_ZKkPWXIJk/edit?tab=t.pdj0ytau4jf4)",
        inline=False,
    )
    embed.add_field(
        name="",
        value="[**Maximum Outpost**](https://docs.google.com/document/d/1r_c7pE09-u8j56UrUuKNrVRjUk2mllZNyJZtrZ92cmg/edit?tab=t.43dlgb96w6kf)",
        inline=False,
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="masterer",
    description="Shows the fishing rod list checklist",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def masterer(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Fishing Rod List Checklist",
        description="[View Checklist](https://1drv.ms/x/c/96630b48fdd13aed/IQBzuRfZf-mtS7ILKQPeohemAbV8P-nlAnklIrRcS7bCCNg?e=Sk2NtW)",
        color=discord.Color.red(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="tguide",
    description="Shows TDS tower and farming guides",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def tguide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="TDS Guides",
        color=discord.Color.red(),
    )
    embed.add_field(
        name="",
        value="[**Guide for All Towers**](https://docs.google.com/document/d/1cQ6MJDFngI6bFYkrj_juXX_iM8uolSEwgeGOF4Vn644/preview?tab=t.tv38kihvbgil)",
        inline=False,
    )
    embed.add_field(
        name="",
        value="[**Guide for Farming**](https://docs.google.com/document/d/1cQ6MJDFngI6bFYkrj_juXX_iM8uolSEwgeGOF4Vn644/preview?tab=t.1myxwn7ipcj)",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="hostserver",
    description="Starts a Minecraft server",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.describe(server="Which server to start")
@app_commands.choices(server=[
    app_commands.Choice(name="BlockSurvival (Paper)", value="minecraft.service"),
    app_commands.Choice(name="Customs (NeoForge)", value="minecraft-customs.service"),
])
async def hostserver(interaction: discord.Interaction, server: app_commands.Choice[str]):
    server_name = server.name.split(" (")[0]
    await interaction.response.send_message(f"Starting {server_name}...", ephemeral=True)
    try:
        proc = subprocess.Popen(
            ["systemctl", "--user", "start", server.value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=10)
        if proc.returncode == 0:
            await interaction.edit_original_response(
                content=f"{server_name} started! Give it ~30 seconds to fully load before connecting."
            )
        else:
            stderr = proc.stderr.read().decode()
            await interaction.edit_original_response(
                content=f"Failed to start {server_name}: {stderr or 'unknown error'}"
            )
    except Exception as e:
        await interaction.edit_original_response(
            content=f"Failed to start {server_name}: {e}"
        )


@bot.tree.command(
    name="closeserver",
    description="Stops a Minecraft server",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.describe(server="Which server to stop")
@app_commands.choices(server=[
    app_commands.Choice(name="BlockSurvival (Paper)", value="minecraft.service"),
    app_commands.Choice(name="Customs (NeoForge)", value="minecraft-customs.service"),
])
async def closeserver(interaction: discord.Interaction, server: app_commands.Choice[str]):
    server_name = server.name.split(" (")[0]
    await interaction.response.send_message(f"Stopping {server_name}...", ephemeral=True)
    try:
        proc = subprocess.Popen(
            ["systemctl", "--user", "stop", server.value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=10)
        if proc.returncode == 0:
            await interaction.edit_original_response(
                content=f"{server_name} stopped."
            )
        else:
            stderr = proc.stderr.read().decode()
            await interaction.edit_original_response(
                content=f"Failed to stop {server_name}: {stderr or 'unknown error'}"
            )
    except Exception as e:
        await interaction.edit_original_response(
            content=f"Failed to stop {server_name}: {e}"
        )


@bot.tree.command(
    name="serverstatus",
    description="Shows Minecraft server status",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def serverstatus(interaction: discord.Interaction):
    def is_active(service):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"

    block_survival = is_active("minecraft.service")
    custom = is_active("minecraft-customs.service")

    embed = discord.Embed(
        title="🖥 Minecraft Server Status",
        color=discord.Color.green() if (block_survival or custom) else discord.Color.red(),
    )
    embed.add_field(
        name=f"{'✅' if block_survival else '❌'} Block Survival",
        value="Online" if block_survival else "Offline",
        inline=False,
    )
    embed.add_field(
        name=f"{'✅' if custom else '❌'} Other servers",
        value="Online" if custom else "Offline",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="thhelp",
    description="Lists all Trial Helper commands",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def thhelp(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Trial Helper Commands",
        description="Here's everything this bot can do:",
        color=discord.Color.red(),
    )
    embed.add_field(
        name="⏹ Trials & Games",
        value=(
            "`/trialschedule` — Current + upcoming TDS trials\n"
            "`/currenttrial` — Current trial details\n"
            "`/pin` — DM reminder 15 min before a trial\n"
            "`/strat` — Challenge trials strategy guides"
        ),
        inline=False,
    )
    embed.add_field(
        name="📚 Guides",
        value=(
            "`/masterer` — Fishing rod checklist\n"
            "`/tguide` — TDS tower & farming guides"
        ),
        inline=False,
    )
    embed.add_field(
        name="🖥 Minecraft Server",
        value=(
            "`/hostserver` — Start a server\n"
            "`/closeserver` — Stop a server\n"
            "`/serverstatus` — Server status"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 Misc",
        value="`/ping` — Pong!",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


active_pins: dict[int, asyncio.Task] = {}


def build_current_trial_embed():
    current_index = get_current_trial_index()
    current_trial = TRIALS[current_index]
    now = datetime.now(GMT8)
    elapsed = (now - ANCHOR_TIME).total_seconds() / 3600
    current_cycle = int(elapsed // TRIAL_HOURS)
    next_cycle_end = ANCHOR_TIME + timedelta(hours=(current_cycle + 1) * TRIAL_HOURS)

    embed = discord.Embed(
        title=f"{current_trial['emoji']} {current_trial['name']}",
        color=discord.Color.red(),
    )
    embed.add_field(name="Modifier", value=current_trial["modifier"], inline=True)
    embed.add_field(name="Map", value=current_trial["map"], inline=True)
    embed.add_field(name="Skills", value="Enabled", inline=True)

    content = f"Ends <t:{int(next_cycle_end.timestamp())}:R>"
    return content, embed


class PinView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Cancel Reminder", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your reminder.", ephemeral=True)
            return
        task = active_pins.pop(self.user_id, None)
        if task:
            task.cancel()
        await interaction.response.edit_message(content="Reminder cancelled.", view=None)



@bot.tree.command(
    name="pin",
    description="Get a DM reminder 15 minutes before a specific trial starts",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.describe(trial="Which trial to be reminded about")
@app_commands.choices(trial=[
    app_commands.Choice(name=f"{t['emoji']} {t['name']}", value=i)
    for i, t in enumerate(TRIALS)
])
async def pin(interaction: discord.Interaction, trial: app_commands.Choice[int]):
    user_id = interaction.user.id

    if user_id in active_pins:
        active_pins[user_id].cancel()
        del active_pins[user_id]

    chosen_index = trial.value
    chosen_trial = TRIALS[chosen_index]
    now = datetime.now(GMT8)
    elapsed = (now - ANCHOR_TIME).total_seconds() / 3600
    current_cycle = int(elapsed // TRIAL_HOURS)
    current_index = (ANCHOR_TRIAL_INDEX + current_cycle) % NUM_TRIALS

    offset = (chosen_index - current_index) % NUM_TRIALS
    if offset == 0:
        await interaction.response.send_message(
            content=f"{chosen_trial['emoji']} **{chosen_trial['name']}** is the current trial. Use `/currenttrial` to check it!",
            ephemeral=True,
        )
        return

    target_cycle_end = ANCHOR_TIME + timedelta(hours=(current_cycle + offset) * TRIAL_HOURS)
    reminder_time = target_cycle_end - timedelta(minutes=15)
    wait_seconds = (reminder_time - now).total_seconds()

    async def remind():
        await asyncio.sleep(wait_seconds)
        try:
            await interaction.user.send(
                f"{chosen_trial['emoji']} **{chosen_trial['name']}** starts in 15 minutes!\n"
                f"Modifier: {chosen_trial['modifier']}\n"
                f"Map: {chosen_trial['map']}"
            )
        except discord.Forbidden:
            pass
        active_pins.pop(user_id, None)

    active_pins[user_id] = asyncio.create_task(remind())

    await interaction.response.send_message(
        content=f"You'll be reminded about **{chosen_trial['emoji']} {chosen_trial['name']}** <t:{int(reminder_time.timestamp())}:R>.",
        view=PinView(user_id),
        ephemeral=True,
    )


bot.run(os.environ["DISCORD_TOKEN"])
