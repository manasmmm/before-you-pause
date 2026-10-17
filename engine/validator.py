"""Caption validator: slot-fill, numeric grounding and language lint. Fails closed."""
import math, re

BANNED = [r"\byou should\b", r"\byou must\b", r"\bwe recommend\b", r"\bwe suggest\b", r"\byou need to\b",
          r"\bconsider (pausing|continuing|stopping|switching|redeeming)\b", r"\bbest to\b", r"\bbetter to\b",
          r"\bdon'?t stop\b", r"\bdo not stop\b", r"\bguarantee[sd]?\b", r"\bwill recover\b", r"\brisk[- ]free\b",
          r"\bsafe\b", r"\bdefinitely\b", r"\bcertain(ly)?\b", r"\bexpected to (rise|grow|rebound)\b",
               r"\bpredict(s|ed)?\b"]
BANNED_HI = ["\u091a\u093e\u0939\u093f\u090f", "\u0938\u0932\u093e\u0939", "\u0917\u093e\u0930\u0902\u091f\u0940",
    "\u0938\u0941\u0930\u0915\u094d\u0937\u093f\u0924", "\u0928\u093f\u0936\u094d\u091a\u093f\u0924",
    "\u091c\u093c\u0930\u0942\u0930", "\u091c\u0930\u0942\u0930"]   # should / advice / guarantee / safe / certain / definitely
UNIT = {"": 1, "k": 1e3, "l": 1e5, "lakh": 1e5, "cr": 1e7, "crore": 1e7}
NUM = re.compile(r"(?:₹\s?)?(\d[\d,]*(?:\.\d+)?)\s?(lakh|crore|cr|l|k)?\b(?!\w)", re.I)
SLOT = re.compile(r"\{\{([a-z0-9_.]+)\}\}")


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix[:-1]] = obj
    elif isinstance(obj, str):
        out[prefix[:-1]] = obj
    return out


def allowed_numbers(payload) -> list[float]:
    vals = []
    for v in flatten(payload).values():
        if isinstance(v, (int, float)):
            vals.append(float(v))
        else:
            vals += [float(x.replace(",", "")) for x in re.findall(r"\d+(?:\.\d+)?", v)]
    return vals


def fmt_inr(x: float) -> str:
    n = str(round(x)); head, tail = n[:-3], n[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return "₹" + (head + "," + tail if head else tail)


def render_slots(text: str, payload: dict) -> str:
    flat = flatten(payload)
    def sub(m):
        key = m.group(1)
        if key not in flat:
            raise ValueError(f"unknown slot {key}")
        v = flat[key]
        if key.endswith("_inr"):
            return fmt_inr(v)
        if key.endswith("_pct"):
            return f"{v:g}%"
        return f"{v:g}" if isinstance(v, (int, float)) else str(v)
    return SLOT.sub(sub, text)


def validate(text: str, payload: dict, lang: str = "en") -> dict:
    reasons = []
    try:
        rendered = render_slots(text, payload)
    except ValueError as e:
        return {"ok": False, "reasons": [str(e)], "text": None}
    for pat in BANNED:
        if re.search(pat, rendered, re.I):
            reasons.append(f"banned phrase: {pat}")
    if lang == "hi":
        reasons += [f"banned word: {w}" for w in BANNED_HI if w in rendered]
    allowed = allowed_numbers(payload)
    for m in NUM.finditer(rendered):
        v = float(m.group(1).replace(",", "")) * UNIT[(m.group(2) or "").lower()]
        if not any(math.isclose(v, a, abs_tol=max(0.5, 0.005 * abs(a) if a > 1000 else 0.06)) for a in allowed):
            reasons.append(f"ungrounded number: {m.group(0).strip()}")
    return {"ok": not reasons, "reasons": reasons, "text": rendered if not reasons else None}
