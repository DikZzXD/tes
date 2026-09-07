// Cek status OTP suatu nomor lewat /v2/exist -- BEDA dari cek_wait.js.
// /v2/exist cuma NGECEK status nomor & baca cooldown (sms_wait/voice_wait), TANPA
// nyuruh server kirim kode OTP beneran. Jadi aman buat ngintip masa tunggu tanpa
// mengganggu/memulai cooldown baru. (cek_wait.js pakai /v2/code yang benar2 minta OTP.)
//
// Token & material kripto-nya sama persis dgn cek_wait.js.

const { randomBytes, randomUUID, createHash } = require('crypto')
const { generateKeyPair, sign } = require('curve25519-js')
const { phone } = require('phone')

const WA_VERSION = process.env.WA_VERSION || '2.26.33.73'
const UA = `WhatsApp/${WA_VERSION} iOS/17.5.1 Device/Apple-iPhone_13`
const WA_SECRET = '0a1mLfGUIBVrMKF1RdvLI5lkRBvof6vn0fD2QRSM'

const b64url = (a) => Buffer.from(a).toString('base64url')
const md5 = (s) => createHash('md5').update(s).digest('hex')
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

// Sama seperti cek_wait.js: jangan mati kalau lib phone nolak nomor yg sebenernya valid.
const pisahNomor = (raw) => {
  const digits = raw.replace(/\D/g, '')
  const hasil = phone('+' + digits)
  if (hasil.isValid) {
    const cc = hasil.countryCode.replace('+', '')
    return { cc, nomor: hasil.phoneNumber.replace(hasil.countryCode, ''), sumber: 'phone' }
  }
  const ccEnv = (process.env.WA_CC || '').replace(/\D/g, '')
  if (ccEnv && digits.startsWith(ccEnv)) {
    return { cc: ccEnv, nomor: digits.slice(ccEnv.length), sumber: 'WA_CC' }
  }
  const cc = digits.slice(0, 3)
  return { cc, nomor: digits.slice(cc.length), sumber: 'tebakan (set WA_CC utk pasti)' }
}

const cek = async (number) => {
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

  // /v2/exist gak butuh 'method' -- kita gak minta kirim apa pun, cuma cek status.
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
  }
  const url = 'https://v.whatsapp.net/v2/exist?' + new URLSearchParams(params).toString()
  const res = await fetch(url, { headers: { 'User-Agent': UA, Accept: 'text/json' } })
  return res.json()
}

const fmt = (d) => {
  if (typeof d !== 'number') return '-'
  if (d < 0) return 'tidak tersedia (method dinonaktifkan server)'
  if (!d) return '0 (boleh minta OTP sekarang)'
  return `${Math.floor(d / 3600)} jam ${Math.floor((d % 3600) / 60)} menit (${d} detik)`
}

;(async () => {
  const nomor = process.argv[2] || '+22378862602'
  const r = await cek(nomor)
  console.log('Nomor  :', nomor, '| versi:', WA_VERSION, '| endpoint: /v2/exist (cuma cek, TIDAK kirim OTP)')
  console.log('Respons:', JSON.stringify(r, null, 2))
  const arti = {
    incorrect: 'Nomor terdaftar tapi kode belum/ tidak dicek (normal utk cek tanpa kirim).',
    too_recent: 'Nomor baru saja minta OTP, sedang cooldown.',
    no_routes: 'Server gak punya rute / IP-mu lagi di-rem (sering muncul 3600 seragam).',
    blocked: 'Request/IP diblokir server.',
    old_version: 'WA_VERSION terlalu lama.',
    bad_token: 'Token gak cocok dengan versi.',
  }
  if (r.reason && arti[r.reason]) console.log('Catatan:', arti[r.reason])
  if (typeof r.sms_wait === 'number') {
    console.log('---')
    console.log('sms_wait   :', fmt(r.sms_wait))
    console.log('voice_wait :', fmt(r.voice_wait))
  }
})()
