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
import time
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from celery_app import r  # noqa: E402
from src.meeting_types import MEETING_TYPES, MeetingType  # noqa: E402
from src.processors.helpers.google_auth import load_credentials  # noqa: E402
from src.processors.upload_video import (  # noqa: E402
    CLIENT_SECRETS_FILE,
    MAX_UPLOADS_PAGES,
    RESULT_COUNT,
    RETRIABLE_STATUS_CODES,
    SCOPES,
    YOUTUBE_TOKEN_FILE,
)
from src.util import get_year_string_from_string  # noqa: E402

# Obsolete/superseded videos to leave in place (the operator deletes these):
# the General-titled 2026-05-26 copies of a meeting that is canonically Planning
# Commission (its correct PC videos are moved by this tool).
SKIP_VIDEO_IDS = {"DzhdARPh05s", "EN5TG9pFS-Q"}

# Only a genuinely dead item is safe to remove. A "Private video" is a LIVE video
# that is temporarily private — its id is hidden, so removing the playlist entry
# would be irreversible; leave those in place.
PLACEHOLDER_TITLES = {"Deleted video"}


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
    if request is not None:
        print(
            f"    WARNING: hit the {MAX_UPLOADS_PAGES}-page cap while listing "
            "playlists; the channel has more and this audit/repair may be incomplete."
        )
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
    if request is not None:
        print(
            f"    WARNING: hit the {MAX_UPLOADS_PAGES}-page cap while listing items "
            f"of playlist {playlist_id}; it has more and this may be incomplete."
        )
    return out


# A just-created playlist can briefly be not-yet-writable (the same propagation lag
# that makes its item listing 404); retry the insert a few times before giving up.
_INSERT_RETRY_ATTEMPTS = 5
_INSERT_RETRY_DELAY_SECONDS = 2.0


def _insert_video(
    service: Any, dest_id: str, video_id: str, retry_transient: bool
) -> None:
    """Insert a video into a playlist. ``retry_transient`` enables a few backoff
    retries on a 404 or a transient 5xx — set it only for a just-created playlist,
    whose propagation lag those absorb. For a pre-existing playlist it's False, so a
    404 (the playlist is genuinely gone) or any error surfaces immediately rather
    than wasting retries/quota."""
    body = {
        "snippet": {
            "playlistId": dest_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    for attempt in range(_INSERT_RETRY_ATTEMPTS):
        try:
            service.playlistItems().insert(part="snippet", body=body).execute()
            return
        except HttpError as exc:
            resp = getattr(exc, "resp", None)
            status = resp.status if resp is not None else None
            retriable = status == 404 or status in RETRIABLE_STATUS_CODES
            last = attempt == _INSERT_RETRY_ATTEMPTS - 1
            if retry_transient and retriable and not last:
                time.sleep(_INSERT_RETRY_DELAY_SECONDS)
                continue
            raise


def _ensure_playlist(
    service: Any, name: str, cache: dict[str, str]
) -> tuple[str, bool]:
    """Return (playlist_id, created). ``created`` is True when this call inserted
    the playlist — callers must NOT immediately list its items, since a freshly
    created playlist isn't queryable yet (playlistItems.list returns 404)."""
    if name in cache:
        return cache[name], False
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
    return response["id"], True


def _playlist_video_ids(service: Any, playlist_id: str) -> set[str]:
    """Current video ids in an EXISTING playlist. Callers must not pass a
    just-created playlist (its item listing 404s until it propagates) — those are
    seeded empty via the ``created`` flag from _ensure_playlist instead. A 404 here
    therefore means the playlist is genuinely gone and is allowed to propagate up so
    the move is recorded as a failure rather than silently treated as empty."""
    return {it["video_id"] for it in _playlist_items(service, playlist_id)}


def _perform_move(
    service: Any,
    item: dict[str, str],
    dest_name: str,
    name_to_id: dict[str, str],
    dest_members: dict[str, set[str]],
) -> None:
    """Insert ``item`` into ``dest_name`` (creating that playlist if needed), then
    delete it from its source. A playlist created this run is known-empty and is not
    listed; a pre-existing one is listed so a video already present is not inserted
    again (a partial-run re-run can't duplicate). ``name_to_id``/``dest_members`` are
    updated in place; API failures raise so the caller can record them."""
    dest_id, created = _ensure_playlist(service, dest_name, name_to_id)
    if dest_id not in dest_members:
        dest_members[dest_id] = (
            set() if created else _playlist_video_ids(service, dest_id)
        )
    if item["video_id"] not in dest_members[dest_id]:
        # Only tolerate a lagging insert for a playlist we just created this run.
        _insert_video(service, dest_id, item["video_id"], retry_transient=created)
        dest_members[dest_id].add(item["video_id"])
    service.playlistItems().delete(id=item["item_id"]).execute()


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
    failures: list[str] = []
    dest_members: dict[str, set[str]] = {}  # dest playlist id -> its video ids

    def _move(item: dict[str, str], src_title: str, dest: MeetingType) -> None:
        """Plan/perform moving one item into dest's correct year playlist: resolve
        the year, log the move, and (under --execute) delegate the API work to
        _perform_move, collecting any failure so one bad item doesn't abort the batch."""
        nonlocal moves
        year = get_year_string_from_string(item["title"])
        if not year:  # don't fabricate a yearless " <Type> Meetings" playlist
            failures.append(f"no parseable year, not moved: {item['title']!r}")
            return
        dest_name = dest.playlist_name_template.format(year)
        print(
            f"  move {item['video_id']} {item['title']!r}: {src_title!r} -> {dest_name!r}"
        )
        if not args.execute:
            moves += 1
            return
        try:
            _perform_move(service, item, dest_name, name_to_id, dest_members)
            moves += 1
        except Exception as exc:
            failures.append(f"move {item['video_id']} -> {dest_name!r}: {exc}")

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
                print(f"  remove placeholder from {title!r}: {item['item_id']}")
                if not args.execute:
                    removals += 1
                    continue
                try:
                    service.playlistItems().delete(id=item["item_id"]).execute()
                    removals += 1
                except Exception as exc:
                    failures.append(f"remove {item['item_id']} from {title!r}: {exc}")
                continue
            assert dest is not None
            _move(item, title, dest)

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
    if failures:
        print(f"\n{len(failures)} operation(s) FAILED (safe to re-run):")
        for f in failures:
            print(f"  {f}")
    if not args.execute:
        print("\n(read-only audit — re-run with --execute to apply.)")


if __name__ == "__main__":
    main()
