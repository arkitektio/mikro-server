from kante.channel import build_channel

from .channel_signals import FileSignal

file_channel = build_channel(FileSignal)


# Room names are built in one place so the broadcasting signals and the
# subscribing resolvers cannot drift apart. The org-wide rooms carry the
# organization id so cross-tenant events never share a room.


def org_files_room(org_id: int) -> str:
    return f"org_{org_id}_files"


def folder_files_room(folder_id: int) -> str:
    return f"folder_files_{folder_id}"
