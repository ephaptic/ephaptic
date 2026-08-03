// A test between the TS server and the Python client :O

import { Ephaptic, ServiceError, activeUser, emit } from "../../dist/index.js";

class NotEnoughFunds extends ServiceError {
    static code = "INSUFFICIENT_FUNDS";
    static message = "Not enough funds.";
    static statusCode = 402;
}

const ephaptic = new Ephaptic({ debug: false });

ephaptic.identityLoader(async auth => auth?.token ?? null);

ephaptic.expose("add", (a, b) => a + b);
ephaptic.expose("echo", msg => msg);
ephaptic.expose("whoami", () => activeUser());
ephaptic.expose("returns_null", () => null);

ephaptic.expose("named", (first, second) => `${first}|${second}`);

ephaptic.expose("boom", () => {
    throw new NotEnoughFunds("You are broke.", { data: { available: 3 } });
});

ephaptic.expose("kaboom", () => {
    throw new Error("secret sk-proj-abcd");
});

ephaptic.expose("secret", () => "sk-proj-1234", { requiresLogin: true });

ephaptic.expose("countdown", async function* (n) {
    for (let i = n; i > 0; i--) yield i;
});

ephaptic.expose("falsy_stream", async function* () {
    for (const v of [0, null, 1]) yield v;
});

ephaptic.expose("badstream", async function* () {
    yield 1;
    throw new NotEnoughFunds("stream broke");
});

ephaptic.expose("ping", () => {
    emit("pong", { ok: true });
    return "sent";
});

const port = Number(process.env.PARITY_PORT || 0);
const server = ephaptic.listen(port);
await new Promise(resolve => server.on("listening", resolve));
console.log(`READY ${server.address().port}`);
