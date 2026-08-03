import express from "express";
import { Ephaptic, ServiceError, activeUser, emit, isHttp } from "../../dist/index.js";
import { connect, EphapticError } from "@ephaptic/client";

class NotEnoughFunds extends ServiceError {
    static code = "INSUFFICIENT_FUNDS";
    static message = "Not enough funds.";
    static statusCode = 402;
}

const ephaptic = new Ephaptic({ debug: false });

ephaptic.expose("add", (a, b) => a + b);

ephaptic.expose("echo", msg => msg);

ephaptic.expose("whoami", () => activeUser());

ephaptic.expose("boom", async () => {
    throw new NotEnoughFunds("You are broke.", { data: { available: 3 } });
});

ephaptic.expose("kaboom", async () => {
    throw new Error("pspspsp secrets secrets sk-proj-abcd");
});

ephaptic.expose("countdown", async function* (n) {
    for (let i = n; i > 0; i--) yield i;
});

ephaptic.expose("badstream", async function* () {
    yield 1;
    yield 2;
    throw new NotEnoughFunds("stream broke");
});

ephaptic.expose("whoami_stream", async function* () {
    // AsyncLocalStorage context should be present throughout the generator iteration.
    yield activeUser();
});

ephaptic.expose("ping", () => {
    emit("pong", { ok: true });
    return "sent";
});

ephaptic.expose("secret", () => "sk-proj-1234", { requiresLogin: true });

ephaptic.expose("falsy_stream", async function* () {
    for (const v of [0, null, 1]) yield v;
});

ephaptic.expose("returns_null", () => null);

const falsyIdentityServer = new Ephaptic({ debug: false });
falsyIdentityServer.identityLoader(() => 0);
falsyIdentityServer.expose("whoami0", () => activeUser(), { requiresLogin: true });

ephaptic.identityLoader(async (auth) => auth?.token ?? null);

ephaptic.httpIdentityLoader(req => {
    const auth = req.headers["authorization"];
    return auth ? auth.replace("Bearer ", "") : null;
});

const router = ephaptic.router();

router.get("/sum", (a, b) => a + b); // query params

router.get("/greet/{name}", name => `hi ${name}`); // path param

router.post("/echoObj", p => p); // single body param = whole body; like FastAPI

router.get("/whereami", () => isHttp() ? "http" : "rpc");

router.get("/secretHttp", () => activeUser(), { requiresLogin: true });

router.get("/boomHttp", () => {
    throw new NotEnoughFunds("broke", { data: { available: 0 } });
});

router.get("/countHttp", async function* (n) {
    for (let i = n; i > 0; i--) yield i;
});

router.get("/double", n => n * 2);

router.get("/customName", function functionName() { return 'OK'; });

router.get("/limited", () => "ok", { rateLimit: "1/m" });

const server = ephaptic.listen(0);
await new Promise((r) => server.on("listening", r));
const wsUrl = `ws://127.0.0.1:${server.address().port}/_ephaptic`;

const falsyServer = falsyIdentityServer.listen(0);
await new Promise((r) => falsyServer.on("listening", r));
const falsyUrl = `ws://127.0.0.1:${falsyServer.address().port}/_ephaptic`;

const httpApp = express();
httpApp.use(router.express());
const httpServer = httpApp.listen(0);
await new Promise((r) => httpServer.on("listening", r));
const httpBase = `http://127.0.0.1:${httpServer.address().port}`;

let failed = false;
function check(label, cond) {
    if (cond) {
        console.log  (`   [INFO] - ${label}`);
    } else {
        failed = true;
        console.error(`  [ERROR] - ${label}`);
    }
}

const client = connect({ url: wsUrl, auth: { token: "user-1" } });

