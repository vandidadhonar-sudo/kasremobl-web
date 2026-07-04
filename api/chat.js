// kasrmobilya — AI satış danışmanı proxy'si (Gemini)
// GEMINI_API_KEY, Google AI Studio'dan alınan geçerli bir anahtar olmalıdır
// (yeni "AQ." veya eski "AIza" formatı). x-goog-api-key başlığıyla gönderilir.

const RETRYABLE = new Set([429, 500, 502, 503, 504]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export default async function handler(req, res) {
    if (req.method !== 'POST') return res.status(405).json({ error: 'Only POST allowed' });

    const { text, systemPrompt } = req.body || {};
    if (!text || !systemPrompt) {
        return res.status(400).json({ error: 'Eksik istek verisi.' });
    }

    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
        return res.status(500).json({ error: 'GEMINI_API_KEY tanımlı değil (Vercel > Environment Variables).' });
    }

    const MODEL = process.env.GEMINI_MODEL || 'gemini-2.5-flash-lite';
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
    const payload = JSON.stringify({
        system_instruction: { parts: [{ text: systemPrompt }] },
        contents: [{ parts: [{ text: String(text).slice(0, 2000) }] }],
        // Kısa, olgusal ve maliyet dostu yanıtlar (ücretsiz kotayı korur)
        generationConfig: { temperature: 0.3, maxOutputTokens: 400, topP: 0.9 }
    });

    // Geçici hatalarda (503 yoğunluk, 429 kota vb.) otomatik tekrar dene.
    // Böylece müşteri çoğu zaman geçici Google kesintilerini hiç görmez.
    const MAX_ATTEMPTS = 3;
    let lastStatus = 500;
    let lastError = 'AI şu an yanıt veremiyor';

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
                body: payload
            });
            const data = await response.json();

            if (response.ok && !data.error) {
                return res.status(200).json(data);
            }

            lastStatus = (data.error && data.error.code) || response.status || 500;
            lastError = (data.error && data.error.message) || 'AI hatası';
            console.error(`Gemini error (attempt ${attempt}):`, lastStatus, lastError);

            // Yeniden denenebilir değilse (ör. 401 geçersiz anahtar) hemen çık.
            if (!RETRYABLE.has(Number(lastStatus)) || attempt === MAX_ATTEMPTS) break;
        } catch (error) {
            lastStatus = 500;
            lastError = 'Server error: ' + error.message;
            console.error(`chat proxy error (attempt ${attempt}):`, error);
            if (attempt === MAX_ATTEMPTS) break;
        }
        await sleep(700 * attempt); // 0.7s, 1.4s artan bekleme
    }

    return res.status(Number(lastStatus) || 500).json({ error: lastError });
}
