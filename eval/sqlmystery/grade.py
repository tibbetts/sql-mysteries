"""Score a submission against the hidden answer."""
from __future__ import annotations

import re


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def grade(answer: dict, submission: dict) -> dict:
    truth = [_norm(h["name"]) for h in answer["hops"]]
    sub_chain = [_norm(x) for x in (submission.get("chain") or []) if _norm(x)]
    hit = [x for x in sub_chain if x in truth]
    return {
        "mastermind_correct": _norm(submission.get("mastermind")) == _norm(answer["mastermind"]),
        "murderer_correct": _norm(submission.get("murderer")) == _norm(answer["murderer"]),
        "chain_recall": len(set(hit)) / len(truth) if truth else 0.0,
        "chain_precision": len(hit) / len(sub_chain) if sub_chain else 0.0,
    }
