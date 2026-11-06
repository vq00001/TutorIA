import json
from unittest.mock import AsyncMock

from tests.stubs import mail
from tests.stubs.ai import chat, embed


def test_embed_stub_distinguishes_related_from_unrelated_queries():
    result = embed({"input": ["Derivada", "unrelated astronomy"]})["embeddings"]
    assert result == [[1, 0, 0], [0, 1, 0]]
    assert all(sum(v * v for v in row) > 0 for row in result)


def test_chat_stub_keeps_only_supplied_source_identity():
    result = chat(
        {
            "messages": [
                {"content": "Return citations"},
                {"content": json.dumps({"sources": [{"id": "synthetic-source"}]})},
            ]
        }
    )
    assert json.loads(result["message"]["content"])["citations"] == ["synthetic-source"]


def test_smtp_sink_keeps_messages_inside_its_process():
    lines = b"EHLO fixture\r\nMAIL FROM:<a@example.com>\r\nRCPT TO:<b@example.com>\r\nDATA\r\nTo: b@example.com\r\nSubject: synthetic\r\nContent-Type: text/plain\r\n\r\nhello\r\n.\r\nQUIT\r\n".splitlines(
        keepends=True
    )
    reader = AsyncMock()
    reader.readline.side_effect = lines + [b""]
    writer = AsyncMock()
    writer.write = lambda data: None
    writer.close = lambda: None
    mail.messages.clear()
    coroutine = mail.smtp(reader, writer)
    try:
        coroutine.send(None)
    except StopIteration:
        pass
    else:
        raise AssertionError("In-memory SMTP protocol unexpectedly waited for I/O")
    assert mail.messages == [{"to": "b@example.com", "subject": "synthetic", "text": "hello\r\n"}]
    mail.messages.clear()
