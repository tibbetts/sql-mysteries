"""Turn a chain into crime scene report text and interview transcripts."""
from __future__ import annotations

import random

from .chain import Chain, Clue, Hop
from .db import Db
from .predicates import REGISTRY, Ctx, DirtyFlags
from .world import WorldInfo

ORDINALS = ["first", "second", "third", "fourth"]
SUBJECTS = ["This person", "The same person", "This individual"]


def _sentences(rng: random.Random, lead: str, clues: list[Clue]) -> str:
    parts = [f"{lead} {clues[0].rendered}."]
    for c in clues[1:]:
        parts.append(f"{rng.choice(SUBJECTS)} {c.rendered}.")
    return " ".join(parts)


def _report(rng: random.Random, chain: Chain) -> str:
    witnesses = chain.witnesses
    intro = rng.choice([
        f"Security footage shows that there were {len(witnesses)} witnesses.",
        f"Responding officers located {len(witnesses)} witnesses who have not yet given statements.",
    ])
    parts = [intro]
    for i, w in enumerate(witnesses):
        parts.append(_sentences(rng, f"The {ORDINALS[i]} witness", w.clues))
    return " ".join(parts)


def _witness_transcript(rng: random.Random, clues: list[Clue]) -> str:
    opener = rng.choice([
        "I heard a gunshot and then saw someone run out.",
        "I saw the whole thing from my window.",
        "I was walking home when it happened.",
    ])
    if not clues:
        return opener + " I did not get a good look, sorry."
    return f"{opener} {_sentences(rng, 'The person I saw', clues)}"


def _hired_transcript(rng: random.Random, role: str, clues: list[Clue]) -> str:
    if role == "murderer":
        opener = rng.choice([
            "Fine. I was hired to do it. I never got a name.",
            "I did it, but somebody paid me to. I do not know who they really are.",
        ])
        lead = "The person who hired me"
    else:
        opener = rng.choice([
            "I only passed the money and the instructions along. I never asked questions.",
            "I was a go-between, nothing more. The orders came from someone else.",
        ])
        lead = "The one giving the orders"
    return f"{opener} {_sentences(rng, lead, clues)}"


def write_narrative(db: Db, rng: random.Random, chain: Chain, info: WorldInfo) -> None:
    ctx = Ctx(info=info, dirty=chain.dirty, known=[])
    chain_ids = [h.person_id for h in chain.hops]
    db.executemany("DELETE FROM interview WHERE person_id=?", [(pid,) for pid in chain_ids])

    # crime scene report for the murder
    db.execute("INSERT INTO crime_scene_report VALUES (?,?,?,?)",
               (info.crime_date, "murder", _report(rng, chain), info.crime_city))

    # transcripts: each speaker states the clues attributed to them
    by_speaker: dict[int, list[tuple[Hop, Clue]]] = {}
    for hop in chain.hops:
        for c in hop.clues:
            if c.speaker is not None:
                by_speaker.setdefault(c.speaker, []).append((hop, c))
    role_of = {h.person_id: h.role for h in chain.hops}
    for hop in chain.hops:
        pid = hop.person_id
        items = by_speaker.get(pid, [])
        clues = [c for _, c in items]
        if hop.role == "witness":
            text = _witness_transcript(rng, clues)
        elif hop.role == "mastermind":
            text = rng.choice([
                "I have nothing to say without my lawyer present.",
                "You have no idea who you are dealing with.",
            ])
        else:
            text = _hired_transcript(rng, hop.role, clues)
        db.execute("INSERT INTO interview VALUES (?,?)", (pid, text))

    # rumour transcripts: random people repeating plausible but unrelated clue-shaped facts
    n_rumours = max(20, min(500, db.one("SELECT count(*) FROM person")[0] // 200))
    pool = db.ids("SELECT id FROM person WHERE id NOT IN (SELECT person_id FROM interview) ORDER BY id")
    kinds = [c for c in REGISTRY.values() if not c.needs_known and not c.exclusive]
    for pid in rng.sample(pool, min(n_rumours, len(pool))):
        subject = rng.choice(pool)
        rctx = Ctx(info=info, dirty=chain.dirty, known=[], reserved=set())
        pred = rng.choice(kinds).sample(db, rng, subject, rctx)
        text = rng.choice([
            f"People are saying the killer {pred.text(rng, rctx)}. I do not know if that is true.",
            f"My cousin swears the person responsible {pred.text(rng, rctx)}.",
            f"I heard a rumour that whoever did it {pred.text(rng, rctx)}.",
        ])
        db.execute("INSERT INTO interview VALUES (?,?)", (pid, text))
    db.commit()
