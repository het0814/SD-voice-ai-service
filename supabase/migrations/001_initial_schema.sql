-- 1. Enable UUID extension
create extension if not exists "uuid-ossp";

-- 2. Specialists Table (Directory)
create table specialists (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  
  -- Core Identity
  npi text unique,
  name text not null,
  specialty text not null,
  clinic_name text not null,
  phone text not null,
  
  -- Verification Status
  is_verified boolean default false,
  last_verified_at timestamptz,
  next_verification_due_at timestamptz,
  
  -- Metadata
  address text,
  city text,
  state text,
  zip_code text,
  
  -- Current Data (JSON for flexible schema)
  -- Structure: { "accepting_new_patients": true, "insurances": ["Aetna", "BCBS"] }
  current_data jsonb default '{}'::jsonb
);

-- 3. Verification Calls
create type call_status as enum (
  'queued', 
  'dispatched', 
  'ringing', 
  'connected', 
  'in_progress', 
  'completed', 
  'failed', 
  'voicemail'
);

create table verification_calls (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  
  specialist_id uuid references specialists(id) on delete cascade,
  
  -- Call Metadata
  status call_status not null default 'queued',
  direction text default 'outbound',
  twilio_sid text,
  livekit_room_id text,
  
  -- Timing
  started_at timestamptz,
  ended_at timestamptz,
  duration_seconds int,
  
  -- Content
  transcript text,
  recording_url text,
  
  -- Failure tracking
  retry_count int default 0,
  failure_reason text
);

-- 4. Data Updates (Extraction Results)
create type update_status as enum ('pending', 'approved', 'rejected');

create table data_updates (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  
  call_id uuid references verification_calls(id) on delete cascade,
  specialist_id uuid references specialists(id) on delete cascade,
  
  -- The field being updated (e.g., "accepting_new_patients")
  field_name text not null,
  
  -- Values
  old_value jsonb,
  new_value jsonb not null,
  
  -- Confidence & Review
  confidence_score float not null check (confidence_score >= 0 and confidence_score <= 1.0),
  requires_review boolean default false,
  status update_status default 'pending',
  
  -- Reviewer actions
  reviewed_at timestamptz,
  reviewed_by text, -- User ID or email
  rejection_reason text
);

-- 5. Audit Log (Immutable History)
create table audit_log (
  id uuid primary key default uuid_generate_v4(),
  happened_at timestamptz not null default now(),
  
  entity_type text not null, -- 'specialist', 'call', 'update'
  entity_id uuid not null,
  action text not null,      -- 'create', 'update', 'delete', 'verify', 'approve'
  actor text default 'system',
  
  changes jsonb -- { "old": ..., "new": ... }
);

-- Indexes
create index idx_specialists_next_verification on specialists(next_verification_due_at);
create index idx_calls_status on verification_calls(status);
create index idx_updates_pending on data_updates(status) where status = 'pending';

-- Automatic updated_at trigger
create or replace function update_updated_at_column()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language 'plpgsql';

create trigger update_specialists_modtime
    before update on specialists
    for each row execute procedure update_updated_at_column();

create trigger update_calls_modtime
    before update on verification_calls
    for each row execute procedure update_updated_at_column();

-- RLS Policies (Enable RLS, but allow service role full access)
alter table specialists enable row level security;
alter table verification_calls enable row level security;
alter table data_updates enable row level security;
alter table audit_log enable row level security;

-- For this backend service, we primarily access via service role key which bypasses RLS.
-- But it's good practice to have a policy for potential dashboard users later.
create policy "Enable read access for authenticated users" on specialists for select using (auth.role() = 'authenticated');
create policy "Enable read access for authenticated users" on verification_calls for select using (auth.role() = 'authenticated');
create policy "Enable read access for authenticated users" on data_updates for select using (auth.role() = 'authenticated');
create policy "Enable read access for authenticated users" on audit_log for select using (auth.role() = 'authenticated');
