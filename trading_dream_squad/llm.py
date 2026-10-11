"""Appels au modèle Claude et extraction JSON robuste, plus un faux LLM (règles) pour le dry-run."""
from __future__ import annotations

import json
import os
import re


def extract_json(text):
    """Premier objet JSON trouvé dans `text` (avec ou sans bloc ```), sinon None."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for candidate in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else ""):
        if not candidate:
            continue
        try:
            v = json.loads(candidate)
            if isinstance(v, dict):
                return v
        except Exception:
            continue
    return None


class LLM:
    """Client Anthropic minimal. Compte les tokens pour que le coût réel soit visible dans le journal."""

    def __init__(self, model: str, client=None, temperature=None):
        if client is None:
            import anthropic  # importé ici pour que le dry-run n'en ait pas besoin
            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.model = model
        self.temperature = temperature if temperature is not None else (
            float(os.environ["SQUAD_TEMPERATURE"]) if os.environ.get("SQUAD_TEMPERATURE") else None)
        self.in_tokens = self.out_tokens = self.calls = 0

    def call(self, role: str, system: str, user: str, payload=None, max_tokens: int = 800) -> str:
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.messages.create(**kwargs)
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u is not None:
            self.in_tokens += getattr(u, "input_tokens", 0) or 0
            self.out_tokens += getattr(u, "output_tokens", 0) or 0
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")

    def ask_json(self, role: str, system: str, user: str, payload=None, max_tokens: int = 800):
        data = extract_json(self.call(role, system, user, payload, max_tokens))
        if data is None:  # une seule relance, ensuite l'appelant échoue du côté prudent
            nudge = user + "\n\nYour previous reply was not valid JSON. Reply with ONE JSON object only."
            data = extract_json(self.call(role, system, nudge, payload, max_tokens))
        return data


class StubLLM:
    """Agents à règles fixes, aucun appel réseau (voir rules.py)."""

    model = "rules"
    in_tokens = out_tokens = calls = 0

    def ask_json(self, role, system, user, payload=None, max_tokens=800):
        from rules import RULES
        return RULES[role](payload or {})
