"""SMTP sink with an HTTP inbox, internal to one disposable Docker run."""

import asyncio
import json
from email import policy
from email.parser import BytesParser

messages = []


async def smtp(reader, writer):
    writer.write(b"220 mail-stub TutorIA\r\n")
    await writer.drain()
    data = False
    buffer = []
    while line := await reader.readline():
        if data:
            if line == b".\r\n":
                parsed = BytesParser(policy=policy.default).parsebytes(b"".join(buffer))
                messages.append(
                    {
                        "to": str(parsed.get("To", "")),
                        "subject": str(parsed.get("Subject", "")),
                        "text": parsed.get_body(preferencelist=("plain",)).get_content(),
                    }
                )
                writer.write(b"250 accepted\r\n")
                data = False
                buffer = []
            else:
                buffer.append(line[1:] if line.startswith(b"..") else line)
        else:
            command = line.decode(errors="replace").strip().upper()
            if command.startswith(("EHLO", "HELO")):
                writer.write(b"250-mail-stub\r\n250 SIZE 1000000\r\n")
            elif command == "DATA":
                writer.write(b"354 End data with dot\r\n")
                data = True
            elif command == "QUIT":
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
        await writer.drain()
    writer.close()
    await writer.wait_closed()


async def http(reader, writer):
    request = await reader.readline()
    while await reader.readline() not in (b"\r\n", b""):
        pass
    path = request.split(b" ")[1]
    payload = json.dumps(
        {"status": "ok"} if path == b"/health" else {"messages": messages}
    ).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nContent-Length: "
        + str(len(payload)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + payload
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main():
    a = await asyncio.start_server(smtp, "0.0.0.0", 1025)
    b = await asyncio.start_server(http, "0.0.0.0", 8025)
    async with a, b:
        await asyncio.gather(a.serve_forever(), b.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
