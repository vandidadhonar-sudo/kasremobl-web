-- ============================================================
-- kasrmobilya — Supabase kurulumu (bir kez çalıştırın)
-- Dashboard > SQL Editor > New query > yapıştır > Run
-- Betik idempotenttir (birden fazla kez çalıştırılabilir).
-- ============================================================

-- 1) products: herkese açık OKUMA, yazma yalnızca gizli anahtarla (bot)
alter table public.products enable row level security;

drop policy if exists "public_read_products" on public.products;
create policy "public_read_products"
  on public.products for select
  to anon, authenticated using (true);

-- 2) Depolama: ürün görselleri herkese açık okunabilir
update storage.buckets set public = true where id = 'furniture_images';

drop policy if exists "public_read_furniture_images" on storage.objects;
create policy "public_read_furniture_images"
  on storage.objects for select
  to anon, authenticated using (bucket_id = 'furniture_images');

-- ============================================================
-- 3) Sunucusuz bot için oturum durumu tabloları
--    (yalnızca gizli servis anahtarı erişir; herkese açık politika yok)
-- ============================================================

create table if not exists public.bot_sessions (
  chat_id     bigint primary key,
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);
alter table public.bot_sessions enable row level security;

create table if not exists public.bot_uploads (
  id          bigint generated always as identity primary key,
  chat_id     bigint not null,
  url         text not null,
  created_at  timestamptz not null default now()
);
create index if not exists bot_uploads_chat_idx on public.bot_uploads (chat_id, created_at);
alter table public.bot_uploads enable row level security;

-- NOT: bot_sessions ve bot_uploads için anon/authenticated politikası YOK.
-- Böylece bu tablolara yalnızca botun gizli servis anahtarı erişebilir.
