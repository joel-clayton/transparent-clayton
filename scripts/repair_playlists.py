"""Repair YouTube playlist bucketing (TRA-157).

The pre-TRA-155 bug filed some meetings' videos under the wrong meeting type's
playlist (non-City-Council videos landed in City Council playlists). TRA-155
stops new misfiling; this one-time tool cleans up what already happened:

  * move a video that sits in the wrong type's playlist into the correct
    per-type/year playlist (creating that playlist if needed),
  * remove dead "Deleted video" / "Private video" placeholder items,
  * drop stale ``video_playlist.<type>.<year>`` Redis pointers that point at
    another type's playlist.

Read-only by default — it only changes anything with ``--execute``:

    python scripts/repair_playlists.py              # audit (read-only)
    python scripts/repair_playlists.py --execute    # perform the repair

Videos listed in ``SKIP_VIDEO_IDS`` are left untouched for a human to delete
(superseded copies — deletions are the operator's call, not this tool's).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from googleapiclient.discovery import build  # noqa: E402

from celery_app import r  # noqa: E402
from src.meeting_types import MEETING_TYPES, MeetingType  # noqa: E402
from src.processors.helpers.google_auth import load_credentials  # noqa: E402
from src.processors.upload_video import (  # noqa: E402
    CLIENT_SECRETS_FILE,
    MAX_UPLOADS_PAGES,
    RESULT_COUNT,
    SCOPES,
    YOUTUBE_TOKEN_FILE,
)
from src.util import get_year_string_from_string  # noqa: E402

# Obsolete/superseded videos to leave in place (the operator deletes these):
# the General-titled 2026-05-26 copies of a meeting that is canonically Planning
# Commission (its correct PC videos are moved by this tool).
SKIP_VIDEO_IDS = {"DzhdARPh05s", "EN5TG9pFS-Q"}

PLACEHOLDER_TITLES = {"Deleted video", "Private video"}


def _service() -> Any:
    creds = load_credentials(
        scopes=SCOPES,
        client_secret_path=CLIENT_SECRETS_FILE,
        token_path=YOUTUBE_TOKEN_FILE,
    )
    return build("youtube", "v3", credentials=creds)


def owner_type(playlist_title: str) -> MeetingType | None:
    """The meeting type a playlist belongs to, from its title, or None if the
    title isn't a ``<year> <display> Meetings`` playlist this pipeline manages."""
    year = get_year_string_from_string(playlist_title)
    if not year:
        return None
    for mt in MEETING_TYPES:
        if playlist_title == mt.playlist_name_template.format(year):
            return mt
    return None


def video_type(video_title: str) -> MeetingType | None:
    """The meeting type a video belongs to, from its title prefix, or None."""
    for mt in MEETING_TYPES:
        if mt.compressed_title_prefix in video_title:
            return mt
    return None


def classify_item(
    owner: MeetingType, video_title: str, video_id: str | None
) -> tuple[str, MeetingType | None]:
    """How to handle one playlist item in a playlist owned by ``owner``.

    Returns (action, dest_type) where action is one of:
      "keep"   — belongs here (or type unknown), leave it;
      "remove" — dead placeholder, delete the item;
      "skip"   — an explicitly-skipped (obsolete) video, leave for manual deletion;
      "move"   — misfiled; move to dest_type's playlist for the same year.
    """
    if video_title in PLACEHOLDER_TITLES:
        return ("remove", None)
    if video_id in SKIP_VIDEO_IDS:
        return ("skip", None)
    vtype = video_type(video_title)
    if vtype is None or vtype.key == owner.key:
        return ("keep", None)
    return ("move", vtype)


def _all_playlists(service: Any) -> dict[str, str]:
    """{playlist_id: title} for every playlist on the channel (all pages)."""
    out: dict[str, str] = {}
    request = service.playlists().list(
        mine=True, part="snippet", maxResults=RESULT_COUNT
    )
    pages = 0
    while request is not None and pages < MAX_UPLOADS_PAGES:
        response = request.execute()
        for item in response.get("items", []):
            out[item["id"]] = item["snippet"]["title"]
        request = service.playlists().list_next(request, response)
        pages += 1
    return out


