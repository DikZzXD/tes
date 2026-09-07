# Wa Maklu — Cek Cooldown OTP WhatsApp

Alat baris perintah untuk membaca **status cooldown OTP** sebuah nomor WhatsApp
secara langsung dari server registrasi WhatsApp (`v.whatsapp.net/v2/code`).

Berbeda dengan endpoint `/v2/exist` yang cuma bilang "nomor ini ada atau tidak",
endpoint `/v2/code` mengembalikan **sisa waktu tunggu nyata** sebelum sebuah nomor
boleh meminta kode OTP lagi (`sms_wait`, `voice_wait`, `retry_after`) beserta alasannya
(`too_recent`, `blocked`, `bad_token`, dan lain-lain).

---

## Kegunaan

- Mengetahui apakah sebuah nomor baru saja meminta OTP dan sedang dalam masa cooldown.
- Membaca berapa lama (jam/menit/detik) sisa cooldown SMS maupun voice.
- Membedakan nomor yang kena cooldown (`too_recent`) dari nomor yang diblokir
  server (`blocked`) atau versi client kedaluwarsa (`old_version`).

---

## Prasyarat

- Node.js 18 atau lebih baru (butuh `fetch` bawaan).
- Koneksi internet dari IP yang tidak sedang diblokir WhatsApp.

## Instalasi

```bash
cd code
npm install
```

## Cara Pakai

```bash
# Cek cooldown SMS (default)
node cek_wait.js +22378862602

# Cek cooldown lewat voice call
node cek_wait.js +22378862602 voice

# Paksa pakai versi WhatsApp tertentu (token harus cocok dengan versi ini)
WA_VERSION=2.26.33.73 node cek_wait.js +22378862602

# Paksa kode negara kalau nomornya ditolak parser otomatis (lihat catatan di bawah)
WA_CC=223 node cek_wait.js +22392909522
```

### Contoh Keluaran

```
Nomor  : +22378862602 | versi: 2.26.33.73 | method: sms
Respons: {
  "reason": "too_recent",
  "sms_wait": 45015,
  "voice_wait": -1,
  "retry_after": 45015,
  "status": "fail"
}
---
sms_wait   : 12 jam 30 menit (45015 detik)
voice_wait : tidak tersedia (method dinonaktifkan server)
retry_after: 12 jam 30 menit (45015 detik)
```

---

## Cara Kerja

Server registrasi WhatsApp mewajibkan setiap request `/v2/code` membawa **token**
yang diturunkan dari versi client. Untuk jalur iOS, tokennya:

```
packageMD5 = md5(WA_VERSION)
token      = md5(WA_SECRET + packageMD5 + nomor_tanpa_kode_negara)
```

`cek_wait.js` juga membangun material kriptografi Signal (identity key, noise key,
signed pre-key) yang diminta endpoint, mengirim request, lalu memformat field
`sms_wait` / `voice_wait` / `retry_after` menjadi jam-menit-detik.

Nilai penting pada respons:

| Field         | Arti                                                              |
|---------------|-------------------------------------------------------------------|
| `sms_wait`    | Detik tersisa sebelum boleh minta OTP via SMS (`0` = boleh sekarang) |
| `voice_wait`  | Sama seperti di atas, untuk voice (`-1` = method dinonaktifkan)   |
| `retry_after` | Detik sampai server mau memproses request berikutnya             |
| `reason`      | `too_recent`, `blocked`, `old_version`, `bad_token`, dll          |

---

## Berkas dalam Folder Ini

| Berkas          | Keterangan                                                                 |
|-----------------|----------------------------------------------------------------------------|
| `cek_wait.js`   | Baca cooldown OTP via `/v2/code`. **Meminta server kirim kode** (memicu OTP). |
| `cek_ex.js`     | Cek status nomor via `/v2/exist`. **Hanya mengecek, TIDAK kirim OTP.**       |
| `token_wa.js`   | Referensi perhitungan token gaya Android (HMAC-SHA1). Tidak dipakai jalur iOS. |
| `replay_enc.js` | Alat uji: replay blob ENC hasil capture untuk verifikasi endpoint masih hidup. |
| `about_logo.png`| Aset yang dibutuhkan `token_wa.js` untuk turunkan key Android.              |

