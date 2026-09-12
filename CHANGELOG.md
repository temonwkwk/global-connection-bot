# Changelog — Global Connection Bot

Semua perubahan penting proyek dicatat di sini.

## [Unreleased] — 2026-09-12

### Added

- Logging aplikasi ke `data/connection-bot.log`.
- Rotasi log otomatis berbasis ukuran file:
  - default maksimum 5 MB;
  - default menyimpan 3 file backup.
- Konfigurasi logging melalui:
  - `CONNECTION_LOG_FILE`;
  - `CONNECTION_LOG_MAX_BYTES`;
  - `CONNECTION_LOG_BACKUP_COUNT`.
- Event yang dicatat:
  - bot siap dan jumlah server;
  - inisialisasi database;
  - command prefix yang dipakai;
  - interaksi mention/reply yang menunggu balasan;
  - connection yang terbentuk;
  - duplicate atau interaksi yang dilewati;
  - decay connection;
  - perubahan pengaturan privacy;
  - error command.
- Logging ledger untuk event `daily_connection` dan `decay`.
- Dokumentasi logging di README.

### Security / Privacy

- Isi pesan Discord tidak ditulis ke log.
- Token Discord tidak ditulis ke log.
- File runtime `data/*.log` ditambahkan ke `.gitignore`.

### Verification

- 16 unit test lulus.
- Python syntax check lulus untuk `bot.py` dan `connection.py`.
- `git diff --check` lulus.

> Catatan: perubahan pada bagian ini masih berada di working tree dan belum dibuat commit.

## [1.1.0] — 2026-09-11

### Changed

- Merapikan tampilan daftar koneksi agar nama user bisa di-resolve dengan aman.
- Menambahkan fallback `User <id>` jika user tidak tersedia di cache/API.
- Menambahkan tampilan pagination untuk daftar koneksi.
- Menambahkan fitur menyembunyikan koneksi pasangan dari leaderboard.
- Menambahkan pengaturan privacy user dan opt-out leaderboard.
- Menambahkan leaderboard per server.
- Menambahkan statistik profil dan statistik komunitas.
- Menambahkan command prefix `Q!` dan `q!` untuk fitur utama.
- Menambahkan panduan/invite bot ke server lain.
- Memisahkan reset pasangan dan reset global dengan konfirmasi owner.

### Fixed

- Tampilan mention mentah yang bisa berubah menjadi `unknown-user` pada daftar lintas server.
- Validasi batas tampilan daftar koneksi.

Commit: `a9314ee` — `Polish connection display and invite guide`

## [1.0.0] — 2026-09-11

### Added

- Sistem Global Connection berbasis pasangan Discord User ID.
- Mention atau reply dua arah menghasilkan maksimal `+1 connection` per pasangan per hari.
- Pair key canonical agar urutan user tidak memengaruhi data.
- Penyimpanan SQLite untuk:
  - current connection;
  - lifetime connection;
  - streak;
  - daily connection;
  - interaction ledger;
  - decay history.
- Decay setelah tiga hari tanpa koneksi.
- Lifetime connection tetap tersimpan saat decay.
- Dukungan timezone koneksi, default `Asia/Jakarta`.
- Dukungan prefix `Q!` dan `q!`.
- Slash command dasar untuk koneksi, profil, leaderboard, privacy, statistik, dan reset.
- Owner-only reset global dan reset pasangan.
- Test suite awal untuk logika koneksi, streak, decay, privacy, dan leaderboard.
- README dan `.env.example` untuk setup.

Commit: `9c4f6ff` — `Build global connection Discord bot`
