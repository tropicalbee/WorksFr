import asyncio

from app.broker import Broker
from app.events import DoneEvent, StoredEvent
from app.models import Score


def test_broker_thread_publication_and_terminal() -> None:
    async def run() -> None:
        broker = Broker()
        one, two = broker.subscribe("c1"), broker.subscribe("c1")
        event = StoredEvent(seq=1, check_id="c1", event=DoneEvent(score=Score()))
        await asyncio.to_thread(broker.publish, "c1", event)
        assert await anext(one) == event
        assert await anext(two) == event
        assert [item async for item in one] == []
        late = broker.subscribe("c1")
        assert await anext(late) == event

    asyncio.run(run())
