-- CD ROOT: 완료된 검토 결과를 저장하는 테이블.
-- 원본 CSV 행은 저장하지 않는다. 요약된 증거, 가설 점수, AI 주장·반박, 보고서만 저장한다.
create table if not exists public.investigations (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  session_id    text not null,                          -- 방문자 구분용 임의 값
  dataset_label text not null,                          -- 'demo:orders.csv' 또는 'upload'
  n_rows        integer,
  n_cols        integer,
  case_name     text,
  main_metric   text,
  mode          text not null check (mode in ('llm', 'rule')),   -- llm: AI 토론 + 코드 검증, rule: 규칙 기반
  top_id        text,
  top_title     text,
  top_kind      text,
  top_score     numeric,
  strength      text,
  hypotheses    jsonb not null default '[]'::jsonb,
  claims        jsonb not null default '[]'::jsonb,
  rebuttals     jsonb not null default '[]'::jsonb,
  report        text,
  tokens        integer,
  seconds       numeric,
  warnings      jsonb not null default '[]'::jsonb
);

create index if not exists investigations_session_idx on public.investigations (session_id, created_at desc);

-- 행 수준 보안을 켠다. 정책을 만들지 않으므로 anon/authenticated 키로는 읽기·쓰기가 모두 막힌다.
-- 서버(Streamlit)가 service_role 키로만 접근한다. 그 키는 Secrets에만 두고 브라우저에는 보내지 않는다.
alter table public.investigations enable row level security;

comment on table public.investigations is 'CD ROOT 검토 결과 (요약만, 원본 행 없음)';