def _playlist_items(service: Any, playlist_id: str) -> list[dict[str, str]]:
    """[{item_id, video_id, title}] for a playlist (all pages)."""
    out: list[dict[str, str]] = []
    request = service.playlistItems().list(
        playlistId=playlist_id, part="snippet", maxResults=RESULT_COUNT
    )
    pages = 0
    while request is not None and pages < MAX_UPLOADS_PAGES:
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            out.append(
                {
                    "item_id": item["id"],
                    "video_id": snippet["resourceId"].get("videoId", ""),
                    "title": snippet["title"],
                }
            )
        request = service.playlistItems().list_next(request, response)
        pages += 1
    return out


def _ensure_playlist(service: Any, name: str, cache: dict[str, str]) -> str:
    if name in cache:
        return cache[name]
    response = (
        service.playlists()
        .insert(
            part="snippet,status",
            body={"snippet": {"title": name}, "status": {"privacyStatus": "public"}},
        )
        .execute()
    )
    cache[name] = response["id"]
    print(f"    created playlist {name!r}")
    return response["id"]


def _drop_stale_pointers(playlists: dict[str, str], execute: bool) -> int:
    """Delete video_playlist.<type>.<year> pointers whose target playlist isn't
    that type's playlist for that year (left over from the old cross-type bug)."""
    stale = 0
    for mt in MEETING_TYPES:
        prefix = f"video_playlist.{mt.key}."
        for raw_key in list(r.scan_iter(match=f"{prefix}*")):
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            year = key.rsplit(".", 1)[-1]
            target = r.get(key)
            pid = target.decode("utf-8") if target else ""
            expected = mt.playlist_name_template.format(year)
            if playlists.get(pid) != expected:
                stale += 1
                if execute:
                    r.delete(key)
    return stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the repair (default is a read-only audit)",
    )
    args = parser.parse_args()

    service = _service()
    playlists = _all_playlists(service)
    name_to_id = {title: pid for pid, title in playlists.items()}

    moves = 0
    removals = 0
    skips: list[str] = []
    for pid, title in sorted(playlists.items(), key=lambda kv: kv[1]):
        owner = owner_type(title)
        if owner is None:
            continue  # not a pipeline-managed playlist
        for item in _playlist_items(service, pid):
            action, dest = classify_item(owner, item["title"], item["video_id"])
            if action == "keep":
                continue
            if action == "skip":
                skips.append(f"{item['video_id']}  {item['title']!r}")
                continue
            if action == "remove":
                removals += 1
                print(f"  remove placeholder from {title!r}: {item['item_id']}")
                if args.execute:
                    service.playlistItems().delete(id=item["item_id"]).execute()
                continue
            # move
            assert dest is not None
            year = get_year_string_from_string(item["title"]) or ""
            dest_name = dest.playlist_name_template.format(year)
            moves += 1
            print(
                f"  move {item['video_id']} {item['title']!r}: {title!r} -> {dest_name!r}"
            )
            if args.execute:
                dest_id = _ensure_playlist(service, dest_name, name_to_id)
                service.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": dest_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": item["video_id"],
                            },
                        }
                    },
                ).execute()
                service.playlistItems().delete(id=item["item_id"]).execute()

    stale = _drop_stale_pointers(playlists, args.execute)

    verb = "Applied" if args.execute else "Would apply"
    print(
        f"\n{verb}: move {moves} misfiled video(s), remove {removals} placeholder "
        f"item(s), drop {stale} stale video_playlist pointer(s)."
    )
    if skips:
        print(f"Left for manual deletion ({len(skips)} obsolete video(s)):")
        for s in skips:
            print(f"  {s}")
    if not args.execute:
        print("\n(read-only audit — re-run with --execute to apply.)")


if __name__ == "__main__":
    main()
