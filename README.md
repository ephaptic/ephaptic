<div align="center">
    <a href="https://github.com/ephaptic/ephaptic">
        <picture>
            <img src="https://raw.githubusercontent.com/ephaptic/ephaptic/refs/heads/main/.github/assets/logo.png" alt="ephaptic logo" height="200">
            <!-- <img src="https://avatars.githubusercontent.com/u/248199226?s=256" alt="ephaptic logo" height="200> -->
        </picture>
    </a>
<br>
<h1>ephaptic</h1>
<br>
<a href="https://github.com/ephaptic/ephaptic/blob/main/LICENSE"><img alt="GitHub License" src="https://img.shields.io/github/license/ephaptic/ephaptic?style=for-the-badge&labelColor=%23222222"></a> <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/ephaptic/ephaptic/publish-js.yml?style=for-the-badge&label=NPM%20Build%20Status&labelColor=%23222222"> <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/ephaptic/ephaptic/publish-python.yml?style=for-the-badge&label=PyPI%20Build%20Status&labelColor=%23222222">


</div>

## What is `ephaptic`?

<br>

<blockquote>
    <b>ephaptic (adj.)</b><br>
    electrical conduction of a nerve impulse across an ephapse without the mediation of a neurotransmitter.
</blockquote>

Nah, just kidding. It's an RPC framework.

> **ephaptic** — Call your backend straight from your frontend. No JSON. No latency. No middleware.

## Getting Started

- Ephaptic is designed to be invisible. Write a function on the server, call it on the client. No extra boilerplate.

- Plus, it's horizontally scalable with Redis (optional), and features extremely low latency thanks to [msgpack](https://github.com/msgpack).

- Oh, and the client can also listen to events broadcasted by the server. No, like literally. You just need to add an `eventListener`. Did I mention? Events can be sent to specific targets, specific users - not just anyone online.

What are  you waiting for? **Let's go.**

<details>
    <summary>Python (server)</summary>
    <!-- TODO: Add documentation -->
</details>

<details>
    <summary>JavaScript/TypeScript — Browser (Svelt, React, Angular, Vite, etc.)</summary>

#### To use with a framework / Vite:

```
npm install @ephaptic/client
```

Then:

```typescript
import { connect } from "@ephaptic/client";

const client = connect(); // Defaults to `/_ephaptic`.
```

Or, you can use it with a custom URL:

```typescript
const client = connect({ url: '/ws' });
```

```typescript
const client = connect({ url: 'wss://my-backend.deployment/ephaptic' });
```

#### Or, to use in your browser:

```html
<script type="module">
import { connect } from 'https://cdn.jsdelivr.net/npm/@ephaptic/client@latest/+esm';

const client = connect();
</script>
```

<!-- TODO: Add extended documentation -->

</details>

## [License](https://github.com/ephaptic/ephaptic/blob/main/LICENSE)

---

<p align="center">
    &copy; ephaptic 2025
</p>