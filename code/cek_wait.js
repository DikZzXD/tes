// Cek cooldown OTP (sms_wait/voice_wait) suatu nomor lewat /v2/code.
// Token iOS = md5(waString + md5(versi) + nomor)  -- rumus dari Baileys commit #290,
// jadi packageMD5 ternyata cuma md5 dari STRING versi, bukan hash file IPA.
// sms_wait = detik yang harus ditunggu sebelum boleh minta OTP lagi (0 = boleh sekarang).

const { randomBytes, randomUUID, createHash } = require('crypto')
const { generateKeyPair, sign } = require('curve25519-js')
const { phone } = require('phone')
const { ProxyAgent, fetch } = require('undici')

// PENTING: pakai `fetch` dari paket undici, BUKAN fetch global Node. ProxyAgent dari
// paket ini punya interface handler yang beda dari undici bawaan Node -- kalau dipasang
// sebagai dispatcher ke fetch global, error "invalid onRequestStart method".

const WA_VERSION = process.env.WA_VERSION || '2.26.33.73'
const WA_SECRET = '0a1mLfGUIBVrMKF1RdvLI5lkRBvof6vn0fD2QRSM'

// Pilih device iPhone acak tiap request biar UA gak selalu identik (bikin fingerprint
// server lebih susah nge-flag pola "satu device spam banyak nomor").
const DEVICE_IOS = [
  'Apple-iPhone_13', 'Apple-iPhone_13_Pro', 'Apple-iPhone_14', 'Apple-iPhone_14_Pro',
  'Apple-iPhone_15', 'Apple-iPhone_15_Pro', 'Apple-iPhone_12', 'Apple-iPhone_SE_3',
]
const IOS_VER = ['17.5.1', '17.6.1', '18.0', '18.1.1', '16.7.8']
const acak = (arr) => arr[Math.floor(Math.random() * arr.length)]
const buatUA = () => `WhatsApp/${WA_VERSION} iOS/${acak(IOS_VER)} Device/${acak(DEVICE_IOS)}`

// Ambil daftar proxy HTTP dari raw GitHub (dikit aja utk tes). Bisa dioverride lewat
// WA_PROXY (satu proxy manual, mis. http://ip:port) yang selalu diprioritaskan.
const SUMBER_PROXY = [
  'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
  'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
]
const ambilProxyList = async (maksimal = 200) => {
  const kumpulan = []
  for (const url of SUMBER_PROXY) {
    try {
      const t = await (await fetch(url)).text()
      for (const baris of t.trim().split(/\r?\n/)) {
        const p = baris.trim()
        if (/^\d{1,3}(\.\d{1,3}){3}:\d+$/.test(p)) kumpulan.push('http://' + p)
      }
    } catch { /* sumber mati, lanjut ke sumber berikutnya */ }
    if (kumpulan.length >= maksimal * 3) break
  }
  // Acak lalu potong, biar tiap run nyoba proxy yang beda-beda.
  for (let i = kumpulan.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[kumpulan[i], kumpulan[j]] = [kumpulan[j], kumpulan[i]]
  }
  return kumpulan.slice(0, maksimal)
}

const b64url = (a) => Buffer.from(a).toString('base64url')

