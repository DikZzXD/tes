// Hitung token WhatsApp ala Android (rumus dari technocode/RE-WA):
//   key   = PBKDF2-SHA1(waPrefix + bytes(about_logo.png), salt, iter=512, keylen=128)
//   data  = signature_bytes + classesMd5_bytes + phone
//   token = base64( HMAC-SHA1(key, data) )
// CATATAN: nilai salt/classesMd5/logo di bawah dari build lama (2021). Kalau server balas
// bad_token, artinya konstanta ini sudah beda dgn versi client yang di-spoof.
const { pbkdf2Sync, createHmac } = require('crypto')
const { readFileSync } = require('fs')

const WA_PREFIX = 'Y29tLndoYXRzYXBw' // base64("com.whatsapp")
const SIGNATURE =
  'MIIDMjCCAvCgAwIBAgIETCU2pDALBgcqhkjOOAQDBQAwfDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCkNhbGlmb3JuaWExFDASBgNVBAcTC1NhbnRhIENsYXJhMRYwFAYDVQQKEw1XaGF0c0FwcCBJbmMuMRQwEgYDVQQLEwtFbmdpbmVlcmluZzEUMBIGA1UEAxMLQnJpYW4gQWN0b24wHhcNMTAwNjI1MjMwNzE2WhcNNDQwMjE1MjMwNzE2WjB8MQswCQYDVQQGEwJVUzETMBEGA1UECBMKQ2FsaWZvcm5pYTEUMBIGA1UEBxMLU2FudGEgQ2xhcmExFjAUBgNVBAoTDVdoYXRzQXBwIEluYy4xFDASBgNVBAsTC0VuZ2luZWVyaW5nMRQwEgYDVQQDEwtCcmlhbiBBY3RvbjCCAbgwggEsBgcqhkjOOAQBMIIBHwKBgQD9f1OBHXUSKVLfSpwu7OTn9hG3UjzvRADDHj+AtlEmaUVdQCJR+1k9jVj6v8X1ujD2y5tVbNeBO4AdNG/yZmC3a5lQpaSfn+gEexAiwk+7qdf+t8Yb+DtX58aophUPBPuD9tPFHsMCNVQTWhaRMvZ1864rYdcq7/IiAxmd0UgBxwIVAJdgUI8VIwvMspK5gqLrhAvwWBz1AoGBAPfhoIXWmz3ey7yrXDa4V7l5lK+7+jrqgvlXTAs9B4JnUVlXjrrUWU/mcQcQgYC0SRZxI+hMKBYTt88JMozIpuE8FnqLVHyNKOCjrh4rs6Z1kW6jfwv6ITVi8ftiegEkO8yk8b6oUZCJqIPf4VrlnwaSi2ZegHtVJWQBTDv+z0kqA4GFAAKBgQDRGYtLgWh7zyRtQainJfCpiaUbzjJuhMgo4fVWZIvXHaSHBU1t5w//S0lDK2hiqkj8KpMWGywVov9eZxZy37V26dEqr/c2m5qZ0E+ynSu7sqUD7kGx/zeIcGT0H+KAVgkGNQCo5Uc0koLRWYHNtYoIvt5R3X6YZylbPftF/8ayWTALBgcqhkjOOAQDBQADLwAwLAIUAKYCp0d6z4QQdyN74JDfQ2WCyi8CFDUM4CaNB+ceVXdKtOrNTQcc0e+t'
const CLASSES_MD5 = 'ZXEimx8U9X1WZwQmVJRnGA=='
const SALT =
  'PkTwKSZqUfAUyR0rPQ8hYJ0wNsQQ3dW1+3SCnyTXIfEAxxS75FwkDf47wNv/c8pP3p0GXKR6OOQmhyERwx74fw1RYSU10I4r1gyBVDbRJ40pidjM41G1I1oN'

const buatToken = (phone, logoPath = __dirname + '/about_logo.png', classesMd5 = CLASSES_MD5) => {
  const imageBytes = readFileSync(logoPath)
  const password = Buffer.concat([Buffer.from(WA_PREFIX, 'base64'), imageBytes])
  const key = pbkdf2Sync(password, Buffer.from(SALT, 'base64'), 0x200, 0x80, 'sha1')
  const data = Buffer.concat([
    Buffer.from(SIGNATURE, 'base64'),
    Buffer.from(classesMd5, 'base64'),
    Buffer.from(phone, 'utf8'),
  ])
  return createHmac('sha1', key).update(data).digest('base64')
}

module.exports = { buatToken, CLASSES_MD5, SIGNATURE, SALT }

if (require.main === module) {
  const phone = process.argv[2] || '000000000'
  console.log('token:', buatToken(phone))
}
