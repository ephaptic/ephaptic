/**
 * find the function's paramater list
 * like literally what it says
 */ 
function findParamList(src: string): { open: number; close: number } | null {
    const head =
        // function f(
        // function(
        // async function* f(
        /^(?:async\s+)?function\s*\*?\s*(?:[A-Za-z_$][\w$]*)?\s*\(/.exec(src) ??
        // (a, b) => ..
        /^(?:async\s+)?\(/.exec(src) ??
        // f(
        // *f(
        // async f(
        // get f(
        /^(?:async\s+)?\*?\s*(?:get\s+|set\s+)?[A-Za-z_$][\w$]*\s*\(/.exec(src);

    if (!head) return null;

    const open = head[0].length - 1;
    let depth = 0;
    for (let i = open; i < src.length; i++) {
        const c = src[i];
        if (c === "(") depth++;
        else if (c === ")") {
            depth--;
            if (depth === 0) return { open, close: i };
        }
    }
    return null;
}

/**
 * extract a function's param names. used to map `kwargs` sent over RPC by the Python client, and HTTP request fields,
 * onto JS/TS positional arguments, by name.
 *
 * degrades gracefully -- we return `[]` for anything that we can't parse (e.g. destructuring, defaults with parenthesis, etc.)
 * so, handlers that want HTTP routing or proper kwargs support should use plain old named params.
 */
export function getParamNames(fn: (...args: any[]) => any): string[] {
    try {
        let src = fn.toString();

        // strip comments
        src = src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "").trim();

        // `x => ...` -- a single param without parenthesis.
        // checked first because the fx body might have his own paranthesis
        const bare = /^(?:async\s+)?([A-Za-z_$][\w$]*)\s*=>/.exec(src);
        if (bare) return [bare[1]];

        const list = findParamList(src);
        if (!list) return [];

        const params = src.slice(list.open + 1, list.close);
        if (/[[{]/.test(params)) return []; // destructuring -> bail out with `[]`.

        const names = params
            .split(",")
            .map((p) => p.split("=")[0].replace(/\.\.\./, "").trim())
            .filter((p) => p.length > 0 && /^[A-Za-z_$][\w$]*$/.test(p));

        if (names.length < fn.length) return [];

        return names;
    } catch {
        return [];
    }
}

/**
 * coerce a string value from a path/query param into the JSON it looks like (number, boolean, null)
 * this mirrors FastAPI coercing `?n=5` to an int from the type hint -- since TS has no runtime types,
 * we infer from the value so an RPC call and the equivalent HTTP call see the
 * same argument. non-scalar-looking strings are left as-is.
 */
export function coerceScalar(value: unknown): unknown {
    if (typeof value !== "string") return value;
    if (value === "true") return true;
    if (value === "false") return false;
    if (value === "null") return null;
    if (value !== "" && /^-?\d+(\.\d+)?$/.test(value)) return Number(value);
    return value;
}

/**
 * whether a function's signature ends in a rest parameter
 */
export function hasRestParameter(fn: (...args: any[]) => any): boolean {
    try {
        const src = fn.toString().replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "").trim();

        // `x => ...` has exactly one parameter and it is not a rest parameter.
        if (/^(?:async\s+)?[A-Za-z_$][\w$]*\s*=>/.test(src)) return false;

        const list = findParamList(src);
        if (!list) return false;
        return /\.\.\./.test(src.slice(list.open + 1, list.close));
    } catch {
        return false;
    }
}