## `cek_ex.js` vs `cek_wait.js` — mana yang dipakai?

| Hal | `cek_ex.js` (`/v2/exist`) | `cek_wait.js` (`/v2/code`) |
|-----|---------------------------|----------------------------|
| Memicu kirim OTP? | **Tidak** — cuma cek status | **Ya** — server benar-benar kirim kode |
| Aman diulang tanpa ganggu nomor | Ya | Tidak (bisa mulai/ubah cooldown) |
| Baca `sms_wait` cooldown asli | Terbatas (lihat catatan) | Ya, saat `reason: too_recent` |

```bash
# Cuma cek status, tanpa kirim OTP
node cek_ex.js +22378862602
```

**Catatan penting soal `cek_ex.js`:** endpoint `/v2/exist` di aplikasi asli
mengembalikan `sms_wait` nyata (mis. `3371`) HANYA jika request membawa header
`Authorization` berisi Android Key Attestation dari device. Attestation itu terikat
kriptografis ke request/nomor aslinya, jadi tidak bisa dipinjam untuk nomor lain —
sudah diuji, hasilnya tetap `sms_wait: 0`. Tanpa attestation, `cek_ex.js` bisa
memastikan nomor **dikenali server** (`reason: incorrect`) tapi tidak membaca angka
cooldown asli. Untuk angka cooldown nyata, pakai `cek_wait.js` (dengan konsekuensi
ia memicu OTP).

---

## Arti Kode `reason`

| `reason`      | Penjelasan                                                        |
|---------------|-------------------------------------------------------------------|
| `too_recent`  | Nomor baru saja minta OTP; sedang cooldown. Lihat `sms_wait`.     |
| `no_routes`   | Server tidak punya rute kirim OTP ke nomor ini (format/operator tak dikenali). Sering muncul juga saat IP-mu sedang di-rem server (semua nomor balas `3600`). |
| `blocked`     | Request/IP dibatasi server. Coba dari IP lain, jangan spam.       |
| `old_version` | `WA_VERSION` terlalu lama. Setel versi yang lebih baru.           |
| `bad_token`   | Token tidak cocok dengan versi. Pastikan `WA_VERSION` selaras.    |

## Kalau nomor ditolak "nomor tidak valid" / `WA_CC`

Parser nomor (`phone`) kadang menolak nomor yang sebenarnya aktif di WhatsApp
karena aturan panjang/prefix negaranya ketat. Script tidak lagi berhenti di situ:
kalau parser gagal, ia jatuh ke tebakan kode negara 1-3 digit pertama supaya
request tetap dikirim dan server WhatsApp yang menilai. Untuk memastikan pemisahan
kode negara benar, set `WA_CC` (contoh Mali `+223` → `WA_CC=223`). Pakai `WA_DEBUG=1`
untuk melihat bagaimana nomor dipisah.

## Bedanya `too_recent` vs `3600` seragam

Kalau `sms_wait`, `voice_wait`, dan `retry_after` semuanya persis `3600` dengan
`reason: no_routes`, itu biasanya **IP-mu sedang di-rem server**, bukan cooldown
asli nomor. Cooldown asli terlihat sebagai `reason: too_recent` dengan angka yang
spesifik dan terus berkurang antar pengecekan. Coba dari IP/HP lain bila kena `3600`.

---

## Catatan Penting

- **Jangan spam request.** Terlalu sering memanggil endpoint bisa memicu `blocked`
  atau justru mengubah/memulai cooldown baru pada nomor yang dicek. Beri jeda antar
  pengecekan, apalagi untuk batch.
- Versi WhatsApp (`WA_VERSION`) harus cocok dengan token yang dihasilkan. Kalau
  server balas `bad_token` atau `old_version`, ganti ke versi yang lebih baru.
- `sms_wait` menunjukkan status cooldown registrasi di sisi server, bukan detail
  pemilik akun.
- Alat ini untuk keperluan diagnostik/riset pribadi terhadap nomor milik sendiri.
  Gunakan secara bertanggung jawab dan patuhi Ketentuan Layanan WhatsApp.