try {
    check("add(2,3) === 5", (await client.add(2, 3)) === 5);
    check("echo round-trips", (await client.echo("hello")) === "hello");
    check("activeUser() sees the loaded identity", (await client.whoami()) === "user-1");

    let serviceErr = null;
    try { await client.boom(); } catch (e) { serviceErr = e; }
    check("ServiceError is an EphapticError", serviceErr instanceof EphapticError);
    check("ServiceError code", serviceErr?.code === "INSUFFICIENT_FUNDS");
    check("ServiceError data", serviceErr?.data?.available === 3);

    let internalErr = null;
    try { await client.kaboom(); } catch (e) { internalErr = e; }
    check("generic INTERNAL code", internalErr?.code === "INTERNAL");
    check("generic INTERNAL hides message", internalErr?.message === "Internal server error.");

    // unknown function -> NOT_FOUND
    let notFound = null;
    try { await client.nope(); } catch (e) { notFound = e; }
    check("NOT_FOUND for unknown function", notFound?.code === "NOT_FOUND");

    // VALIDATION_ERROR for missing args
    let validation = null;
    try { await client.add(1); } catch (e) { validation = e; }
    check("VALIDATION_ERROR for missing args", validation?.code === "VALIDATION_ERROR");

    // streaming
    const chunks = [];
    for await (const x of await client.countdown(3)) chunks.push(x);
    check("stream chunks are [3,2,1]", JSON.stringify(chunks) === JSON.stringify([3, 2, 1]));

    const badChunks = [];
    let midErr = null;
    try {
        for await (const x of await client.badstream()) badChunks.push(x);
    } catch (e) { midErr = e; }
    check("partial chunks before error", JSON.stringify(badChunks) === JSON.stringify([1, 2]));
    check("mid-stream error surfaced", midErr?.code === "INSUFFICIENT_FUNDS");

    // AsyncLocalStorage should work within a generator
    const idnChunks = [];
    for await (const x of await client.whoami_stream()) idnChunks.push(x);
    check("activeUser() works inside a stream", idnChunks[0] === "user-1");

    // emit() delivers an event to the client
    const pongReceived = new Promise((resolve) => client.on("pong", (data) => resolve(data)));
    await client.ping();
    const pong = await pongReceived;
    check("emit() delivers event to caller", pong?.ok === true);

    // requiresLogin
    const secret = await client.secret();
    check("requiresLogin allows authenticated caller", typeof secret === "string");

    const anon = connect({ url: wsUrl }); // no auth
    let unauthorized = null;
    try { await anon.secret(); } catch (e) { unauthorized = e; }
    check("requiresLogin rejects anonymous caller", unauthorized?.code === "UNAUTHORIZED");

    check("router handler callable over RPC", (await client.sum(2, 3)) === 5);
    check("anon arrow: RPC name derived from path", (await client.double(5)) === 10);
    check("named fn in router: RPC name from function", (await client.functionName()) === "OK");

    // http [router]
    const sumHttp = await fetch(`${httpBase}/sum?a=2&b=3`).then(r => r.json());
    check("HTTP query args mapped + coerced", sumHttp === 5);

    const greetHttp = await fetch(`${httpBase}/greet/world`).then(r => r.json());
    check("HTTP path param mapped", greetHttp === "hi world");

    const echoHttp = await fetch(`${httpBase}/echoObj`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ x: 1, y: 2 }),
    }).then(r => r.json());
    check("HTTP single body param = whole body", echoHttp?.x === 1 && echoHttp?.y === 2);

    const whereHttp = await fetch(`${httpBase}/whereami`).then((r) => r.json());
    check("isHttp() true over HTTP", whereHttp === "http");

    const secretUnauth = await fetch(`${httpBase}/secretHttp`);
    check("HTTP requiresLogin -> 401", secretUnauth.status === 401);
    check("HTTP 401 body is typed", (await secretUnauth.json()).code === "UNAUTHORIZED");

    const secretAuth = await fetch(`${httpBase}/secretHttp`, { headers: { authorization: "Bearer u9" } });
    check("HTTP identity loader sets activeUser", (await secretAuth.json()) === "u9");

    const boomHttp = await fetch(`${httpBase}/boomHttp`);
    check("HTTP ServiceError -> status_code", boomHttp.status === 402);
    check("HTTP ServiceError body is typed", (await boomHttp.json()).code === "INSUFFICIENT_FUNDS");

    const countRes = await fetch(`${httpBase}/countHttp?n=3`);
    check("HTTP stream content-type is jsonl", (countRes.headers.get("content-type") || "").includes("application/jsonl"));
    const countLines = (await countRes.text()).trim().split("\n").map(l => JSON.parse(l));
    check("HTTP generator -> JSONL", JSON.stringify(countLines) === JSON.stringify([3, 2, 1]));

    const doubleHttp = await fetch(`${httpBase}/double?n=5`).then(r => r.json());
    check("anon arrow: HTTP works (name from path)", doubleHttp === 10);

    const customHttp = await fetch(`${httpBase}/customName`).then(r => r.json());
    check("named fn in router: HTTP works", customHttp === "OK");

    const falsyChunks = [];
    for await (const v of await client.falsy_stream()) falsyChunks.push(v);
    check("falsy and null chunks survive", JSON.stringify(falsyChunks) === JSON.stringify([0, null, 1]));

    check("a null result is delivered", (await client.returns_null()) === null);

    const falsyClient = connect({ url: falsyUrl, auth: { token: "ignored" } });
    check("falsy identity is a present identity", (await falsyClient.whoami0()) === 0);
    falsyClient.disconnect();

    const auth = { authorization: "Bearer rl-user" };
    const rl1 = await fetch(`${httpBase}/limited`, { headers: auth });
    const rl2 = await fetch(`${httpBase}/limited`, { headers: auth });
    check("HTTP rate limit: first request ok", rl1.status === 200);
    check("HTTP rate limit: second request 429", rl2.status === 429);
    check("HTTP 429 has Retry-After", rl2.headers.get("retry-after") !== null);
} catch (e) {
    failed = true;
    console.error("test harness error:", e);
} finally {
    server.close();
    httpServer.close();
    falsyServer.close();
}

if (failed) {
    console.error("\nSMOKE TEST FAILED");
    process.exit(1);
} else {
    console.log("\nAll smoke tests passed.");
    process.exit(0);
}