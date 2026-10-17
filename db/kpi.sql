-- North Star: Comprehended Decision Rate (CDR), pilot window
with per_decision as (
  select d.id, d.recall_correct,
         count(*) filter (where v.dwell_ms >= 4000) as long_panels
  from public.decisions d
  join public.copilot_sessions s on s.id = d.session_id and s.variant = 'treatment'
  left join public.copilot_panel_views v on v.session_id = d.session_id
  where d.recall_answered
  group by d.id, d.recall_correct
)
select count(*) as decisions_with_recall,
       round(100.0 * count(*) filter (where long_panels >= 2 and recall_correct) / nullif(count(*), 0), 1) as cdr_pct
from per_decision;

-- Avoidance lift: share of attempts with the SIP still contributing at 90 days, by arm
select s.variant,
       count(*) as attempts,
       round(100.0 * avg(c.sip_active_at_90d::int), 1) as pct_contributing_90d
from public.copilot_sessions s
join public.decisions d on d.session_id = s.id
join public.checkins c on c.decision_id = d.id
where s.variant in ('treatment', 'holdout') and c.sip_active_at_90d is not null
group by s.variant;

-- Regret rate among users who paused, reduced or stopped, by arm
select s.variant,
       round(100.0 * avg((coalesce(c.regret_reported, false) or coalesce(c.restarted_within_90d, false))::int), 1) as
           regret_pct
from public.copilot_sessions s
join public.decisions d on d.session_id = s.id and d.choice in ('pause', 'reduce', 'stop')
join public.checkins c on c.decision_id = d.id
where c.responded_at is not null or c.restarted_within_90d is not null
group by s.variant;

-- Skip rate (treatment) and validator fallback rate
select round(100.0 * count(*) filter (where skipped_at_panel is not null) / nullif(count(*), 0), 1) as skip_rate_pct
from public.copilot_sessions where variant = 'treatment';

select round(100.0 * count(*) filter (where served = 'template') / nullif(count(*), 0), 1) as fallback_rate_pct
from public.copilot_captions;
