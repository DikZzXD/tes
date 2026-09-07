// Uji blob ENC hasil capture (dari snippet user) apa masih hidup & apa terikat nomor.
// ENC ini sudah berisi token valid dari device asli, jadi bisa dites langsung tanpa regenerate.
const ENC =
  'KeHhn25QP7dedf0a2WcpjLoUS2E3Mz_I60tq6-VUpW6PDTsh3B3ydg-suE8jSj4yF0G2kZ-HcAA-DRmEhIBySZtS-CsiA6qk7GFDj1Ky71Dp-sY-jHgmxUgEOxNW_nSmH_yWVsnEwFSk3nV0SJpSvtLwYHVktydhKjTXyhZ1cUg8g4Am9kcWYetNZRA8jLbw9ldQOQAk-yCQpQScezniP5K7Erjvy1bJcKTq1olLxfmWy1kTepLvA4M9UGk8ZdFQeEdSh30fnZsgoJ97qbjAMtdvkyBxQzD0tRuznlztYTtnNnrcIiu0y4tK0qaRDI-Q3XQo1qCb0hsRecs2OdKK442tBUTKmenopxv7F_Yw_CcPWuWHIpvTdibHA-DF83lNSvg5N301DEnhxJ1CWzQTwqmMA8XhIlhB2BJsw3HZpBdOcUXI532ai6nVIg1fkLhoER_BaRUVlZ1R5ADnsOboZV5uiDgqA4kUJXav-ozZUJUMb0WxTpWq6fJQrM9LYpWRUMjUnWA4OhB175C-pdt_4TR_IM98Nnk73P6S6MTjf_ahRznQ_arita88TNmGnnF3yszTmzl4hbFefPK1onxgPFTi-IkyOtD3ODh30ZH6sLLUBlMm1Owg8U79B9wuqCLodnd-l373fQJ3ymZFzD9b2odeSfPzxFi5QRco8gulvRFguK1HqsFOIRX3URxUG8hPfJbNE87WvEnReMVC2ljuO-iRwy--czoyeohd7wtSnEdi6s09_doWRiGIHpkzSpKbaKOEWN5KRMfFxzu9-JTlMcCePAXDX569DnHD3wLQURqaJwyN5x57oVIF3Osddyomr3_n0cqPVzY_GrU0gwysDFRdmaamvjM1FgVIsA5OKHGJNdtrfKuXm0byr9uyqhPHmF2PfCXI54T9m_uHPhOr0fadTCfoFuEAI_0emBGPsP2x51J2c9usfigdIoYnWTG3bvdRkOZhUpLF1Umfej76KnVwiNHeEQBHC1LxTSlXM1ad34DCZqHEGsbWGL3TEw1NOYUxSTYuOTuiIluOejTpk3pX4P8fqPBpPNAO0djtczyVaRXAzU9MbBbAINrOUQ7EJWF0aNNbXnHBx5mPXhT4lPF9uy3edxviNxTJ2Q2I2roe6cIKM6DSN-V4hDdxWSahVtw0nSNeqEyq9azvt2KCGsUaeWeFhCsNzmpoNjgX_b8i0lqNRQQ6tKr6pRDI9MPMLUqfA5_N'

const PROXY_URL = 'https://us-central1-propane-fusion-170222.cloudfunctions.net/s'

;(async () => {
  const path = process.argv[2] || '/v2/code'
  const qs = new URLSearchParams({ _: path, ENC, H: 'eyJlcnJvckNvZGUiOjEwMDF9' })
  const res = await fetch(PROXY_URL + '?' + qs.toString(), {
    headers: {
      'User-Agent': 'WhatsApp/2.26.33.73 SMB iOS/27.0 Device/iPhone_13_Pro_Max',
      priority: 'u=3',
      'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
    },
  })
  console.log('endpoint:', path)
  console.log(await res.text())
})()
