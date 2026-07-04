// kasrmobilya — Supabase "uyanık tut" ucu.
// Vercel Cron her gün bunu çağırır; küçük bir veritabanı okuması yaparak
// projeyi aktif tutar, böylece ücretsiz plan 1 haftalık hareketsizlikten
// dolayı duraklamaz. Herhangi bir kullanıcı işlemi gerektirmez.
export default async function handler(req, res) {
    const url = process.env.SUPABASE_URL || "https://ooklzhsnzovfnmzdupoq.supabase.co";
    // Genel (public) anon anahtar — zaten index.html içinde herkese açıktır.
    const anon = process.env.SUPABASE_ANON_KEY ||
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9va2x6aHNuem92Zm5temR1cG9xIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI5MDcwODEsImV4cCI6MjA5ODQ4MzA4MX0.YPRQUSUmC0_D7AhIYlh-SxAuZ4817Q6rywtr5gImnTs";
    try {
        const r = await fetch(`${url}/rest/v1/products?select=id&limit=1`, {
            headers: { apikey: anon, Authorization: `Bearer ${anon}` }
        });
        await r.text();
        return res.status(200).json({ ok: true, status: r.status, ts: new Date().toISOString() });
    } catch (error) {
        return res.status(200).json({ ok: false, error: error.message });
    }
}
