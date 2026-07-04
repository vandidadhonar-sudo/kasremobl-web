// kasrmobilya — AI satış danışmanı proxy'si (Gemini)
// Not: GEMINI_API_KEY, Google AI Studio'dan alınan "AIza..." ile başlayan
// geçerli bir API anahtarı olmalıdır. Aksi halde Gemini 401 döndürür.
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

    try {
        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`,
            {
                method: 'POST',
                // Yeni "AQ." auth anahtarları ve eski "AIza" anahtarları için
                // Google'ın önerdiği yöntem: x-goog-api-key başlığı.
                headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
                body: JSON.stringify({
                    system_instruction: { parts: [{ text: systemPrompt }] },
                    contents: [{ parts: [{ text: String(text).slice(0, 2000) }] }],
                    // Kısa, tutarlı ve maliyet dostu yanıtlar (ücretsiz kotayı korur)
                    generationConfig: {
                        temperature: 0.5,
                        maxOutputTokens: 400,
                        topP: 0.9
                    }
                })
            }
        );

        const data = await response.json();

        if (data.error) {
            // Gerçek nedeni sunucu loglarına yaz; istemciye kısa mesaj dön.
            console.error('Gemini error:', response.status, data.error);
            return res.status(response.status || 400).json({ error: data.error.message || 'AI hatası' });
        }

        res.status(200).json(data);
    } catch (error) {
        console.error('chat proxy error:', error);
        res.status(500).json({ error: 'Server error: ' + error.message });
    }
}
