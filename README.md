# Global Connection Bot

Discord bot yang menghitung koneksi global dari interaksi dua arah antar-member.

## Fitur

- Mention atau reply dua arah menghasilkan **+1 koneksi per pasangan per hari**.
- Koneksi global berdasarkan Discord User ID, lintas server tempat bot terpasang.
- DM tidak dihitung.
- Current connection, lifetime connection, streak, decay, dan ledger tersimpan di SQLite.
- Setelah 3 hari tanpa koneksi, current connection pasangan dikurangi 50% dibulatkan ke bawah.
- Lifetime connection tidak berkurang.
- Prefix menerima `Q!` dan `q!`.
- Reset global atau pasangan dilindungi tombol konfirmasi dan hanya owner.

## Command

```text
Q!intro / q!intro
Q!koneksi / q!koneksi
Q!koneksi @member
Q!peringkat / q!peringkat
Q!resetkoneksi / q!resetkoneksi
Q!bantuan / q!bantuan
```

Slash command yang tersedia:

```text
/koneksi
/koneksi @member
/peringkat_koneksi
/reset_koneksi
/panduan_koneksi
```

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
```

Aktifkan **Server Members Intent** dan **Message Content Intent** di Discord Developer Portal. Bot butuh View Channel, Send Messages, Read Message History, dan Use Application Commands.

## Testing

```bash
python3 -m unittest tests/test_connection.py -v
```
