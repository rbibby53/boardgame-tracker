import pytest
from pydantic import ValidationError

from app.scoring import PlayPayload, compute


def P(**kw):
    return PlayPayload.model_validate(kw)


def by_id(results):
    return {r.player_id: r for r in results}


def test_high_score_with_tie_shares_first():
    res = by_id(compute("high", P(players=[{"player_id": 1, "score": 10}, {"player_id": 2, "score": 10},
                                          {"player_id": 3, "score": 7}])))
    assert res[1].place == res[2].place == 1 and res[3].place == 3
    assert res[1].is_winner and res[2].is_winner and not res[3].is_winner


def test_low_score_from_rounds_and_override():
    payload = P(players=[{"player_id": 1}, {"player_id": 2, "score": 3}],
                rounds=[{"scores": {1: 5, 2: 20}, "won": [1]}, {"scores": {1: 4, 2: 1}}])
    res = by_id(compute("low", payload))
    assert res[1].score == 9 and not res[1].score_manual
    assert res[2].score == 3 and res[2].score_manual  # manual override beats round total (21)
    assert res[2].is_winner and res[1].rounds_won == 1


def test_winloss_most_rounds_won():
    payload = P(players=[{"player_id": 1}, {"player_id": 2}],
                rounds=[{"won": [1]}, {"won": [2]}, {"won": [2]}])
    res = by_id(compute("winloss", payload))
    assert res[2].is_winner and not res[1].is_winner
    assert res[2].place == 1 and res[1].place == 2


def test_manual_winner_overrides_scores():
    payload = P(players=[{"player_id": 1, "score": 50}, {"player_id": 2, "score": 10, "winner": True}],
                winners_manual=True)
    res = by_id(compute("high", payload))
    assert res[2].is_winner and not res[1].is_winner


def test_coop_everyone_or_no_one():
    won = compute("coop", P(players=[{"player_id": 1}, {"player_id": 2}], coop_won=True))
    lost = compute("coop", P(players=[{"player_id": 1}, {"player_id": 2}], coop_won=False))
    assert all(r.is_winner for r in won) and not any(r.is_winner for r in lost)


def test_validation():
    with pytest.raises(ValidationError):
        P(players=[])
    with pytest.raises(ValidationError):
        P(players=[{"player_id": 1}, {"player_id": 1}])
    with pytest.raises(ValidationError):
        P(players=[{"player_id": 1}], rounds=[{"scores": {9: 1}}])
