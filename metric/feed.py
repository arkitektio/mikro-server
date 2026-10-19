"""mikro's change feed of metrics, served to kraph (``POST _feed``, signed with kraph's instance key).

kraph reads it one organization at a time, after its cursor, and applies each page in one
transaction with that cursor. Only the services named here may read it at all; which
organization a run is for is the reader's to keep apart.
"""

from arkitekt_service.feed.publisher import FeedPublisher

from metric import logic
from mikro_server.service import service

#: Who reads every metric: kraph (its name in the hub), where a metric is given its meaning.
READERS = ["kraph"]

publisher = FeedPublisher(service.identifier)
publisher.feed(logic.FEED, readers=READERS, render=logic.render)
