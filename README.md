# Global Connection Bot

Discord bot yang menghitung koneksi global dari interaksi dua arah antar-member.

Riwayat perubahan proyek: [`CHANGELOG.md`](CHANGELOG.md).

## Fitur

- Mention atau reply dua arah menghasilkan **+1 koneksi per pasangan per hari**.
- Koneksi global berdasarkan Discord User ID, lintas server tempat bot terpasang.
- DM tidak dihitung.
- Current connection, lifetime connection, streak, decay, dan ledger tersimpan di SQLite.
- Setelah 3 hari tanpa koneksi, current connection pasangan dikurangi 50% dibulatkan ke bawah.
- Lifetime connection tidak berkurang.
- Prefix menerima `Q!` dan `q!`.
- Reset global atau pasangan dilindungi tombol konfirmasi dan hanya owner.
- Wallet LinkCoin: user baru mendapat **10.000 LinkCoin**, terlihat di profil.
- Streak 7 hari memberi **100 LinkCoin** ke masing-masing member pasangan (milestone tiap kelipatan 7).
- Minigame pertama: `Q!dice <taruhan> high/low`, taruhan 10–2.000 LinkCoin.

## Command

```text
Q!intro / q!intro
Q!koneksi / q!koneksi (top 5; koneksi yang di-hide tampil sebagai `someone`)
Q!koneksi @member
Q!dice <taruhan> high/low — game dadu LinkCoin (10–2.000)
Q!games / q!games — daftar game dan tutorial singkat
Q!profil — termasuk saldo LinkCoin
`Q!sembunyikankoneksi @member on/off` — sembunyikan atau tampilkan koneksi dengan member tertentu
Q!peringkatglobal / q!peringkatglobal — peringkat koneksi global lintas server
`Q!peringkat` / `q!peringkat` — peringkat koneksi server
Q!resetkoneksi / q!resetkoneksi
Q!bantuan / q!bantuan
```

Slash command yang tersedia:

```text
/koneksi
/koneksi @member
/peringkat_global
/peringkat_server
/statistik
/profil
/privasi
/reset_koneksi
/panduan_koneksi
```

## Logging

Bot menulis log ke `data/connection-bot.log` dan tetap menampilkan log ke console.
File log otomatis berotasi saat mencapai ukuran maksimum.

Konfigurasi opsional di `.env`:

```text
CONNECTION_LOG_FILE=data/connection-bot.log
CONNECTION_LOG_MAX_BYTES=5242880
CONNECTION_LOG_BACKUP_COUNT=3
```

Log mencatat startup, command, mention/reply yang masuk, connection yang terbentuk,
decay, perubahan privacy, dan error. Isi pesan Discord tidak dicatat.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Isi `.env`:

```env
DISCORD_BOT_TOKEN=token_bot
SOCIALQUEST_OWNER_USER_ID=discord_user_id_owner
CONNECTION_DB_FILE=data/connections.sqlite3
CONNECTION_TIMEZONE=Asia/Jakarta
```

Aktifkan **Server Members Intent** dan **Message Content Intent** di Discord Developer Portal. Bot butuh View Channel, Send Messages, Read Message History, dan Use Application Commands.

## Testing

```bash
python3 -m unittest tests/test_connection.py -v
```
