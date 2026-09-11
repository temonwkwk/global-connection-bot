"""Global Connection bot: mutual server interactions earn one connection per pair/day."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from connection import ConnectionStore

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
OWNER_USER_ID = int(os.getenv("SOCIALQUEST_OWNER_USER_ID", "0") or 0)
DB_FILE = Path(os.getenv("CONNECTION_DB_FILE", "data/connections.sqlite3"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("connection")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=("Q!", "q!"), intents=intents, help_command=None)


def utc_date() -> date:
    return datetime.now(timezone.utc).date()


def pair_key(a: int, b: int) -> tuple[int, int]:
    return tuple(sorted((a, b)))


class ResetPairView(discord.ui.View):
    def __init__(self, cog, owner_id: int, member: discord.Member):
        super().__init__(timeout=60)
        self.cog = cog
        self.owner_id = owner_id
        self.member = member

    @discord.ui.button(label="Reset pasangan", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Hanya owner bot yang bisa mengonfirmasi.", ephemeral=True)
            return
        self.cog.store.reset_pair(self.owner_id, self.member.id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"✅ Data koneksi kamu dengan {self.member.mention} sudah direset.", view=self)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Hanya owner bot yang bisa membatalkan.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Reset pasangan dibatalkan.", view=self)


class GlobalResetView(discord.ui.View):
    def __init__(self, cog, owner_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.owner_id = owner_id

    @discord.ui.button(label="Reset GLOBAL", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Hanya owner bot yang bisa mengonfirmasi.", ephemeral=True)
            return
        self.cog.db.execute("DELETE FROM interactions")
        self.cog.db.execute("DELETE FROM daily_connections")
        self.cog.db.execute("DELETE FROM ledger")
        self.cog.db.execute("DELETE FROM pairs")
        self.cog.db.commit()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="✅ Semua data koneksi global sudah direset.", view=self)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Hanya owner bot yang bisa membatalkan.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Reset global dibatalkan.", view=self)


class ConnectionCog(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_FILE)
        self.store = ConnectionStore(self.db)
        self.daily_decay.start()

    def cog_unload(self):
        self.daily_decay.cancel()
        self.db.close()

    def record_target(self, guild_id: int, author_id: int, target_id: int, channel_id: int, message_id: int):
        if author_id == target_id:
            return None
        today = utc_date().isoformat()
        a, b = pair_key(author_id, target_id)
        # A directed interaction is recorded once per day. The reverse direction
        # completes the pair and awards exactly one connection for that day.
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS interactions (author_id INTEGER, target_id INTEGER, day TEXT, guild_id INTEGER, channel_id INTEGER, message_id INTEGER, PRIMARY KEY(author_id,target_id,day))"
        )
        reverse = self.db.execute(
            "SELECT 1 FROM interactions WHERE author_id=? AND target_id=? AND day=?", (target_id, author_id, today)
        ).fetchone()
        self.db.execute(
            "INSERT OR IGNORE INTO interactions VALUES (?,?,?,?,?,?)",
            (author_id, target_id, today, guild_id, channel_id, message_id),
        )
        self.db.commit()
        if reverse:
            return self.store.record_connection(author_id, target_id, utc_date(), guild_id, channel_id)
        return False

    async def targets_from(self, message: discord.Message) -> set[int]:
        targets = {member.id for member in message.mentions if not member.bot}
        if message.reference:
            referenced = message.reference.resolved
            if referenced is None and message.reference.message_id:
                try:
                    referenced = await message.channel.fetch_message(message.reference.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    referenced = None
            author = getattr(referenced, "author", None)
            if author and not author.bot:
                targets.add(author.id)
        return targets

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        for target_id in await self.targets_from(message):
            made = self.record_target(message.guild.id, message.author.id, target_id, message.channel.id, message.id)
            if made:
                await message.reply(
                    f"🔗 **Connection terbentuk!** <@{message.author.id}> × <@{target_id}>\n"
                    "Koneksi hari ini **+1**."
                )

    @tasks.loop(hours=1)
    async def daily_decay(self):
        self.store.apply_decay(utc_date())

    @daily_decay.before_loop
    async def before_decay(self):
        await self.client.wait_until_ready()

    @app_commands.command(name="panduan_koneksi", description="Tampilkan panduan sistem Global Connection")
    async def panduan(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔗 Global Connection",
            description="Bangun koneksi lewat percakapan dua arah di server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Cara mendapatkan koneksi", value="Mention atau reply member lain. Kalau dia membalas atau mention balik di hari yang sama, kalian mendapat **+1 koneksi**.", inline=False)
        embed.add_field(name="Aturan", value="Satu pasangan maksimal +1 per hari. DM tidak dihitung. Koneksi berlaku global selama bot ada di server tempat interaksi terjadi.", inline=False)
        embed.add_field(name="Cek koneksi", value="`/koneksi` — daftar koneksi pribadi\n`/koneksi @member` — detail pasangan\n`/peringkat_koneksi` — peringkat global", inline=False)
        embed.add_field(name="Streak & histori", value="Streak bertambah jika terhubung setiap hari. Setelah 3 hari tanpa koneksi, current connection berkurang 50% (dibulatkan ke bawah), tetapi lifetime dan ledger tetap tersimpan.", inline=False)
        embed.add_field(name="➕ Invite ke server lain", value="[Klik di sini untuk invite Solit](https://discord.com/oauth2/authorize?client_id=1547649664&scope=bot%20applications.commands&permissions=2147569)\nCatatan: kamu harus punya izin **Manage Server / Kelola Server** atau izin untuk menambahkan bot ke server tersebut.", inline=False)
        embed.set_footer(text="Ngobrol santai, jangan spam mention 😊")
        await interaction.response.send_message(embed=embed)

    @app_commands.describe(member="Kosongkan untuk melihat daftar koneksi pribadi")
    async def koneksi(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if member and member.id == interaction.user.id:
            await interaction.response.send_message("Pilih member lain untuk melihat koneksi pasangan.", ephemeral=True)
            return
        if member:
            pair = self.store.get_pair(interaction.user.id, member.id)
            embed = discord.Embed(
                title="🔗 Koneksi kalian",
                description=f"**{interaction.user.display_name}**  ×  **{member.display_name}**",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="💙 Connection", value=f"**{pair['current_connections']}**", inline=True)
            embed.add_field(name="🏆 Lifetime", value=f"**{pair['lifetime_connections']}**", inline=True)
            embed.add_field(name="🔥 Streak", value=f"**{pair['streak']} hari**", inline=True)
            embed.set_footer(text="Terus ngobrol untuk menjaga koneksi kalian ✨")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        rows = self.store.connections_for(interaction.user.id)
        if not rows:
            text = "Belum ada koneksi. Mulai ngobrol dengan mention atau reply dua arah."
        else:
            lines = []
            for row in rows[:15]:
                other_id = row["user_b"] if row["user_a"] == interaction.user.id else row["user_a"]
                user = self.client.get_user(other_id)
                name = user.display_name if user else f"User {other_id}"
                lines.append(f"💙 **{name}**  ·  {row['current_connections']} connection  ·  🔥 {row['streak']} hari")
            text = "\n".join(lines)
        embed = discord.Embed(
            title="🔗 Koneksi kamu",
            description=text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Terus ngobrol untuk membangun koneksi ✨")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="peringkat_koneksi", description="Lihat peringkat koneksi global")
    async def peringkat(self, interaction: discord.Interaction):
        rows = self.db.execute("SELECT user_a, user_b, current_connections FROM pairs ORDER BY current_connections DESC LIMIT 10").fetchall()
        text = "\n".join(f"<@{a}> × <@{b}> — **{score}**" for a, b, score in rows) or "Belum ada koneksi."
        await interaction.response.send_message(f"🏆 **Peringkat Koneksi Global**\n{text}")

    @app_commands.command(name="reset_koneksi", description="Reset data koneksi testing")
    @app_commands.describe(member="Kosongkan untuk reset global; isi untuk reset satu pasangan")
    async def reset(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if not OWNER_USER_ID or interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message("Command ini hanya bisa dipakai owner bot.", ephemeral=True)
            return
        if member:
            if member.id == interaction.user.id:
                await interaction.response.send_message("Pilih member lain sebagai pasangan.", ephemeral=True)
                return
            pair = self.store.get_pair(interaction.user.id, member.id)
            await interaction.response.send_message(
                f"⚠️ Reset koneksi pasangan **kamu × {member.display_name}**?\n"
                f"Current: {pair['current_connections']} · Lifetime: {pair['lifetime_connections']}\n"
                "Tindakan ini tidak bisa dibatalkan.",
                view=ResetPairView(self, interaction.user.id, member), ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "⚠️ **Reset GLOBAL semua koneksi?**\nSemua pasangan, streak, ledger, dan histori akan dihapus.\nTindakan ini tidak bisa dibatalkan.",
            view=GlobalResetView(self, interaction.user.id), ephemeral=True,
        )

    async def safe_user_name(self, user_id: int) -> str:
        user = self.client.get_user(user_id)
        if user is None:
            try:
                user = await self.client.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return f"User {user_id}"
        return user.display_name or user.name or f"User {user_id}"

    @commands.command(name="koneksi", aliases=["connection", "connections"])
    async def prefix_koneksi(self, ctx, member: discord.Member | None = None):
        if member:
            pair = self.store.get_pair(ctx.author.id, member.id)
            await ctx.reply(
                f"🔗 **Koneksi kalian**\n"
                f"**{ctx.author.display_name}**  ×  **{member.display_name}**\n\n"
                f"💙 Connection: **{pair['current_connections']}**\n"
                f"🏆 Lifetime: **{pair['lifetime_connections']}**\n"
                f"🔥 Streak: **{pair['streak']} hari**\n\n"
                "_Terus ngobrol untuk menjaga koneksi kalian ✨_"
            )
            return
        rows = self.store.connections_for(ctx.author.id)
        lines = []
        for row in rows[:15]:
            other_id = row['user_b'] if row['user_a'] == ctx.author.id else row['user_a']
            name = await self.safe_user_name(other_id)
            lines.append(f"💙 **{name}**  ·  {row['current_connections']} connection  ·  🔥 {row['streak']} hari")
        text = "\n".join(lines) or "Belum ada koneksi. Mulai ngobrol untuk membangun koneksi ✨"
        await ctx.reply(f"🔗 **Koneksi kamu**\n\n{text}")

    @commands.command(name="peringkat", aliases=["ranking", "leaderboard"])
    async def prefix_peringkat(self, ctx):
        rows = self.db.execute("SELECT user_a, user_b, current_connections FROM pairs ORDER BY current_connections DESC LIMIT 10").fetchall()
        text = "\n".join(f"<@{a}> × <@{b}> — **{score}**" for a,b,score in rows) or "Belum ada koneksi."
        await ctx.reply(f"🏆 **Peringkat Koneksi Global**\n{text}")

    @commands.command(name="resetkoneksi")
    async def prefix_reset(self, ctx, member: discord.Member | None = None):
        if ctx.author.id != OWNER_USER_ID:
            await ctx.reply("Command ini hanya bisa dipakai owner bot.")
            return
        if member:
            if member.id == ctx.author.id:
                await ctx.reply("Pilih member lain sebagai pasangan.")
                return
            pair = self.store.get_pair(ctx.author.id, member.id)
            await ctx.send(
                f"⚠️ Reset koneksi pasangan **kamu × {member.display_name}**?\n"
                f"Current: {pair['current_connections']} · Lifetime: {pair['lifetime_connections']}\n"
                "Tindakan ini tidak bisa dibatalkan.",
                view=ResetPairView(self, ctx.author.id, member),
            )
        else:
            await ctx.send(
                "⚠️ **Reset GLOBAL semua koneksi?**\nSemua pasangan, streak, ledger, dan histori akan dihapus.\nTindakan ini tidak bisa dibatalkan.",
                view=GlobalResetView(self, ctx.author.id),
            )

    @commands.command(name="intro")
    async def intro_prefix(self, ctx):
        embed = discord.Embed(
            title="🔗 Global Connection",
            description="Bangun koneksi lewat percakapan dua arah dengan member lain.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="💬 Cara mendapatkan koneksi", value="Mention atau reply member lain. Kalau dia membalas atau mention balik di hari yang sama, kalian mendapat **+1 koneksi**.\n\nContoh:\n> A: @B sudah makan?\n> B: Sudah, kamu?", inline=False)
        embed.add_field(name="📌 Aturan", value="• Satu pasangan maksimal **+1 per hari**\n• DM tidak dihitung\n• Koneksi berlaku global selama bot ada di server interaksi\n• Setelah 3 hari tanpa koneksi, current connection berkurang 50%", inline=False)
        embed.add_field(name="📊 Command", value="`Q!koneksi` / `q!koneksi` — daftar koneksi\n`Q!koneksi @member` — detail pasangan\n`Q!peringkat` — peringkat global\n`Q!resetkoneksi` — reset dengan tombol konfirmasi", inline=False)
        embed.add_field(name="➕ Invite ke server lain", value="[Klik di sini untuk invite Solit](https://discord.com/oauth2/authorize?client_id=1547649664&scope=bot%20applications.commands&permissions=2147569)\nCatatan: kamu harus punya izin **Manage Server / Kelola Server** atau izin untuk menambahkan bot ke server tersebut.", inline=False)
        embed.set_footer(text="Ngobrol santai, jangan spam mention 😊")
        await ctx.send(embed=embed)

    @commands.command(name="bantuan", aliases=["help"])
    async def bantuan(self, ctx):
        await ctx.reply("Pakai `Q!intro` untuk panduan. `Q!koneksi` untuk melihat koneksi, `Q!koneksi @member` untuk detail pasangan, dan `Q!peringkat` untuk leaderboard.")


@bot.event
async def setup_hook():
    await bot.add_cog(ConnectionCog(bot))


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    log.info("Global Connection online sebagai %s; %d server", bot.user, len(bot.guilds))


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN belum diisi")
    bot.run(TOKEN)
