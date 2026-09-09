from sqlmystery.grade import grade

ANSWER = {
    "murderer": "Evelyn Fernandez",
    "mastermind": "Carrie Brady",
    "hops": [
        {"role": "witness", "name": "Ralph Hubbard"},
        {"role": "witness", "name": "Fiona Reed"},
        {"role": "murderer", "name": "Evelyn Fernandez"},
        {"role": "accomplice", "name": "Marie Payne"},
        {"role": "mastermind", "name": "Carrie Brady"},
    ],
}


def test_exact_match():
    g = grade(ANSWER, {"murderer": "Evelyn Fernandez", "mastermind": "Carrie Brady",
                       "chain": ["Ralph Hubbard", "Fiona Reed", "Evelyn Fernandez", "Marie Payne", "Carrie Brady"]})
    assert g == {"mastermind_correct": True, "murderer_correct": True, "chain_recall": 1.0, "chain_precision": 1.0}


def test_case_and_whitespace_insensitive():
    g = grade(ANSWER, {"murderer": "  evelyn FERNANDEZ ", "mastermind": "carrie brady", "chain": []})
    assert g["mastermind_correct"] and g["murderer_correct"]
    assert g["chain_recall"] == 0.0


def test_wrong_mastermind_partial_chain():
    g = grade(ANSWER, {"murderer": "Evelyn Fernandez", "mastermind": "Marie Payne",
                       "chain": ["Evelyn Fernandez", "Marie Payne", "Nobody Real"]})
    assert g["murderer_correct"] and not g["mastermind_correct"]
    assert g["chain_recall"] == 2 / 5
    assert g["chain_precision"] == 2 / 3


def test_empty_submission():
    g = grade(ANSWER, {})
    assert g == {"mastermind_correct": False, "murderer_correct": False, "chain_recall": 0.0, "chain_precision": 0.0}
