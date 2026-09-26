import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import asyncio
import subprocess
import os

load_dotenv()

COLOR_SUCCESS = discord.Color.green()
COLOR_FAIL = discord.Color.red()
COLOR_NATURAL = discord.Color.from_rgb(0x4D, 0xFF, 0xF0)

FISCH_SEASONS = [
    {"name": "Spring", "emoji": "🌸"},
    {"name": "Summer", "emoji": "☀️"},
    {"name": "Autumn", "emoji": "🍂"},
    {"name": "Winter", "emoji": "❄️"},
]
FISCH_SEASON_MINUTES = 576
FISCH_ANCHOR = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
FISCH_ANCHOR_SEASON = 2  # Autumn

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
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    bot.loop.create_task(update_fisch_status())
    print(f"Logged in as {bot.user} — synced {len(synced)} commands", flush=True)


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
    latency_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        description=f"Pong! 🏓 **{latency_ms} ms**",
        color=COLOR_NATURAL,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="say", description="Make the bot say a message")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.describe(message="The message the bot should send")
async def say(interaction: discord.Interaction, message: str):
    if interaction.user.id != 772721325164462101:
        await interaction.response.send_message("You can't control me haha!")
        return
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(message)
    await interaction.delete_original_response()


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
        color=COLOR_NATURAL,
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
    embed = build_current_trial_embed()
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="strat",
    description="Shows Challenge Trials strategy guides",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def strat(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Strategy Guides",
        color=COLOR_NATURAL,
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
        color=COLOR_NATURAL,
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
        color=COLOR_NATURAL,
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
    await interaction.response.send_message(
        embed=discord.Embed(description=f"Starting {server_name}...", color=COLOR_NATURAL),
        ephemeral=True,
    )
    try:
        proc = subprocess.Popen(
            ["systemctl", "--user", "start", server.value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=10)
        if proc.returncode == 0:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"✅ {server_name} started! Give it ~30 seconds to fully load before connecting.",
                    color=COLOR_SUCCESS,
                )
            )
        else:
            stderr = proc.stderr.read().decode()
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Failed to start {server_name}: {stderr or 'unknown error'}",
                    color=COLOR_FAIL,
                )
            )
    except Exception as e:
        await interaction.edit_original_response(
            embed=discord.Embed(
                description=f"Failed to start {server_name}: {e}",
                color=COLOR_FAIL,
            )
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
    await interaction.response.send_message(
        embed=discord.Embed(description=f"Stopping {server_name}...", color=COLOR_NATURAL),
        ephemeral=True,
    )
    try:
        proc = subprocess.Popen(
            ["systemctl", "--user", "stop", server.value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=10)
        if proc.returncode == 0:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"✅ {server_name} stopped.",
                    color=COLOR_SUCCESS,
                )
            )
        else:
            stderr = proc.stderr.read().decode()
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Failed to stop {server_name}: {stderr or 'unknown error'}",
                    color=COLOR_FAIL,
                )
            )
    except Exception as e:
        await interaction.edit_original_response(
            embed=discord.Embed(
                description=f"Failed to stop {server_name}: {e}",
                color=COLOR_FAIL,
            )
        )


SERVER_BOTS = {
    1541490906484703384: "Block Survival",
    1551994606147604630: "Other servers",
}


@bot.tree.command(
    name="serverstatus",
    description="Shows which Minecraft servers are currently online",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def serverstatus(interaction: discord.Interaction):
    online = []
    offline = []

    for user_id, name in SERVER_BOTS.items():
        is_online = False
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            if member is not None and member.status != discord.Status.offline:
                is_online = True
                break
        if is_online:
            online.append(name)
        else:
            offline.append(name)

    if not online:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="No servers are currently online.",
                color=COLOR_FAIL,
            )
        )
        return

    embed = discord.Embed(
        title="🖥 Minecraft Server Status",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Online",
        value="\n".join(f"✅ {name}" for name in online),
        inline=False,
    )
    if offline:
        embed.add_field(
            name="Offline",
            value="\n".join(f"❌ {name}" for name in offline),
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="thhelps",
    description="Lists all Trial Helper commands",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def thhelp(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Trial Helper Commands",
        description="Here's everything this bot can do:",
        color=COLOR_NATURAL,
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
            "`/serverstatus` — Check which servers are online"
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
        description=f"Ends <t:{int(next_cycle_end.timestamp())}:R>",
        color=COLOR_NATURAL,
    )
    embed.add_field(name="Modifier", value=current_trial["modifier"], inline=True)
    embed.add_field(name="Map", value=current_trial["map"], inline=True)
    embed.add_field(name="Skills", value="Enabled", inline=True)

    return embed


class PinView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Cancel Reminder", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=discord.Embed(description="This isn't your reminder.", color=COLOR_FAIL),
                ephemeral=True,
            )
            return
        task = active_pins.pop(self.user_id, None)
        if task:
            task.cancel()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Reminder cancelled.", color=COLOR_SUCCESS),
            view=None,
        )



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
            embed=discord.Embed(
                description=f"{chosen_trial['emoji']} **{chosen_trial['name']}** is the current trial. Use `/currenttrial` to check it!",
                color=COLOR_NATURAL,
            ),
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
                embed=discord.Embed(
                    title=f"{chosen_trial['emoji']} {chosen_trial['name']} starts in 15 minutes!",
                    description=f"Modifier: {chosen_trial['modifier']}\nMap: {chosen_trial['map']}",
                    color=COLOR_NATURAL,
                )
            )
        except discord.Forbidden:
            pass
        active_pins.pop(user_id, None)

    active_pins[user_id] = asyncio.create_task(remind())

    await interaction.response.send_message(
        embed=discord.Embed(
            description=f"You'll be reminded about **{chosen_trial['emoji']} {chosen_trial['name']}** <t:{int(reminder_time.timestamp())}:R>.",
            color=COLOR_SUCCESS,
        ),
        view=PinView(user_id),
        ephemeral=True,
    )

bot.run(os.environ["DISCORD_TOKEN"])