// Ambil daftar proxy OwlProxy (residential) dari file. Path lewat WA_OWL_FILE, default
// owl_proxies.txt (satu URI per baris). OwlProxy pakai 1 endpoint utk http & socks5,
// jadi socks5:// otomatis diubah ke http:// karena undici cuma dukung HTTP proxy.
const fs = require('fs')
const muatOwlProxy = () => {
  const file = process.env.WA_OWL_FILE || 'owl_proxies.txt'
  let isi
  try { isi = fs.readFileSync(file, 'utf8') } catch { return [] }
  const list = []
  for (const baris of isi.trim().split(/\r?\n/)) {
    const u = baris.trim()
    if (!u || u.startsWith('#')) continue
    list.push(u.replace(/^socks5:\/\//, 'http://'))
  }
  return list
}

const md5 = (s) => createHash('md5').update(s).digest('hex')
// packageMD5 = md5(versi); token = md5(secret + packageMD5 + nomor_tanpa_cc)
const buatToken = (nomor) => md5(WA_SECRET + md5(WA_VERSION) + nomor)

const buatKeyPair = () => {
  const kp = generateKeyPair(randomBytes(32))
  return { private: Buffer.from(kp.private), public: Buffer.from(kp.public) }
}
const pubKeySignal = (p) => (p.length === 33 ? p : Buffer.concat([Buffer.from([5]), p]))
const signedPreKey = (idk) => {
  const preKey = buatKeyPair()
  return { keyPair: preKey, signature: sign(idk.private, pubKeySignal(preKey.public)) }
}

// Pisah kode negara + nomor lokal. Utamakan library `phone`, tapi library ini
// suka nolak nomor yang sebenernya aktif di WA (validasi panjang/prefix-nya ketat).
// Kalau dia bilang invalid, JANGAN langsung berhenti: fallback parse manual biar
// server WhatsApp yang jadi wasit valid/tidaknya. Bisa juga paksa lewat env WA_CC.
const pisahNomor = (raw) => {
  const digits = raw.replace(/\D/g, '')
  const hasil = phone('+' + digits)
  if (hasil.isValid) {
    const cc = hasil.countryCode.replace('+', '')
    return { cc, nomor: hasil.phoneNumber.replace(hasil.countryCode, ''), sumber: 'phone' }
  }
  // Fallback manual: pakai WA_CC kalau ada, kalau nggak tebak 1-3 digit pertama.
  const ccEnv = (process.env.WA_CC || '').replace(/\D/g, '')
  if (ccEnv && digits.startsWith(ccEnv)) {
    return { cc: ccEnv, nomor: digits.slice(ccEnv.length), sumber: 'WA_CC' }
  }
  const cc = digits.slice(0, 3)
  return { cc, nomor: digits.slice(cc.length), sumber: 'tebakan (set WA_CC utk pasti)' }
}

const cek = async (number, method = 'sms', opsi = {}) => {
  const digits = number.replace(/\D/g, '')
  if (digits.length < 8) return { status: 'invalid', reason: 'nomor kependekan' }
  const { cc, nomor, sumber } = pisahNomor(number)
  if (!cc || !nomor) return { status: 'invalid', reason: 'gagal pisah kode negara' }
  if (process.env.WA_DEBUG) console.log(`[debug] cc=${cc} nomor=${nomor} (via ${sumber})`)

  const identityKey = buatKeyPair()
  const noiseKey = buatKeyPair()
  const signed = signedPreKey(identityKey)
  const regId = Buffer.alloc(4)
  regId.writeInt32BE(Uint16Array.from(randomBytes(2))[0] & 16383)
  const skeyId = Buffer.alloc(3)
  skeyId.writeInt16BE(1)

  const params = {
    cc, in: nomor, lg: 'en', lc: 'GB', mistyped: '6',
    authkey: b64url(noiseKey.public),
    e_regid: b64url(regId), e_keytype: 'BQ', e_ident: b64url(identityKey.public),
    e_skey_id: b64url(skeyId), e_skey_val: b64url(signed.keyPair.public),
    e_skey_sig: b64url(signed.signature),
    fdid: randomUUID(), expid: b64url(randomBytes(16)),
    network_radio_type: '1', simnum: '1', hasinrc: '1',
    pid: String(Math.floor(Math.random() * 9000) + 1000), rc: '0',
    id: b64url(randomBytes(20)),
    token: buatToken(nomor),
    method,
  }
  const url = 'https://v.whatsapp.net/v2/code?' + new URLSearchParams(params).toString()
  const fetchOpt = {
    headers: { 'User-Agent': opsi.ua || buatUA(), Accept: 'text/json' },
    signal: AbortSignal.timeout(opsi.timeout || 15000),
  }
  if (opsi.dispatcher) fetchOpt.dispatcher = opsi.dispatcher
  const res = await fetch(url, fetchOpt)
  return res.json()
}

// Sekali hit, dari IP langsung (tanpa proxy).
const cekLangsung = (number, method) => cek(number, method, { ua: buatUA() })

// Hit via daftar proxy sampai dapat respons yang BUKAN rate-limit/blocked. Proxy publik
// mayoritas mati/lambat, jadi kita coba banyak & ambil yang pertama kasih data valid.
const cekViaProxy = async (number, method, daftarProxy) => {
  let terakhir = null
  for (const px of daftarProxy) {
    try {
      const dispatcher = new ProxyAgent(px)
      const r = await cek(number, method, { dispatcher, timeout: 12000 })
      terakhir = { ...r, _proxy: px }
      const buruk = r.reason === 'no_routes' || r.reason === 'blocked' || r.status === 'invalid'
      if (process.env.WA_DEBUG) console.log(`[proxy] ${px} -> reason=${r.reason || r.status}`)
      if (!buruk) return { ...r, _proxy: px } // dapat cooldown asli (too_recent/incorrect dgn angka)
    } catch (e) {
      if (process.env.WA_DEBUG) console.log(`[proxy] ${px} gagal: ${e.code || e.message}`)
    }
  }
  return terakhir // semua proxy gagal/kena rate-limit; balikin yang terakhir buat info
}

// Saring proxy: uji cepat paralel ke situs netral, ambil yang benar-benar hidup & bisa
// HTTPS-tunnel. Tanpa ini kita buang waktu nyoba ratusan proxy mati satu per satu.
const saringProxyHidup = async (daftar, target = 8) => {
  const hidup = []
  const batch = 40 // uji 40 sekaligus biar cepat
  for (let i = 0; i < daftar.length && hidup.length < target; i += batch) {
    const potongan = daftar.slice(i, i + batch)
    await Promise.all(potongan.map(async (px) => {
      if (hidup.length >= target) return
      try {
        const dispatcher = new ProxyAgent(px)
        const r = await fetch('https://api.ipify.org', { dispatcher, signal: AbortSignal.timeout(7000) })
        if (r.ok) hidup.push(px)
      } catch { /* proxy mati */ }
    }))
    if (process.env.WA_DEBUG) console.log(`[saring] ${hidup.length} hidup dari ${Math.min(i + batch, daftar.length)} diuji`)
  }
  return hidup
}

// MODE CEPAT: kolam mengalir (streaming pool). Jalankan `lebar` request ke WhatsApp
// lewat proxy berbarengan; tiap kali satu selesai (sukses/gagal), langsung isi ulang
// dari antrian. Menang SEGERA saat ada proxy yang balas cooldown asli -- gak nunggu
// gelombang penuh, jadi proxy mati gak nge-block yang lain.
const cekBalapan = async (number, method, daftarProxy, lebar = 25) => {
  const bagus = (r) => r && r.reason !== 'no_routes' && r.reason !== 'blocked' && r.status !== 'invalid' && typeof r.sms_wait === 'number'
  let terakhir = null
  let idx = 0
  let selesai = false

  return await new Promise((resolveAkhir) => {
    let aktif = 0
    const majukan = () => {
      if (selesai) return
      while (aktif < lebar && idx < daftarProxy.length) {
        const px = daftarProxy[idx++]
        aktif++
        const dispatcher = new ProxyAgent(px)
        cek(number, method, { dispatcher, timeout: 5000 })
          .then((r) => {
            if (process.env.WA_DEBUG) console.log(`[balap] ${px} -> ${r.reason || r.status}`)
            terakhir = { ...r, _proxy: px }
            if (bagus(r) && !selesai) { selesai = true; resolveAkhir({ ...r, _proxy: px }) }
          })
          .catch(() => {})
          .finally(() => { aktif--; if (!selesai) majukan() })
      }
      // Semua proxy sudah dicoba dan gak ada yang aktif lagi -> balikin yang terakhir.
      if (aktif === 0 && idx >= daftarProxy.length && !selesai) {
        selesai = true
        resolveAkhir(terakhir)
      }
    }
    majukan()
  })
}

const fmt = (d) => {
  // -1 dipakai server buat nandain method-nya lagi gak dibuka sama sekali (beda dari 0 = boleh sekarang)
  if (d < 0) return 'tidak tersedia (method dinonaktifkan server)'
  if (!d) return '0 (boleh minta OTP sekarang)'
  return `${Math.floor(d / 3600)} jam ${Math.floor((d % 3600) / 60)} menit (${d} detik)`
}

;(async () => {
  const nomor = process.argv[2] || '+22378862602'
  const method = process.argv[3] || 'sms'

  // Mode proxy:
  //   WA_PROXY=http://ip:port  -> pakai proxy itu saja (manual)
  //   WA_USE_PROXY=1           -> auto ambil daftar proxy dari GitHub, coba satu per satu
  //   (kosong)                 -> langsung dari IP sandbox
  let r
  let infoProxy = 'langsung (tanpa proxy)'
  const owl = process.env.WA_OWL ? muatOwlProxy() : []
  if (owl.length) {
    // Prioritas: proxy residential OwlProxy. Jauh lebih andal dari proxy publik --
    // biasanya proxy pertama langsung dapat data asli.
    const lebar = Number(process.env.WA_PROXY_LEBAR || 10)
    console.log(`Pakai OwlProxy residential: ${owl.length} proxy (balapan ${lebar})...`)
    r = await cekBalapan(nomor, method, owl, lebar)
    if (r && r._proxy) infoProxy = 'owl ' + r._proxy.replace(/\/\/[^@]+@/, '//***@')
    if (!r) r = { status: 'error', reason: 'semua_owl_gagal' }
  } else if (process.env.WA_PROXY) {
    const px = process.env.WA_PROXY
    infoProxy = 'manual ' + px
    const dispatcher = new ProxyAgent(px)
    try {
      r = await cek(nomor, method, { dispatcher, timeout: 15000 })
    } catch (e) {
      r = { status: 'error', reason: 'proxy_gagal', detail: e.code || e.message }
    }
  } else if (process.env.WA_USE_PROXY) {
    const poolSize = Number(process.env.WA_PROXY_POOL || 300)
    const lebar = Number(process.env.WA_PROXY_LEBAR || 50)
    const maxPutaran = Number(process.env.WA_PROXY_PUTARAN || 3)
    // Proxy publik gak deterministik: kadang batch-nya jelek semua. Ulang beberapa
    // putaran dengan proxy acak baru sampai dapat data asli (too_recent/incorrect).
    const asli = (x) => x && (x.reason === 'too_recent' || x.reason === 'incorrect')
    for (let putaran = 1; putaran <= maxPutaran; putaran++) {
      process.stdout.write(`[putaran ${putaran}/${maxPutaran}] ambil proxy (pool ${poolSize}), balapan ${lebar}... `)
      const daftar = await ambilProxyList(poolSize)
      console.log(daftar.length + ' proxy.')
      const hasil = await cekBalapan(nomor, method, daftar, lebar)
      if (hasil) { r = hasil; if (hasil._proxy) infoProxy = 'proxy ' + hasil._proxy }
      if (asli(hasil)) break // dapat cooldown asli, stop
      if (putaran < maxPutaran) console.log('  -> belum dapat data asli, ulang dengan proxy baru...')
    }
    if (!r) r = { status: 'error', reason: 'semua_proxy_gagal' }
  } else {
    r = await cekLangsung(nomor, method)
  }

  console.log('Nomor  :', nomor, '| versi:', WA_VERSION, '| via:', infoProxy)
  console.log('Respons:', JSON.stringify(r, null, 2))
  const artiReason = {
    too_recent: 'Nomor baru saja minta OTP, sedang cooldown (ini yang kita cari).',
    no_routes: 'Server gak punya rute kirim OTP ke nomor ini (format/operator gak dikenali).',
    blocked: 'Request/IP diblokir server (ban/abuse). Ganti IP, jangan spam.',
    old_version: 'WA_VERSION terlalu lama, setel versi lebih baru.',
    bad_token: 'Token gak cocok dengan versi. Samakan WA_VERSION.',
    incorrect: 'Nomor dianggap belum terdaftar / format salah.',
  }
  if (r.reason && artiReason[r.reason]) console.log('Catatan:', artiReason[r.reason])

  // Kalau server balas ban/abuse, field wait-nya nggak ada -> jangan pura-pura punya cooldown.
  if (r.status === 'fail' && (r.appeal_token || r.reason === 'blocked')) {
    console.log('---')
    console.log('HASIL TIDAK VALID: nomor/IP kena blokir server, bukan cooldown OTP.')
    console.log('Cooldown asli nggak bisa dibaca dari IP ini. Pindah ke IP bersih (non-datacenter).')
    return
  }

  if (typeof r.sms_wait === 'number') {
    // Deteksi placeholder rate-limit IP: semua field wait bernilai SAMA (biasanya 3600)
    // DAN reason-nya no_routes. Kalau reason=too_recent, itu cooldown asli walau angkanya
    // kebetulan sama (mis. cooldown pendek 300 dtk) -- jangan kasih peringatan palsu.
    const waits = [r.sms_wait, r.voice_wait, r.flash_wait, r.email_otp_wait, r.send_sms_wait, r.wa_old_wait]
      .filter((v) => typeof v === 'number')
    const semuaSama = waits.length >= 3 && waits.every((v) => v === waits[0]) && waits[0] > 0
    const kemungkinanRateLimit = r.reason === 'no_routes' || (semuaSama && r.reason !== 'too_recent' && r.reason !== 'incorrect')

    console.log('---')
    console.log('sms_wait   :', fmt(r.sms_wait))
    console.log('voice_wait :', fmt(r.voice_wait))
    if (r.retry_after) console.log('retry_after:', fmt(r.retry_after))

    if (kemungkinanRateLimit) {
      console.log('---')
      console.log('PERINGATAN: reason=' + r.reason + ' dengan field wait seragam.')
      console.log('Ini nilai placeholder rate-limit IP, BUKAN cooldown asli nomor tsb.')
      console.log('Coba dari IP bersih / pakai WA_USE_PROXY=1.')
    }
  }
})()
