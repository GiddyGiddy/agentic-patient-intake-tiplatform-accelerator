import asyncio
import hashlib

from aiosmtpd.controller import Controller


class LoggingHandler:
    async def handle_DATA(self, server, session, envelope):
        digest = hashlib.sha256(envelope.content).hexdigest()
        print("=== KIM message intercepted ===")
        print(f"From: {envelope.mail_from}  To: {envelope.rcpt_tos}")
        print(f"[mock] S/MIME signature (SHA-256 of content): {digest}")
        print(envelope.content.decode("utf-8", errors="replace"))
        print("===============================", flush=True)
        return "250 OK"


async def main():
    controller = Controller(LoggingHandler(), hostname="0.0.0.0", port=2525)
    controller.start()
    print("Mock KIM-CM listening on :2525", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
