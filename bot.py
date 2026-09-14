"""Global Connection bot: mutual server interactions earn one connection per pair/day."""
from __future__ import annotations

import logging
import os
import sqlite3
from logging.handlers import RotatingFileHandler
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from connection import ConnectionStore

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
OWNER_USER_ID = int(os.getenv("SOCIALQUEST_OWNER_USER_ID", "0") or 0)
DB_FILE = Path(os.getenv("CONNECTION_DB_FILE", "data/connections.sqlite3"))
CONNECTION_TIMEZONE = os.getenv("CONNECTION_TIMEZONE", "Asia/Jakarta").strip() or "Asia/Jakarta"
LOG_FILE = Path(os.getenv("CONNECTION_LOG_FILE", "data/connection-bot.log"))
LOG_MAX_BYTES = int(os.getenv("CONNECTION_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("CONNECTION_LOG_BACKUP_COUNT", "3"))

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
log_format = "%(asctime)s %(levelname)s %(name)s %(message)s"
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(log_format))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(log_format))
logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])
log = logging.getLogger("connection")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=("Q!", "q!"), intents=intents, help_command=None)


def local_date(now: datetime | None = None) -> date:
    """Return the connection date in the configured local timezone."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(CONNECTION_TIMEZONE)
    except Exception as exc:
        raise ValueError(f"Invalid CONNECTION_TIMEZONE: {CONNECTION_TIMEZONE}") from exc
    return now.astimezone(zone).date()


def utc_date() -> date:
    return local_date()


def pair_key(a: int, b: int) -> tuple[int, int]:
    return tuple(sorted((a, b)))


def paginate_items(items, page_size: int = 5):
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return [items[start:start + page_size] for start in range(0, len(items), page_size)]


class ConnectionPaginationView(discord.ui.View):
    def __init__(self, owner_id: int, embeds: list[discord.Embed]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.embeds = embeds
        self.page = 0
        self._sync_buttons()

    def _sync_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page == len(self.embeds) - 1
        self.indicator.label = f"{self.page + 1}/{len(self.embeds)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Pagination ini milik orang yang menjalankan command.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)


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
        await interaction.response.edit_message(content=f"✅ Data koneksi kamu dengan {self.member.display_name} sudah direset.", view=self)

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
        self.cog.store.reset_all()
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
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.store = ConnectionStore(self.db)
        self.daily_decay.start()
        log.info("store_ready db=%s timezone=%s", DB_FILE, CONNECTION_TIMEZONE)

    def cog_unload(self):
        self.daily_decay.cancel()
        self.db.close()

    def record_target(self, guild_id: int, author_id: int, target_id: int, channel_id: int, message_id: int):
        if author_id == target_id:
            return None
        if self.store.is_opted_out(author_id) or self.store.is_opted_out(target_id):
            log.info("interaction_skipped reason=opt_out author_id=%s target_id=%s guild_id=%s", author_id, target_id, guild_id)
            return False
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
            made = self.store.record_connection(author_id, target_id, utc_date(), guild_id, channel_id)
            if made:
                log.info("connection_formed user_a=%s user_b=%s guild_id=%s channel_id=%s day=%s", a, b, guild_id, channel_id, today)
            else:
                log.info("connection_duplicate_or_skipped user_a=%s user_b=%s guild_id=%s day=%s", a, b, guild_id, today)
            return made
        log.info("interaction_recorded author_id=%s target_id=%s guild_id=%s channel_id=%s day=%s reverse_pending=true", author_id, target_id, guild_id, channel_id, today)
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
                author_name = message.author.display_name or message.author.name or f"User {message.author.id}"
                target_user = self.client.get_user(target_id)
                target_name = target_user.display_name if target_user else f"User {target_id}"
                await message.reply(
                    f"🔗 **Connection terbentuk!** {author_name} × {target_name}\n"
                    "Koneksi hari ini **+1**."
                )

    @tasks.loop(hours=1)
    async def daily_decay(self):
        today = utc_date()
        self.store.apply_decay(today)
        log.info("decay_check date=%s", today)

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
        embed.add_field(name="Cek koneksi", value="`/koneksi` — daftar koneksi pribadi\n`/koneksi @member` — detail pasangan\n`/profil` — statistik koneksi pribadi\n`/peringkat_server` — leaderboard server\n`/peringkat_global` — leaderboard global\n`/statistik` — statistik komunitas\n`/privasi` — pengaturan opt-out", inline=False)
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
            for row in rows[:5]:
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

    @app_commands.command(name="sembunyikan_koneksi", description="Sembunyikan koneksi dengan member tertentu dari peringkat")
    @app_commands.describe(member="Member pasangan yang ingin disembunyikan", hidden="True untuk sembunyikan, False untuk tampilkan lagi")
    async def sembunyikan_koneksi(self, interaction: discord.Interaction, member: discord.Member, hidden: bool = True):
        if member.id == interaction.user.id:
            await interaction.response.send_message("Pilih member lain sebagai pasangan.", ephemeral=True)
            return
        self.store.set_pair_hidden(interaction.user.id, member.id, hidden)
        status = "disembunyikan dari peringkat" if hidden else "ditampilkan lagi di peringkat"
        await interaction.response.send_message(
            f"✅ Koneksi kamu dengan **{member.display_name}** sekarang **{status}**.", ephemeral=True
        )

    @app_commands.command(name="profil", description="Lihat statistik koneksi pribadi")
    async def profil(self, interaction: discord.Interaction):
        stats = self.store.profile_stats(interaction.user.id)
        embed = discord.Embed(
            title=f"👤 Profil koneksi {interaction.user.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="💙 Connection aktif", value=f"**{stats['current_connections']}**", inline=True)
        embed.add_field(name="🏆 Lifetime", value=f"**{stats['lifetime_connections']}**", inline=True)
        embed.add_field(name="🤝 Partner", value=f"**{stats['unique_partners']}**", inline=True)
        embed.add_field(name="🔥 Streak terbaik", value=f"**{stats['best_streak']} hari**", inline=True)
        embed.add_field(name="🔒 Privacy", value="Opt-out aktif" if self.store.is_opted_out(interaction.user.id) else "Aktif", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="peringkat_server", description="Lihat peringkat koneksi di server ini")
    async def peringkat_server(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Command ini hanya bisa dipakai di server.", ephemeral=True)
            return
        rows = self.store.server_leaderboard(interaction.guild.id)
        lines = []
        for index, row in enumerate(rows, start=1):
            first = await self.leaderboard_name(row["user_a"])
            second = await self.leaderboard_name(row["user_b"])
            lines.append(f"**{index}.** **{first}** × **{second}** — **{row['connections']}**")
        text = "\n".join(lines) or "Belum ada koneksi di server ini."
        await interaction.response.send_message(f"🏠 **Peringkat Koneksi Server**\n{text}")

    @app_commands.command(name="peringkat_global", description="Lihat peringkat koneksi global lintas server")
    async def peringkat_global(self, interaction: discord.Interaction):
        rows = self.store.global_leaderboard()
        lines = []
        for index, row in enumerate(rows, start=1):
            first = await self.leaderboard_name(row["user_a"])
            second = await self.leaderboard_name(row["user_b"])
            lines.append(f"**{index}.** **{first}** × **{second}** — **{row['connections']}**")
        text = "\n".join(lines) or "Belum ada koneksi global."
        await interaction.response.send_message(f"🌐 **Peringkat Koneksi Global**\n{text}")

    @app_commands.command(name="statistik", description="Lihat statistik koneksi server")
    async def statistik(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Command ini hanya bisa dipakai di server.", ephemeral=True)
            return
        row = self.db.execute("""
            SELECT COUNT(*) AS total, COUNT(DISTINCT user_a || ':' || user_b) AS pairs
            FROM daily_connections WHERE server_id=?
        """, (interaction.guild.id,)).fetchone()
        await interaction.response.send_message(
            f"📊 **Statistik {interaction.guild.name}**\n"
            f"Connection terbentuk: **{row['total']}**\n"
            f"Pasangan aktif: **{row['pairs']}**"
        )

    @app_commands.command(name="privasi", description="Atur privacy koneksi kamu")
    @app_commands.describe(opt_out="Matikan atau nyalakan pencatatan koneksi", leaderboard="Sembunyikan atau tampilkan dari leaderboard")
    async def privasi(self, interaction: discord.Interaction, opt_out: bool | None = None, leaderboard: bool | None = None):
        if opt_out is None and leaderboard is None:
            status = "aktif" if self.store.is_opted_out(interaction.user.id) else "nonaktif"
            ranking = "tersembunyi" if self.store.leaderboard_opted_out(interaction.user.id) else "tampil"
            await interaction.response.send_message(f"🔒 Opt-out: **{status}**\nLeaderboard: **{ranking}**", ephemeral=True)
            return
        self.store.set_privacy(interaction.user.id, opt_out=opt_out, leaderboard_opt_out=leaderboard)
        log.info("privacy_updated user_id=%s opt_out=%s leaderboard_opt_out=%s", interaction.user.id, opt_out, leaderboard)
        await interaction.response.send_message("✅ Pengaturan privacy kamu sudah diperbarui.", ephemeral=True)

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

    async def leaderboard_name(self, user_id: int) -> str:
        return await self.safe_user_name(user_id)

    @commands.command(name="koneksi", aliases=["connection", "connections"])
    async def prefix_koneksi(self, ctx, member: discord.Member | None = None):
        if member and member.id == ctx.author.id:
            await ctx.reply("Pilih member lain untuk melihat koneksi pasangan.")
            return
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
        if not rows:
            await ctx.reply("🔗 **Koneksi kamu**\n\nBelum ada koneksi. Mulai ngobrol untuk membangun koneksi ✨")
            return

        pages = paginate_items(rows, 5)
        embeds = []
        total_pages = len(pages)
        for page_rows in pages:
            lines = []
            for index, row in enumerate(page_rows, start=1 + len(embeds) * 5):
                other_id = row['user_b'] if row['user_a'] == ctx.author.id else row['user_a']
                name = "someone" if self.store.pair_hidden(ctx.author.id, other_id) else await self.safe_user_name(other_id)
                lines.append(f"**{index}.** 💙 **{name}**  ·  {row['current_connections']} connection  ·  🔥 {row['streak']} hari")
            embed = discord.Embed(
                title="🔗 Koneksi kamu",
                description="\n".join(lines),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Halaman {len(embeds) + 1}/{total_pages} · Terus ngobrol untuk membangun koneksi ✨")
            embeds.append(embed)

        await ctx.reply(embed=embeds[0], view=ConnectionPaginationView(ctx.author.id, embeds))

    @commands.command(name="sembunyikankoneksi", aliases=["hidekoneksi"])
    async def prefix_sembunyikan_koneksi(self, ctx, member: discord.Member | None = None, mode: str = "on"):
        if member is None or member.id == ctx.author.id:
            await ctx.reply("Pakai: `Q!sembunyikankoneksi @member on` atau `Q!sembunyikankoneksi @member off`")
            return
        mode = mode.lower()
        if mode not in {"on", "off", "aktif", "nonaktif"}:
            await ctx.reply("Pakai: `Q!sembunyikankoneksi @member on` atau `Q!sembunyikankoneksi @member off`")
            return
        hidden = mode in {"on", "aktif"}
        self.store.set_pair_hidden(ctx.author.id, member.id, hidden)
        status = "disembunyikan dari peringkat" if hidden else "ditampilkan lagi di peringkat"
        await ctx.reply(f"✅ Koneksi kamu dengan **{member.display_name}** sekarang **{status}**.")

    @commands.command(name="profil", aliases=["profile"])
    async def prefix_profil(self, ctx):
        stats = self.store.profile_stats(ctx.author.id)
        privacy = "Opt-out aktif" if self.store.is_opted_out(ctx.author.id) else "Aktif"
        await ctx.reply(
            f"👤 **Profil koneksi {ctx.author.display_name}**\n\n"
            f"💙 Connection aktif: **{stats['current_connections']}**\n"
            f"🏆 Lifetime: **{stats['lifetime_connections']}**\n"
            f"🤝 Partner unik: **{stats['unique_partners']}**\n"
            f"🔥 Streak terbaik: **{stats['best_streak']} hari**\n"
            f"🔒 Privacy: **{privacy}**"
        )

    @commands.command(name="peringkatserver", aliases=["serverranking", "rankserver"])
    async def prefix_peringkat_server(self, ctx):
        rows = self.store.server_leaderboard(ctx.guild.id) if ctx.guild else []
        lines = []
        for index, row in enumerate(rows, start=1):
            first = await self.leaderboard_name(row["user_a"])
            second = await self.leaderboard_name(row["user_b"])
            lines.append(f"**{index}.** **{first}** × **{second}** — **{row['connections']}**")
        await ctx.reply("🏠 **Peringkat Koneksi Server**\n" + ("\n".join(lines) or "Belum ada koneksi di server ini."))

    @commands.command(name="peringkatglobal", aliases=["globalranking", "globalleaderboard"])
    async def prefix_peringkat_global(self, ctx):
        rows = self.store.global_leaderboard()
        lines = []
        for index, row in enumerate(rows, start=1):
            first = await self.leaderboard_name(row["user_a"])
            second = await self.leaderboard_name(row["user_b"])
            lines.append(f"**{index}.** **{first}** × **{second}** — **{row['connections']}**")
        await ctx.reply("🌐 **Peringkat Koneksi Global**\n" + ("\n".join(lines) or "Belum ada koneksi global."))

    @commands.command(name="statistik", aliases=["stats", "stat"])
    async def prefix_statistik(self, ctx):
        if not ctx.guild:
            await ctx.reply("Command ini hanya bisa dipakai di server.")
            return
        row = self.db.execute("""
            SELECT COUNT(*) AS total, COUNT(DISTINCT user_a || ':' || user_b) AS pairs
            FROM daily_connections WHERE server_id=?
        """, (ctx.guild.id,)).fetchone()
        await ctx.reply(
            f"📊 **Statistik {ctx.guild.name}**\n"
            f"Connection terbentuk: **{row['total']}**\n"
            f"Pasangan aktif: **{row['pairs']}**"
        )

    @commands.command(name="privasi", aliases=["privacy"])
    async def prefix_privasi(self, ctx, mode: str = "status", value: str | None = None):
        mode = mode.lower()
        value = value.lower() if value else None
        if mode in {"status", "cek", "check"}:
            opt_out = "aktif" if self.store.is_opted_out(ctx.author.id) else "nonaktif"
            leaderboard = "tersembunyi" if self.store.leaderboard_opted_out(ctx.author.id) else "tampil"
            await ctx.reply(f"🔒 Opt-out: **{opt_out}**\nLeaderboard: **{leaderboard}**")
            return
        if mode not in {"optout", "leaderboard", "ranking"} or value not in {"on", "off", "aktif", "nonaktif"}:
            await ctx.reply("Pakai: `Q!privasi status`, `Q!privasi optout on/off`, atau `Q!privasi leaderboard on/off`")
            return
        enabled = value in {"on", "aktif"}
        if mode == "optout":
            self.store.set_privacy(ctx.author.id, opt_out=enabled)
            label = "pencatatan koneksi"
        else:
            self.store.set_privacy(ctx.author.id, leaderboard_opt_out=enabled)
            label = "leaderboard"
        status = "aktif" if enabled else "nonaktif"
        await ctx.reply(f"✅ Opt-out **{label}** sekarang **{status}**.")

    @commands.command(name="peringkat", aliases=["ranking", "leaderboard"])
    async def prefix_peringkat(self, ctx):
        rows = self.store.server_leaderboard(ctx.guild.id) if ctx.guild else []
        lines = []
        for index, row in enumerate(rows, start=1):
            first = await self.leaderboard_name(row["user_a"])
            second = await self.leaderboard_name(row["user_b"])
            lines.append(f"**{index}.** **{first}** × **{second}** — **{row['connections']}**")
        await ctx.reply("🏆 **Peringkat Koneksi Server**\n" + ("\n".join(lines) or "Belum ada koneksi di server ini."))

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
        embed.add_field(name="📊 Command", value="`Q!koneksi` / `q!koneksi` — daftar koneksi\n`Q!koneksi @member` — detail pasangan\n`Q!profil` — statistik koneksi pribadi\n`Q!peringkatserver` — leaderboard server\n`Q!statistik` — statistik komunitas\n`Q!privasi status` — cek privacy\n`Q!privasi optout on/off` — opt-out pencatatan\n`Q!privasi leaderboard on/off` — hide dari leaderboard\n`Q!peringkatglobal` — peringkat koneksi global\n`Q!resetkoneksi` — reset dengan tombol konfirmasi", inline=False)
        embed.add_field(name="➕ Invite ke server lain", value="[Klik di sini untuk invite Solit](https://discord.com/oauth2/authorize?client_id=1547649664&scope=bot%20applications.commands&permissions=2147569)\nCatatan: kamu harus punya izin **Manage Server / Kelola Server** atau izin untuk menambahkan bot ke server tersebut.", inline=False)
        embed.set_footer(text="Ngobrol santai, jangan spam mention 😊")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        log.info(
            "command_received command=%s user_id=%s guild_id=%s channel_id=%s",
            getattr(ctx.command, "qualified_name", "unknown"),
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            getattr(ctx.channel, "id", None),
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            await ctx.reply("Command tidak ditemukan. Pakai `Q!bantuan` untuk melihat command yang tersedia.", mention_author=False)
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Argumen `{error.param.name}` belum diisi. Pakai `Q!bantuan` untuk contoh penggunaan.", mention_author=False)
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply("Member tersebut tidak ditemukan. Gunakan mention member yang valid.", mention_author=False)
            return
        log.exception("command_failed command=%s user_id=%s", getattr(ctx.command, "qualified_name", "unknown"), ctx.author.id, exc_info=error)
        await ctx.reply("Command gagal diproses. Coba lagi sebentar lagi.", mention_author=False)

    @commands.command(name="bantuan", aliases=["help"])
    async def bantuan(self, ctx):
        await ctx.reply("Pakai `Q!intro` untuk panduan. Command utama: `Q!koneksi`, `Q!profil`, `Q!peringkat`, `Q!peringkatglobal`, `Q!peringkatserver`, `Q!statistik`, dan `Q!privasi`.")


@bot.event
async def setup_hook():
    await bot.add_cog(ConnectionCog(bot))


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    log.info("bot_ready user_id=%s user=%s guild_count=%d", bot.user.id if bot.user else None, bot.user, len(bot.guilds))


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN belum diisi")
    bot.run(TOKEN)
