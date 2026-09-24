"""Moderator-mode content must expose only the shared campus identity."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.schemas import AnswerIn, PostIn, profile_out, user_card  # noqa: E402


class Actor:
    id = 999
    handle = "loyola_moderator"
    full_name = "Loyola Moderator"
    photo_path = None
    rep_tier = "Trusted"
    rep_total = 9000
    batch_year = 2024
    department = "Administration"
    course = None
    is_moderator = True



class Student:
    id = 1000
    handle = "student_handle"
    full_name = "Student Real Name"
    photo_path = None
    rep_tier = "Newcomer"
    rep_total = 0
    batch_year = None
    department = None
    course = None
    is_moderator = False
    bio = ""
    interests = []
    links = {}
    tier = 2
    status = "active"
    display_year = ""
    rep_academic = rep_build = rep_service = rep_participation = rep_conduct = 0
    created_at = None


def privacy_checks() -> None:
    answer = AnswerIn(body="This is a moderator answer.", as_moderator=True)
    if not answer.as_moderator:
        raise AssertionError("answer contract must accept moderator identity selection")
    public = user_card(Student())
    if public["handle"] != "student_handle" or public["full_name"] != "@student_handle":
        raise AssertionError("regular users must receive only a handle, never a real name")
    private = user_card(Student(), viewer_is_moderator=True)
    if private["full_name"] != "@student_handle":
        raise AssertionError("moderators must also see handles in social surfaces")
    public_profile = profile_out(Student(), viewer_is_moderator=False)
    if public_profile["full_name"] != "@student_handle":
        raise AssertionError("public profiles must never expose the verification name")

def main() -> int:
    request = PostIn(kind="text", body="Campus update", as_moderator=True)
    if not request.as_moderator:
        raise AssertionError("post contract must accept moderator identity selection")
    card = user_card(Actor())
    if card["id"] is not None or card["handle"] != "mod" or not card["anonymous"]:
        raise AssertionError("moderator actor must not expose a recoverable account identity")
    privacy_checks()
    print('ok   moderator post selection, answer selection, and privacy contract hold')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
