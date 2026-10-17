"""Deterministic co-pilot engine. No LLM code lives here."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import median
import math


def monthly_rate(annual: float) -> float:
    return (1 + annual) ** (1 / 12) - 1


def skipped_fv(per_month: float, n: int, annual: float, horizon: int) -> float:
    """Future value at goal date of n skipped instalments (contributions at month end)."""
    r = monthly_rate(annual)
    return sum(per_month * (1 + r) ** (horizon - 1 - k) for k in range(n))


def project_corpus(corpus: float, other_sip: float, this_sip: float, annual: float,
                   horizon: int, change_months: int = 0, changed_amount: float = 0.0) -> float:
    r = monthly_rate(annual)
    for k in range(horizon):
        corpus *= 1 + r
        corpus += other_sip + (changed_amount if k < change_months else this_sip)
    return corpus


def months_to_goal(corpus: float, other_sip: float, this_sip: float, annual: float, target: float,
                   change_months: int = 0, changed_amount: float = 0.0, cap: int = 240) -> int:
    r, k = monthly_rate(annual), 0
    while corpus < target and k < cap:
        corpus *= 1 + r
        corpus += other_sip + (changed_amount if k < change_months else this_sip)
        k += 1
    return k


def diversification(fund_values: list[float], asset_classes: int) -> float:
    """0-100. Half fund-level spread (1-HHI, normalised), half asset-class spread (classes/3, capped)."""
    n, total = len(fund_values), sum(fund_values)
    hhi = sum((v / total) ** 2 for v in fund_values)
    fund_spread = 0.0 if n == 1 else (1 - hhi) / (1 - 1 / n)
    asset_spread = min(1.0, asset_classes / 3)
    return 100 * (0.5 * fund_spread + 0.5 * asset_spread)


def drawdown_context(current_decline_pct: float, past_monthly_declines_pct: list[float], min_history: int = 12):
    """Share (0-100) of past monthly declines that were LARGER than the current one. None if thin history."""
    if len(past_monthly_declines_pct) < min_history:
        return None
    larger = sum(1 for d in past_monthly_declines_pct if d > current_decline_pct)
    return 100 * larger / len(past_monthly_declines_pct)


@dataclass
class Episode:
    breach: date
    recovered: date | None


def recovery_episodes(navs: list[tuple[date, float]], threshold: float = 0.06) -> list[Episode]:
    """Episodes = NAV falls >= threshold below a running peak (breach date) until NAV regains that peak."""
    eps, peak, in_ep, breach = [], navs[0][1], False, None
    for d, v in navs:
        if not in_ep:
            if v >= peak:
                peak = v
            elif (peak - v) / peak >= threshold:
                in_ep, breach = True, d
        elif v >= peak:
            eps.append(Episode(breach, d))
            in_ep, peak = False, v
    if in_ep:
        eps.append(Episode(breach, None))
    return eps


def recovery_stats(eps: list[Episode], today_in_episode: bool, min_completed: int = 3):
    """Completed episodes only; the current open episode is excluded. Suppress if history is thin."""
    done = [(e.recovered - e.breach).days for e in eps if e.recovered]
    if len(done) < min_completed:
        return None
    return {"n": len(done), "median_days": round(median(done)), "min_days": min(done), "max_days": max(done)}


WEIGHTS = {"goal": 0.35, "horizon": 0.25, "diversification": 0.20, "drawdown": 0.20}


def confidence_score(goal: float, horizon: float, div: float, dd: float | None) -> dict:
    """Inputs are 0-100. If drawdown history is thin, that component is dropped and weights renormalise."""
    parts = {"goal": goal, "horizon": horizon, "diversification": div}
    if dd is not None:
        parts["drawdown"] = dd
    wsum = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / wsum
    band = "hold" if score >= 75 else "shifted" if score >= 50 else "changed"
    return {"parts": parts, "score": round(score), "raw": round(score, 2), "band": band, "limited_history": dd is None}
