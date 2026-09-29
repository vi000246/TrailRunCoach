import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.sync.tp_client import _parse_changed, _extract_athlete_id, _can_download


def test_changed_uses_modified_and_deleted_keys():
    """WKO5 log: 'Received list of %u deleted workouts, and %u changed or new workouts.'"""
    body = {"modified": [{"workoutId": 1}, {"workoutId": 2}], "deleted": [{"workoutId": 9}]}
    modified, deleted = _parse_changed(body)
    assert [w["workoutId"] for w in modified] == [1, 2]
    assert len(deleted) == 1


def test_changed_tolerates_bare_list_and_legacy_key():
    assert _parse_changed([{"workoutId": 1}])[0] == [{"workoutId": 1}]
    assert _parse_changed({"workouts": [{"workoutId": 3}]})[0] == [{"workoutId": 3}]
    assert _parse_changed({}) == ([], [])


def test_premium_comes_from_athlete_type():
    info = {"user": {"userId": 10, "isCoach": False,
                     "athletes": [{"athleteId": 10, "athleteType": "premium"}]}}
    aid, athletes, user_type, premium = _extract_athlete_id(info)
    assert aid == 10 and premium is True
    assert _can_download(user_type, premium)


def test_basic_athlete_cannot_download_but_coach_can():
    basic = {"user": {"userId": 10, "athletes": [{"athleteId": 10, "athleteType": "basic"}]}}
    _, _, ut, prem = _extract_athlete_id(basic)
    assert not _can_download(ut, prem)
    coach = {"user": {"userId": 5, "isCoach": True,
                      "athletes": [{"athleteId": 10, "athleteType": "basic"}]}}
    _, _, ut, prem = _extract_athlete_id(coach)
    assert _can_download(ut, prem)
