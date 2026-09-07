// Cek cooldown OTP (sms_wait/voice_wait) suatu nomor lewat /v2/code.
// Token iOS = md5(waString + md5(versi) + nomor)  -- rumus dari Baileys commit #290,
// jadi packageMD5 ternyata cuma md5 dari STRING versi, bukan hash file IPA.
// sms_wait = detik yang harus ditunggu sebelum boleh minta OTP lagi (0 = boleh sekarang).

const { randomBytes, randomUUID, createHash } = require('crypto')
const { generateKeyPair, sign } = require('curve25519-js')
const { phone } = require('phone')

const WA_VERSION = process.env.WA_VERSION || '2.26.33.73'
const UA = `WhatsApp/${WA_VERSION} iOS/17.5.1 Device/Apple-iPhone_13`
const WA_SECRET = '0a1mLfGUIBVrMKF1RdvLI5lkRBvof6vn0fD2QRSM'

const b64url = (a) => Buffer.from(a).toString('base64url')

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

const cek = async (number, method = 'sms') => {
  number = phone('+' + number.replace(/\D/g, ''))
  if (!number.isValid) return { status: 'invalid', reason: 'nomor tidak valid' }
  const cc = number.countryCode.replace('+', '')
  const nomor = number.phoneNumber.replace(number.countryCode, '')

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
  const res = await fetch(url, { headers: { 'User-Agent': UA, Accept: 'text/json' } })
  return res.json()
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
  const r = await cek(nomor, method)
  console.log('Nomor  :', nomor, '| versi:', WA_VERSION, '| method:', method)
  console.log('Respons:', JSON.stringify(r, null, 2))
  if (typeof r.sms_wait === 'number') {
    console.log('---')
    console.log('sms_wait   :', fmt(r.sms_wait))
    console.log('voice_wait :', fmt(r.voice_wait))
    if (r.retry_after) console.log('retry_after:', fmt(r.retry_after))
  }
})()
